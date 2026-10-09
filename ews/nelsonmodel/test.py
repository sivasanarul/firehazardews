"""Create NFDRS4 dead-fuel-moisture GeoTIFFs from Open-Meteo weather.

Weather is sampled at approximately the native 9 km ECMWF IFS historical
resolution (0.1 degrees). A finer output grid only aligns/interpolates the
meteorological result; it does not create finer meteorological information.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from rasterio.warp import Resampling

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from slim_fire.ews.dryfuel.common import BBox, Grid, Raster

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_MODEL = "ecmwf_ifs"
OPEN_METEO_VARIABLES = (
    "temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m",
    "wind_direction_10m", "shortwave_radiation", "snowfall",
)
DEFAULT_DATE = date(2025, 8, 1)
DEFAULT_GRID_SPACING = 0.1
DEFAULT_WEATHER_GRID_SPACING = 0.1
DEFAULT_SPINUP_DAYS = 14
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output" / "nelsonmodel"
DEFAULT_MAX_WORKERS = 4
DEFAULT_BATCH_SIZE = 64
OUTPUT_COLUMNS = {"1h": "dmc_1_hr", "10h": "dmc_10_hr", "100h": "dmc_100_hr"}
OUTPUT_ATTRIBUTES = {"1h": "MC1", "10h": "MC10", "100h": "MC100"}


class NelsonModelError(RuntimeError):
    """Raised when NFDRS4 moisture rasters cannot be generated."""


@dataclass(frozen=True)
class WeatherHour:
    """Metric hourly Open-Meteo weather for one returned model grid cell."""

    timestamp: datetime
    temperature_c: float
    relative_humidity_pct: float
    precipitation_mm: float
    wind_kmh: float
    wind_azimuth_deg: float
    shortwave_wm2: float
    snowfall_cm: float


def load_zambia_extent() -> dict[str, float]:
    """Load config.py without triggering hazardmap package imports."""
    config_path = SRC_DIR / "slim_fire" / "hazardmap" / "config.py"
    spec = importlib.util.spec_from_file_location("slim_fire_hazardmap_config", config_path)
    if spec is None or spec.loader is None:
        raise NelsonModelError(f"Could not load config.py from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ZAMBIA_EXTENT


def bbox_from_config() -> BBox:
    extent = load_zambia_extent()
    return BBox(float(extent["xmin"]), float(extent["ymin"]), float(extent["xmax"]), float(extent["ymax"]))


def parse_bbox(value: str | None) -> BBox:
    if value is None:
        return bbox_from_config()
    try:
        return BBox(*(float(part.strip()) for part in value.split(",")))
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox must be west,south,east,north in EPSG:4326") from exc


def parse_snapshot(snapshot: str | None, requested_date: str) -> datetime:
    """Use 23:00 UTC for legacy --date calls; otherwise require a UTC snapshot."""
    raw_value = snapshot or f"{requested_date}T23:00:00+00:00"
    try:
        result = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("snapshot must be ISO-8601, e.g. 2025-08-01T23:00:00Z") from exc
    if result.tzinfo is None:
        raise ValueError("snapshot must include a timezone, for example a trailing Z")
    return result.astimezone(timezone.utc)


def iter_grid_points(grid: Grid) -> list[tuple[int, int, float, float]]:
    return [
        (row, col, float(lat), float(lon))
        for row in range(grid.height)
        for col in range(grid.width)
        for lon, lat in [grid.transform * (col + 0.5, row + 0.5)]
    ]


def chunks(values: list[tuple[int, int, float, float]], size: int) -> list[list[tuple[int, int, float, float]]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def cache_path(cache_dir: Path, points: list[tuple[int, int, float, float]], start: date, end: date, model: str) -> Path:
    key = json.dumps({
        "points": [(round(lat, 5), round(lon, 5)) for _, _, lat, lon in points],
        "start": start.isoformat(), "end": end.isoformat(), "model": model,
        "variables": OPEN_METEO_VARIABLES,
    }, sort_keys=True)
    return cache_dir / f"open_meteo_{hashlib.sha256(key.encode()).hexdigest()}.json"


def _payloads(payload: object, expected_count: int) -> list[dict]:
    result = payload if isinstance(payload, list) else [payload]
    if len(result) != expected_count or not all(isinstance(item, dict) for item in result):
        raise NelsonModelError("Open-Meteo returned an unexpected number of weather locations")
    return result


def _parse_weather_payload(payload: dict, latitude: float, longitude: float) -> tuple[tuple[float, float], float, list[WeatherHour]]:
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    missing = [name for name in OPEN_METEO_VARIABLES if name not in hourly]
    if not times or missing:
        detail = "no hourly data" if not times else f"missing {', '.join(missing)}"
        raise NelsonModelError(f"Open-Meteo returned {detail} at {latitude:.4f},{longitude:.4f}")
    if "elevation" not in payload:
        raise NelsonModelError(f"Open-Meteo omitted the reference elevation at {latitude:.4f},{longitude:.4f}")
    series = []
    for index, timestamp in enumerate(times):
        values = [hourly[name][index] for name in OPEN_METEO_VARIABLES]
        if any(value is None for value in values):
            raise NelsonModelError(f"Open-Meteo has a missing value at {latitude:.4f},{longitude:.4f} {timestamp}")
        series.append(WeatherHour(
            datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc), float(values[0]), float(values[1]),
            float(values[2]), float(values[3]), float(values[4]), float(values[5]), float(values[6]),
        ))
    cell = (round(float(payload.get("latitude", latitude)), 5), round(float(payload.get("longitude", longitude)), 5))
    return cell, float(payload["elevation"]), series


def fetch_hourly_weather_batch(
    points: list[tuple[int, int, float, float]], start: date, end: date, cache_dir: Path, model: str, timeout: int = 120,
) -> list[tuple[int, int, tuple[float, float], float, list[WeatherHour]]]:
    """Fetch a coordinate batch; Open-Meteo accepts comma-separated locations.

    Keeps each result tagged with its requesting (row, col) so callers can
    place it directly on the output grid without a nearest-match search. The
    returned elevation is the 90 m DEM height Open-Meteo already lapse-corrected
    temperature and humidity to, and is the only valid datum for a further
    per-pixel elevation correction.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, points, start, end, model)
    if path.exists():
        payload = json.loads(path.read_text())
    else:
        params = {
            "latitude": ",".join(f"{lat:.5f}" for _, _, lat, _ in points),
            "longitude": ",".join(f"{lon:.5f}" for _, _, _, lon in points),
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "hourly": ",".join(OPEN_METEO_VARIABLES), "models": model,
            "timezone": "GMT", "cell_selection": "nearest",
        }
        try:
            response = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NelsonModelError(f"Open-Meteo {model} request failed: {exc}") from exc
        payload = response.json()
        path.write_text(json.dumps(payload))
    return [
        (point[0], point[1], *_parse_weather_payload(item, point[2], point[3]))
        for point, item in zip(points, _payloads(payload, len(points)), strict=True)
    ]


