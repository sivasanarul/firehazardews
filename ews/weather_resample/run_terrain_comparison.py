"""Create coarse and 100 m terrain-conditioned weather/DFM comparison rasters."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from slim_fire.ews.dryfuel.spatial import NODATA, Raster  # noqa: E402
from slim_fire.ews.nelsonmodel import nfdrs4_pipeline as nelson  # noqa: E402
from slim_fire.ews.precipitation_resample import (  # noqa: E402
    DEFAULT_ASPECT_WEIGHT, DEFAULT_ELEVATION_COEFFICIENT, DEFAULT_FACTOR_MAX, DEFAULT_FACTOR_MIN,
    DEFAULT_REFERENCE_WIND_KMH, downscale_hourly_precipitation, terrain_precipitation_factor,
)
from slim_fire.ews.weather_resample.terrain_weather import (  # noqa: E402
    PRECIP_SENSITIVITY_S_PER_M,
    PRECIP_SMOOTHING_M,
    PRECIP_WEIGHT_MAX,
    PRECIP_WEIGHT_MIN,
    pixel_size_meters,
    smoothed_terrain_gradient,
    solar_position,
    solar_terrain_factor,
    terrain_adjust_weather_grid,
    upslope_precipitation_grid,
)

DEFAULT_DATA_DIR = Path("/mnt/hddarchive.nfs/slim/hazardmap/data")
DEFAULT_BBOX = (27.99, -13.01, 28.01, -12.99)
WEATHER_NAMES = (
    "temperature_c", "relative_humidity_pct", "precipitation_mm",
    "wind_kmh", "shortwave_wm2",
)
DEFAULT_ELEVATION_BIN_M = 10.0
DEFAULT_SLOPE_BIN_DEG = 2.5
DEFAULT_ASPECT_BIN_DEG = 15.0
DEFAULT_FLAT_SLOPE_DEG = 2.0
DEFAULT_GRADIENT_BIN = 0.002  # m/m; only used when upslope precipitation is on

_NFDRS_WORKER_CONTEXT: tuple[list[tuple], float, str, float] | None = None


def _init_nfdrs_worker(
    invariant: list[tuple], latitude: float, fuel_model: str, avg_annual_precip_in: float,
) -> None:
    global _NFDRS_WORKER_CONTEXT
    _NFDRS_WORKER_CONTEXT = (invariant, latitude, fuel_model, avg_annual_precip_in)


def _run_nfdrs4_state_batch(
    batch: tuple[int, np.ndarray, np.ndarray, np.ndarray, np.ndarray | None],
) -> list[tuple[int, dict[str, float]]]:
    """Run a contiguous block of terrain states in one worker and return final DFM values."""
    if _NFDRS_WORKER_CONTEXT is None:
        raise RuntimeError("NFDRS worker was not initialized")
    invariant, latitude, fuel_model, avg_annual_precip_in = _NFDRS_WORKER_CONTEXT
    start, temperatures, humidities, solar, precipitation = batch
    return [
        (
            start + offset,
            nelson.run_nfdrs4_arrays(
                invariant, temperatures[offset], humidities[offset], solar[offset],
                latitude, fuel_model, avg_annual_precip_in,
                None if precipitation is None else precipitation[offset],
            ),
        )
        for offset in range(temperatures.shape[0])
    ]


def read_gradient_with_halo(
    dem_path: Path, bbox: tuple[float, float, float, float], smoothing_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed terrain gradient for the bbox window, computed with a halo then cropped.

    The halo matters: without it the Gaussian kernel and the gradient stencil
    both see the window edge, which would put a discontinuity in the rainfall
    field at every weather-cell boundary.
    """
    with rasterio.open(dem_path) as source:
        window = from_bounds(*bbox, transform=source.transform).round_offsets().round_lengths()
        centre_lat = source.xy(window.row_off + window.height / 2, window.col_off + window.width / 2)[1]
        dx_m, dy_m = pixel_size_meters(source.transform, centre_lat)
        halo = int(np.ceil(3.0 * smoothing_m / min(dx_m, dy_m))) if smoothing_m > 0 else 1
        padded = rasterio.windows.Window(
            window.col_off - halo, window.row_off - halo, window.width + 2 * halo, window.height + 2 * halo,
        ).intersection(rasterio.windows.Window(0, 0, source.width, source.height))
        elevation = source.read(1, window=padded, masked=True).astype(np.float64).filled(np.nan)
    gradient_x, gradient_y = smoothed_terrain_gradient(elevation, dx_m, dy_m, smoothing_m)
    row0, col0 = int(window.row_off - padded.row_off), int(window.col_off - padded.col_off)
    crop = (slice(row0, row0 + int(window.height)), slice(col0, col0 + int(window.width)))
    return gradient_x[crop], gradient_y[crop]