def prepare_nfdrs_weather(series: list[WeatherHour]) -> pd.DataFrame:
    """Convert Open-Meteo metric values to the verified nfdrs4py input units."""
    if not series:
        raise NelsonModelError("Cannot prepare NFDRS4 weather from an empty series")
    return pd.DataFrame({
        "DateTime": [weather.timestamp.replace(tzinfo=None) for weather in series],
        "Temperature(F)": [weather.temperature_c * 9.0 / 5.0 + 32.0 for weather in series],
        "RelativeHumidity(%)": [weather.relative_humidity_pct for weather in series],
        "Precipitation(in)": [weather.precipitation_mm / 25.4 for weather in series],
        "WindSpeed(mph)": [weather.wind_kmh / 1.609344 for weather in series],
        "WindAzimuth(degrees)": [weather.wind_azimuth_deg for weather in series],
        "SolarRadiation(W/m2)": [weather.shortwave_wm2 for weather in series],
        "SnowFlag": [weather.snowfall_cm > 0.0 for weather in series],
    })


def run_nfdrs4_point(series: list[WeatherHour], latitude: float, snapshot: datetime, fuel_model: str, avg_annual_precip_in: float) -> tuple[dict[str, float], pd.DataFrame]:
    """Run NFDRS4 hourly to the requested snapshot and return moisture percent."""
    try:
        import nfdrs4py
    except ModuleNotFoundError as exc:
        raise NelsonModelError("nfdrs4py is required; install slim_fire/environment.yaml") from exc
    frame = prepare_nfdrs_weather([weather for weather in series if weather.timestamp <= snapshot])
    if frame.empty or frame["DateTime"].iloc[-1] != snapshot.replace(tzinfo=None):
        raise NelsonModelError(f"Open-Meteo does not provide requested snapshot {snapshot.isoformat()}")
    try:
        result = nfdrs4py.NFDRS4py(Lat=latitude, FuelModel=fuel_model, AvgAnnPrecip=avg_annual_precip_in).process_df(
            frame, datetime_col="DateTime", temp_col="Temperature(F)", rh_col="RelativeHumidity(%)",
            precip_col="Precipitation(in)", srad_col="SolarRadiation(W/m2)",
            windspeed_col="WindSpeed(mph)", snowflag_col="SnowFlag",
        )
    except Exception as exc:
        raise NelsonModelError(f"NFDRS4 initialization or processing failed at {latitude:.4f}: {exc}") from exc
    missing = [column for column in OUTPUT_COLUMNS.values() if column not in result]
    if missing:
        raise NelsonModelError(f"NFDRS4 did not return required columns: {', '.join(missing)}")
    return {name: float(result[column].iloc[-1] * 100.0) for name, column in OUTPUT_COLUMNS.items()}, result


def nfdrs_invariant_inputs(series: list[WeatherHour]) -> list[tuple[int, int, int, int, float, float, bool]]:
    """Pre-convert the NFDRS4 inputs that are identical for every downscaled pixel.

    Terrain downscaling only alters temperature, humidity and shortwave, so the
    timestamp, precipitation, wind and snow columns are converted to NFDRS4
    units once for the whole raster instead of being rebuilt per pixel.
    """
    if not series:
        raise NelsonModelError("Cannot prepare NFDRS4 weather from an empty series")
    return [
        (
            weather.timestamp.year, weather.timestamp.month, weather.timestamp.day, weather.timestamp.hour,
            weather.precipitation_mm / 25.4, weather.wind_kmh / 1.609344, weather.snowfall_cm > 0.0,
        )
        for weather in series
    ]


def run_nfdrs4_arrays(
    invariant: list[tuple[int, int, int, int, float, float, bool]],
    temperature_c, relative_humidity_pct, shortwave_wm2,
    latitude: float, fuel_model: str, avg_annual_precip_in: float,
    precipitation_mm=None,
) -> dict[str, float]:
    """Advance NFDRS4 over one pixel's hourly series and return only the final moisture.

    Equivalent to ``run_nfdrs4_point`` but takes numpy arrays and drives
    ``NFDRS4.Update`` directly, skipping the per-pixel DataFrame build,
    ``pd.to_datetime`` and result ``concat`` that dominate raster-scale runs.
    ``precipitation_mm`` overrides the invariant rainfall when precipitation has
    been downscaled per pixel; otherwise the shared coarse values are reused.
    """
    try:
        import nfdrs4py
    except ModuleNotFoundError as exc:
        raise NelsonModelError("nfdrs4py is required; install slim_fire/environment.yaml") from exc
    temperature_f = (np.asarray(temperature_c, dtype=np.float64) * 1.8 + 32.0).tolist()
    humidity = np.asarray(relative_humidity_pct, dtype=np.float64).tolist()
    solar = np.asarray(shortwave_wm2, dtype=np.float64).tolist()
    if precipitation_mm is None:
        rainfall = [hour[4] for hour in invariant]
    else:
        rainfall = (np.asarray(precipitation_mm, dtype=np.float64) / 25.4).tolist()
    if not len(invariant) == len(temperature_f) == len(humidity) == len(solar) == len(rainfall):
        raise NelsonModelError("NFDRS4 weather arrays must all have one value per hour")
    try:
        model = nfdrs4py.NFDRS4py(Lat=latitude, FuelModel=fuel_model, AvgAnnPrecip=avg_annual_precip_in)
        state = model.nfdrs4
        update = state.Update
        for (year, month, day, hour, _, wind_mph, snow), temp, rh, radiation, precipitation_in in zip(
            invariant, temperature_f, humidity, solar, rainfall, strict=True,
        ):
            update(year, month, day, hour, temp, rh * 100.0 if rh < 1.1 else rh, precipitation_in, radiation, wind_mph, snow)
    except NelsonModelError:
        raise
    except Exception as exc:
        raise NelsonModelError(f"NFDRS4 initialization or processing failed at {latitude:.4f}: {exc}") from exc
    return {name: float(getattr(state, attribute)) for name, attribute in OUTPUT_ATTRIBUTES.items()}