def terrain_states(
    elevation_m: np.ndarray, slope_deg: np.ndarray, aspect_deg: np.ndarray,
    elevation_bin_m: float, slope_bin_deg: float, aspect_bin_deg: float, flat_slope_deg: float,
    extra: tuple[tuple[np.ndarray, float], ...] = (),
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[np.ndarray]]:
    """Group pixels whose terrain-corrected weather is interchangeable.

    NFDRS4 is a sequential hour-by-hour integration with no array API, so the
    only way to vectorize the raster is to shrink the number of runs. Corrected
    weather is a pure function of (elevation, slope, aspect), so pixels landing
    in the same terrain bin share one run; the bin's mean terrain is used as its
    representative. A bin width of zero groups only exactly equal terrain.

    ``extra`` adds further ``(values, bin_width)`` dimensions to the grouping key
    -- used for the upslope precipitation gradient, which makes rainfall vary
    per pixel and so can no longer be shared across a terrain bin.

    Returns ``(state_index_per_pixel, elevation, slope, aspect, extras)`` where
    the terrain arrays and each extra hold one representative value per state.
    """
    elevation = np.asarray(elevation_m, dtype=np.float64).ravel()
    slope = np.asarray(slope_deg, dtype=np.float64).ravel()
    aspect = np.mod(np.asarray(aspect_deg, dtype=np.float64).ravel(), 360.0)

    def binned(values: np.ndarray, width: float) -> np.ndarray:
        return values if width <= 0 else np.rint(values / width)

    aspect_key = np.where(slope <= flat_slope_deg, -1.0, binned(aspect, aspect_bin_deg))
    columns = [binned(elevation, elevation_bin_m), binned(slope, slope_bin_deg), aspect_key]
    extra_values = [np.asarray(values, dtype=np.float64).ravel() for values, _ in extra]
    columns.extend(binned(values, width) for values, (_, width) in zip(extra_values, extra, strict=True))
    _, inverse = np.unique(np.stack(columns, axis=1), axis=0, return_inverse=True)
    inverse = np.asarray(inverse).ravel()
    counts = np.bincount(inverse).astype(np.float64)

    def representative(values: np.ndarray) -> np.ndarray:
        return (np.bincount(inverse, weights=values) / counts).astype(np.float32)

    terrain = [representative(values) for values in (elevation, slope, aspect)]
    return (inverse, *terrain, [representative(values) for values in extra_values])


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(part) for part in value.split(","))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north") from exc
    if west >= east or south >= north:
        raise argparse.ArgumentTypeError("bbox requires west < east and south < north")
    return west, south, east, north


def read_aligned_terrain(
    dem_path: Path, slope_path: Path, aspect_path: Path, bbox: tuple[float, float, float, float],
) -> tuple[dict[str, np.ndarray], object, object]:
    """Read an LC-aligned terrain window and verify all three grids match."""
    arrays: dict[str, np.ndarray] = {}
    reference = None
    transform = None
    for name, path in (("elevation_m", dem_path), ("slope_deg", slope_path), ("aspect_deg", aspect_path)):
        with rasterio.open(path) as source:
            if source.crs is None:
                raise ValueError(f"Terrain raster has no CRS: {path}")
            if source.crs.to_epsg() != 4326:
                raise ValueError(f"Terrain comparison expects the LC-aligned EPSG:4326 grid: {path}")
            window = from_bounds(*bbox, transform=source.transform).round_offsets().round_lengths()
            window = window.intersection(rasterio.windows.Window(0, 0, source.width, source.height))
            data = source.read(1, window=window, masked=True).astype(np.float32)
            signature = (source.crs, source.transform, source.width, source.height, window)
            if reference is not None and signature != reference:
                raise ValueError("DEM, slope, and aspect must have identical grids")
            reference = signature
            transform = source.window_transform(window)
            arrays[name] = data.filled(np.nan)
    valid = np.logical_and.reduce([np.isfinite(values) for values in arrays.values()])
    if not valid.any():
        raise ValueError("Requested bbox contains no valid terrain pixels")
    arrays["valid"] = valid
    return arrays, transform, "EPSG:4326"