def compute_nelson_rasters(
    bbox: BBox, snapshot: datetime, grid_spacing: float, weather_grid_spacing: float, spinup_days: int,
    cache_dir: Path, max_workers: int, batch_size: int, weather_model: str, fuel_model: str,
    avg_annual_precip_in: float, sample_csv: Path | None = None,
) -> tuple[Grid, dict[str, np.ndarray]]:
    """Run NFDRS4 at weather-model resolution, then align only if output differs."""
    if grid_spacing <= 0 or weather_grid_spacing <= 0 or spinup_days < 1 or max_workers < 1 or batch_size < 1:
        raise ValueError("grid spacing must be positive; spinup-days, max-workers and batch-size must be at least one")
    output_grid, weather_grid = Grid(bbox, grid_spacing), Grid(bbox, weather_grid_spacing)
    points = iter_grid_points(weather_grid)
    start, end = (snapshot - timedelta(days=spinup_days)).date(), snapshot.date()
    weather_by_cell: dict[tuple[float, float], list[WeatherHour]] = {}
    cell_by_pixel: dict[tuple[int, int], tuple[float, float]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_hourly_weather_batch, batch, start, end, cache_dir, weather_model) for batch in chunks(points, batch_size)]
        for completed, future in enumerate(as_completed(futures), start=1):
            for row, col, cell, _elevation, series in future.result():
                weather_by_cell.setdefault(cell, series)
                cell_by_pixel[(row, col)] = cell
            print(f"Downloaded weather batch {completed}/{len(futures)}")

    # nfdrs4py has no batch/array API (each call advances one location's internal
    # state hour by hour), so distinct weather cells still need one call each.
    values_by_cell: dict[tuple[float, float], tuple[dict[str, float], pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_nfdrs4_point, series, cell[0], snapshot, fuel_model, avg_annual_precip_in): cell
            for cell, series in weather_by_cell.items()
        }
        for future in as_completed(futures):
            values_by_cell[futures[future]] = future.result()

    # Every output pixel already knows its resolved weather cell, so the whole
    # raster is filled with one vectorized scatter per variable (no per-pixel search).
    pixels = list(cell_by_pixel.items())
    rows = np.array([pixel[0] for pixel, _ in pixels], dtype=np.intp)
    cols = np.array([pixel[1] for pixel, _ in pixels], dtype=np.intp)
    rasters = {name: np.full((weather_grid.height, weather_grid.width), np.nan, dtype=np.float32) for name in OUTPUT_COLUMNS}
    for name in OUTPUT_COLUMNS:
        rasters[name][rows, cols] = np.array([values_by_cell[cell][0][name] for _, cell in pixels], dtype=np.float32)
    if any(np.isnan(data).any() for data in rasters.values()):
        raise NelsonModelError("NFDRS4 left one or more weather-grid cells empty")
    if sample_csv is not None:
        sample_csv.parent.mkdir(parents=True, exist_ok=True)
        frames = []
        columns = ["DateTime", "Temperature(F)", "RelativeHumidity(%)", "Precipitation(in)", "SolarRadiation(W/m2)", "WindSpeed(mph)", *OUTPUT_COLUMNS.values()]
        for (latitude, longitude), (_, frame) in values_by_cell.items():
            diagnostic = frame[columns].copy()
            diagnostic.loc[:, list(OUTPUT_COLUMNS.values())] *= 100.0
            diagnostic.insert(1, "lat", latitude)
            diagnostic.insert(2, "lon", longitude)
            frames.append(diagnostic)
        pd.concat(frames, ignore_index=True).to_csv(sample_csv, index=False)
    if weather_grid_spacing == grid_spacing:
        return output_grid, rasters
    return output_grid, {name: Raster(data, weather_grid.transform).resampled_to(output_grid, Resampling.bilinear).data for name, data in rasters.items()}