def fetch_weather(
    latitude: float, longitude: float, snapshot: datetime, spinup_days: int, cache_dir: Path,
) -> tuple[tuple[float, float], float, list[nelson.WeatherHour]]:
    point = (0, 0, latitude, longitude)
    start = (snapshot - timedelta(days=spinup_days)).date()
    [(_, _, cell, elevation_m, series)] = nelson.fetch_hourly_weather_batch(
        [point], start, snapshot.date(), cache_dir, nelson.OPEN_METEO_MODEL,
    )
    series = [hour for hour in series if hour.timestamp <= snapshot]
    if not series or series[-1].timestamp != snapshot:
        raise nelson.NelsonModelError(f"Weather does not contain snapshot {snapshot.isoformat()}")
    return cell, elevation_m, series


def write_layer(
    path: Path, data: np.ndarray, transform, crs: object, description: str, tags: dict[str, object],
) -> Path:
    values = np.asarray(data, dtype=np.float32)
    values = np.where(np.isfinite(values), values, NODATA).astype(np.float32)
    Raster(values, transform, str(crs), NODATA).write(path)
    with rasterio.open(path, "r+") as destination:
        destination.set_band_description(1, description)
        destination.update_tags(**{key: str(value) for key, value in tags.items()})
    return path


def constant_grid(value: float, shape: tuple[int, int], valid: np.ndarray) -> np.ndarray:
    return np.where(valid, value, np.nan).astype(np.float32)


def run_terrain_comparison(
    snapshot: datetime,
    bbox: tuple[float, float, float, float],
    dem_path: Path,
    slope_path: Path,
    aspect_path: Path,
    output_dir: Path,
    cache_dir: Path,
    spinup_days: int = 14,
    fuel_model: str = "W",
    avg_annual_precip_in: float = 30.0,
    processes: int = 1,
    batch_size: int = 256,
    elevation_bin_m: float = DEFAULT_ELEVATION_BIN_M,
    slope_bin_deg: float = DEFAULT_SLOPE_BIN_DEG,
    aspect_bin_deg: float = DEFAULT_ASPECT_BIN_DEG,
    flat_slope_deg: float = DEFAULT_FLAT_SLOPE_DEG,
    terrain_precipitation: bool = True,
    precip_elevation_coefficient: float = DEFAULT_ELEVATION_COEFFICIENT,
    precip_aspect_weight: float = DEFAULT_ASPECT_WEIGHT,
    precip_reference_wind_kmh: float = DEFAULT_REFERENCE_WIND_KMH,
    precip_factor_min: float = DEFAULT_FACTOR_MIN,
    precip_factor_max: float = DEFAULT_FACTOR_MAX,
) -> list[Path]:
    """Write original, resampled, intermediate, and terrain-conditioned TIFFs."""
    if processes < 1 or batch_size < 1:
        raise ValueError("processes and batch_size must be at least one")
    terrain, transform, crs = read_aligned_terrain(dem_path, slope_path, aspect_path, bbox)
    valid = terrain["valid"]
    rows, cols = np.where(valid)
    center_row, center_col = int(np.median(rows)), int(np.median(cols))
    center_lon, center_lat = transform * (center_col + 0.5, center_row + 0.5)
    cell, weather_elevation_m, series = fetch_weather(center_lat, center_lon, snapshot, spinup_days, cache_dir)

    # Only the snapshot hour is needed for the weather layers, so the full-grid
    # correction is done for one hour instead of the whole spin-up series.
    corrected = terrain_adjust_weather_grid(
        series[-1:], cell[0], cell[1], weather_elevation_m,
        terrain["elevation_m"], terrain["slope_deg"], terrain["aspect_deg"],
    )
    last = series[-1]
    temperature_original = constant_grid(last.temperature_c, valid.shape, valid)
    rh_original = constant_grid(last.relative_humidity_pct, valid.shape, valid)
    precipitation_original = constant_grid(last.precipitation_mm, valid.shape, valid)
    wind_original = constant_grid(last.wind_kmh, valid.shape, valid)
    solar_original = constant_grid(last.shortwave_wm2, valid.shape, valid)
    temperature_corrected = np.where(valid, corrected["temperature_c"][-1], np.nan)
    rh_corrected = np.where(valid, corrected["relative_humidity_pct"][-1], np.nan)
    solar_corrected = np.where(valid, corrected["shortwave_wm2"][-1], np.nan)
    sun_azimuth, sun_elevation = solar_position(snapshot, cell[0], cell[1])
    solar_factor = np.where(
        valid,
        solar_terrain_factor(terrain["slope_deg"], terrain["aspect_deg"], sun_azimuth, sun_elevation),
        np.nan,
    )

    baseline, _ = nelson.run_nfdrs4_point(series, cell[0], snapshot, fuel_model, avg_annual_precip_in)
    baseline_dfm = {name: constant_grid(value, valid.shape, valid) for name, value in baseline.items()}
    terrain_dfm = {name: np.full(valid.shape, np.nan, dtype=np.float32) for name in nelson.OUTPUT_COLUMNS}

    precipitation_downscaled = None
    precipitation_factor = None

    state_index, state_elevation, state_slope, state_aspect, state_extra = terrain_states(
        terrain["elevation_m"][rows, cols], terrain["slope_deg"][rows, cols], terrain["aspect_deg"][rows, cols],
        elevation_bin_m, slope_bin_deg, aspect_bin_deg, flat_slope_deg,
    )
    total = int(state_elevation.size)
    print(f"NFDRS4 runs: {total} terrain states for {len(rows)} valid pixels")
    state_weather = terrain_adjust_weather_grid(
        series, cell[0], cell[1], weather_elevation_m, state_elevation, state_slope, state_aspect,
    )
    # One contiguous (state, hour) row per NFDRS4 run keeps worker payloads small.
    state_temperature, state_rh, state_solar = (
        np.ascontiguousarray(state_weather[name].T)
        for name in ("temperature_c", "relative_humidity_pct", "shortwave_wm2")
    )
    state_precipitation = None
    if terrain_precipitation:
        state_counts = np.bincount(state_index, minlength=total).astype(np.float64)
        state_precipitation = np.ascontiguousarray(downscale_hourly_precipitation(
            series, state_elevation, state_slope, state_aspect, pixel_weights=state_counts,
            elevation_coefficient=precip_elevation_coefficient, aspect_weight=precip_aspect_weight,
            reference_wind_kmh=precip_reference_wind_kmh, factor_min=precip_factor_min,
            factor_max=precip_factor_max,
        ).T)
        state_factor = terrain_precipitation_factor(
            state_elevation, state_slope, state_aspect, series[-1].wind_azimuth_deg, series[-1].wind_kmh,
            pixel_weights=state_counts, elevation_coefficient=precip_elevation_coefficient,
            aspect_weight=precip_aspect_weight, reference_wind_kmh=precip_reference_wind_kmh,
            factor_min=precip_factor_min, factor_max=precip_factor_max,
        )
        precipitation_factor = np.full(valid.shape, np.nan, dtype=np.float32)
        precipitation_factor[rows, cols] = state_factor[state_index]
        precipitation_downscaled = np.full(valid.shape, np.nan, dtype=np.float32)
        precipitation_downscaled[rows, cols] = state_precipitation[:, -1][state_index]
    state_values = {name: np.empty(total, dtype=np.float32) for name in nelson.OUTPUT_COLUMNS}
    invariant = nelson.nfdrs_invariant_inputs(series)

    def state_batches():
        for start in range(0, total, batch_size):
            stop = min(start + batch_size, total)
            yield (
                start, state_temperature[start:stop], state_rh[start:stop], state_solar[start:stop],
                None if state_precipitation is None else state_precipitation[start:stop],
            )

    completed = 0
    with ProcessPoolExecutor(
        max_workers=processes,
        initializer=_init_nfdrs_worker,
        initargs=(invariant, cell[0], fuel_model, avg_annual_precip_in),
    ) as executor:
        for result_batch in executor.map(_run_nfdrs4_state_batch, state_batches()):
            for index, values in result_batch:
                for name, value in values.items():
                    state_values[name][index] = value
            completed += len(result_batch)
            print(f"NFDRS4 terrain states: {completed}/{total}")

    for name in nelson.OUTPUT_COLUMNS:
        terrain_dfm[name][rows, cols] = state_values[name][state_index]

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.strftime("%Y-%m-%dT%H%MZ")
    tags = {
        "snapshot_utc": snapshot.isoformat(),
        "weather_cell_latitude": cell[0],
        "weather_cell_longitude": cell[1],
        "weather_elevation_method": "Open-Meteo 90 m DEM reference for the returned series",
        "weather_elevation_m": f"{weather_elevation_m:.3f}",
        "product": "100 m terrain-conditioned comparison",
        "nfdrs_terrain_bins_m_deg_deg": f"{elevation_bin_m},{slope_bin_deg},{aspect_bin_deg}",
        "nfdrs_terrain_states": total,
        "precipitation_downscaling": (
            f"mass-conserving terrain: elevation_coefficient={precip_elevation_coefficient} "
            f"aspect_weight={precip_aspect_weight} reference_wind_kmh={precip_reference_wind_kmh} "
            f"factor_bounds=[{precip_factor_min},{precip_factor_max}]"
            if terrain_precipitation else "none: coarse cell value used for every pixel"
        ),
    }
    layers = {
        "terrain_dem_100m": (terrain["elevation_m"], "Elevation (m)"),
        "terrain_slope_100m": (terrain["slope_deg"], "Slope (degrees)"),
        "terrain_aspect_100m": (terrain["aspect_deg"], "Aspect (degrees from North)"),
        "weather_temperature_original_100m": (temperature_original, "Original coarse temperature sampled to 100 m (C)"),
        "weather_rh_original_100m": (rh_original, "Original coarse relative humidity sampled to 100 m (%)"),
        "weather_precipitation_original_100m": (precipitation_original, "Original coarse precipitation sampled to 100 m (mm)"),
        "weather_wind_original_100m": (wind_original, "Original coarse wind speed sampled to 100 m (km/h)"),
        "weather_solar_original_100m": (solar_original, "Original coarse shortwave sampled to 100 m (W/m2)"),
        "temperature_delta_100m": (temperature_corrected - temperature_original, "Terrain temperature correction (C)"),
        "temperature_corrected_100m": (temperature_corrected, "Terrain-corrected temperature (C)"),
        "rh_delta_100m": (rh_corrected - rh_original, "Terrain relative-humidity correction (percentage points)"),
        "rh_corrected_100m": (rh_corrected, "Terrain-corrected relative humidity (%)"),
        "solar_factor_100m": (solar_factor, "Slope/aspect solar factor"),
        "solar_corrected_100m": (solar_corrected, "Terrain-corrected shortwave radiation (W/m2)"),
    }
    if precipitation_downscaled is not None:
        layers["precipitation_factor_100m"] = (
            precipitation_factor, "Normalized terrain precipitation redistribution factor",
        )
        layers["weather_precipitation_downscaled_100m"] = (
            precipitation_downscaled, "Mass-conserving terrain-conditioned precipitation at snapshot hour (mm)",
        )
        layers["precipitation_delta_100m"] = (
            precipitation_downscaled - precipitation_original, "Terrain-conditioned minus original precipitation (mm)",
        )
    for name in nelson.OUTPUT_COLUMNS:
        layers[f"dfm_{name}_original_100m"] = (baseline_dfm[name], f"Original coarse-weather {name} dead fuel moisture (%)")
        layers[f"dfm_{name}_terrain_100m"] = (terrain_dfm[name], f"Terrain-conditioned {name} dead fuel moisture (%)")
        layers[f"dfm_{name}_delta_100m"] = (terrain_dfm[name] - baseline_dfm[name], f"Terrain minus original {name} dead fuel moisture (percentage points)")

    paths = [
        write_layer(output_dir / f"{name}_{stamp}.tif", data, transform, crs, description, tags)
        for name, (data, description) in layers.items()
    ]

    coarse_transform = rasterio.transform.from_bounds(*bbox, 1, 1)
    for name, value in (
        ("temperature_c", last.temperature_c),
        ("relative_humidity_pct", last.relative_humidity_pct),
        ("precipitation_mm", last.precipitation_mm),
        ("wind_kmh", last.wind_kmh),
        ("shortwave_wm2", last.shortwave_wm2),
    ):
        paths.append(write_layer(
            output_dir / f"weather_{name}_coarse_{stamp}.tif",
            np.array([[value]], dtype=np.float32), coarse_transform, crs,
            f"Original coarse weather: {name}", tags,
        ))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2025-08-01")
    parser.add_argument("--snapshot-hour", type=int, default=23)
    parser.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX, help="west,south,east,north; default is a ~2 km test window")
    parser.add_argument("--dem", type=Path, default=DEFAULT_DATA_DIR / "dem_100m.tif")
    parser.add_argument("--slope", type=Path, default=DEFAULT_DATA_DIR / "slope_100m.tif")
    parser.add_argument("--aspect", type=Path, default=DEFAULT_DATA_DIR / "aspect_100m.tif")
    parser.add_argument("--output-dir", type=Path, default=SRC_DIR / "output" / "weather_resample_comparison")
    parser.add_argument("--cache-dir", type=Path, default=SRC_DIR / ".cache" / "nelsonmodel")
    parser.add_argument("--spinup-days", type=int, default=14)
    parser.add_argument("--processes", type=int, default=1, help="NFDRS worker processes")
    parser.add_argument("--batch-size", type=int, default=256, help="Terrain states submitted per worker task")
    parser.add_argument("--elevation-bin-m", type=float, default=DEFAULT_ELEVATION_BIN_M, help="Terrain grouping width; 0 runs every distinct elevation")
    parser.add_argument("--slope-bin-deg", type=float, default=DEFAULT_SLOPE_BIN_DEG, help="Terrain grouping width; 0 runs every distinct slope")
    parser.add_argument("--aspect-bin-deg", type=float, default=DEFAULT_ASPECT_BIN_DEG, help="Terrain grouping width; 0 runs every distinct aspect")
    parser.add_argument("--flat-slope-deg", type=float, default=DEFAULT_FLAT_SLOPE_DEG, help="Slopes at or below this ignore aspect when grouping")
    parser.add_argument("--terrain-precipitation", action=argparse.BooleanOptionalAction, default=True, help="Mass-conserving elevation/wind precipitation redistribution")
    parser.add_argument("--precip-elevation-coefficient", type=float, default=DEFAULT_ELEVATION_COEFFICIENT, help="Elevation factor per metre; default 0.0005 gives +25%% at +500 m")
    parser.add_argument("--precip-aspect-weight", type=float, default=DEFAULT_ASPECT_WEIGHT)
    parser.add_argument("--precip-reference-wind-kmh", type=float, default=DEFAULT_REFERENCE_WIND_KMH)
    parser.add_argument("--precip-factor-min", type=float, default=DEFAULT_FACTOR_MIN)
    parser.add_argument("--precip-factor-max", type=float, default=DEFAULT_FACTOR_MAX)
    args = parser.parse_args()
    snapshot = datetime.combine(date.fromisoformat(args.date), datetime.min.time(), timezone.utc).replace(hour=args.snapshot_hour)
    paths = run_terrain_comparison(
        snapshot, args.bbox, args.dem, args.slope, args.aspect,
        args.output_dir, args.cache_dir, args.spinup_days,
        processes=args.processes, batch_size=args.batch_size,
        elevation_bin_m=args.elevation_bin_m, slope_bin_deg=args.slope_bin_deg,
        aspect_bin_deg=args.aspect_bin_deg, flat_slope_deg=args.flat_slope_deg,
        terrain_precipitation=args.terrain_precipitation,
        precip_elevation_coefficient=args.precip_elevation_coefficient,
        precip_aspect_weight=args.precip_aspect_weight,
        precip_reference_wind_kmh=args.precip_reference_wind_kmh,
        precip_factor_min=args.precip_factor_min, precip_factor_max=args.precip_factor_max,
    )
    print(f"Created {len(paths)} comparison rasters in {args.output_dir}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