def write_rasters(grid: Grid, rasters: dict[str, np.ndarray], snapshot: datetime, output_dir: Path) -> list[Path]:
    """Write NFDRS4 dead-fuel moisture as float32 percent GeoTIFFs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.strftime("%Y-%m-%dT%H%MZ")
    paths = []
    for name, data in rasters.items():
        path = output_dir / f"nelson_{name}_{stamp}.tif"
        Raster(data.astype(np.float32), grid.transform, grid.crs).write(path)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE.isoformat(), help="Outputs 23:00 UTC unless --snapshot is supplied")
    parser.add_argument("--snapshot", help="UTC ISO-8601, e.g. 2025-08-01T23:00:00Z")
    parser.add_argument("--bbox", help="west,south,east,north EPSG:4326; defaults to config.py")
    parser.add_argument("--grid-spacing", type=float, default=DEFAULT_GRID_SPACING, help="Output GeoTIFF pixel size in degrees")
    parser.add_argument("--weather-grid-spacing", type=float, default=DEFAULT_WEATHER_GRID_SPACING, help="Weather-model sampling size in degrees")
    parser.add_argument("--spinup-days", type=int, default=DEFAULT_SPINUP_DAYS, help="NFDRS4 conditioning period")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS, help="Concurrent Open-Meteo requests")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Coordinates per Open-Meteo request")
    parser.add_argument("--weather-model", default=OPEN_METEO_MODEL, help="Open-Meteo archive model")
    parser.add_argument("--fuel-model", default="W", help="NFDRS4 initialization fuel model")
    parser.add_argument("--average-annual-precipitation-in", type=float, default=30.0, help="NFDRS4 annual precipitation")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/nelsonmodel"))
    parser.add_argument("--sample-csv", type=Path, help="Write hourly weather/NFDRS diagnostics")
    args = parser.parse_args()
    snapshot = parse_snapshot(args.snapshot, args.date)
    grid, rasters = compute_nelson_rasters(
        parse_bbox(args.bbox), snapshot, args.grid_spacing, args.weather_grid_spacing, args.spinup_days,
        args.cache_dir, args.max_workers, args.batch_size, args.weather_model, args.fuel_model,
        args.average_annual_precipitation_in, args.sample_csv,
    )
    for path in write_rasters(grid, rasters, snapshot, args.output_dir):
        print(path)
    print(f"output shape={grid.height}x{grid.width}; weather grid={args.weather_grid_spacing} degrees")


if __name__ == "__main__":
    main()