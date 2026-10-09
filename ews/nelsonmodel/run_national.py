"""Generate national terrain-conditioned NFDRS4 dead-fuel-moisture rasters.

This driver tiles the national LC grid into ~9 km weather cells, fetches one
Open-Meteo series per cell, and runs NFDRS4 once per distinct terrain state
inside each cell. Long runs are checkpointed so an interrupted job resumes
instead of restarting.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from slim_fire.ews.nelsonmodel import nfdrs4_pipeline as nelson  # noqa: E402
from slim_fire.ews.precipitation_resample.terrain_precipitation import (  # noqa: E402
    DEFAULT_ASPECT_WEIGHT, DEFAULT_ELEVATION_COEFFICIENT, DEFAULT_FACTOR_MAX, DEFAULT_FACTOR_MIN,
    DEFAULT_REFERENCE_WIND_KMH, downscale_hourly_precipitation,
)
from slim_fire.ews.weather_resample.terrain_weather import (  # noqa: E402
    DEFAULT_ASPECT_BIN_DEG,
    DEFAULT_ELEVATION_BIN_M,
    DEFAULT_FLAT_SLOPE_DEG,
    DEFAULT_SLOPE_BIN_DEG,
    terrain_adjust_weather_grid,
    terrain_states,
)

DEFAULT_DATA_DIR = Path("/mnt/hddarchive.nfs/slim/hazardmap/data")
DEFAULT_LANDCOVER = DEFAULT_DATA_DIR / "SLIM_LC_LandCover_2024_100m_cog.tif"
DEFAULT_CELL_PIXELS = 100  # 100 m pixels per ~9 km ECMWF IFS cell
NODATA = -9999.0


def _run_states(
    payload: tuple[list[tuple], float, str, float, int, np.ndarray, np.ndarray, np.ndarray, np.ndarray | None],
) -> tuple[int, list[dict[str, float]]]:
    invariant, latitude, fuel_model, avg_annual_precip_in, start, temperature, humidity, solar, precipitation = payload
    return start, [
        nelson.run_nfdrs4_arrays(
            invariant, temperature[index], humidity[index], solar[index],
            latitude, fuel_model, avg_annual_precip_in,
            None if precipitation is None else precipitation[index],
        )
        for index in range(temperature.shape[0])
    ]


def cell_grid(
    dem_path: Path, cell_pixels: int, decimation: int = 10,
) -> tuple[list[tuple[int, int, float, float]], object, int, int]:
    """List ~9 km cells that contain terrain, with their centre coordinates.

    The valid mask is taken from a decimated DEM read and dilated by one cell so
    that slivers missed by decimation still get weather.
    """
    with rasterio.open(dem_path) as source:
        transform, width, height = source.transform, source.width, source.height
        coarse = source.read(
            1, masked=True,
            out_shape=(1, -(-height // decimation), -(-width // decimation)),
        )
    per_cell = max(1, cell_pixels // decimation)
    rows, cols = -(-height // cell_pixels), -(-width // cell_pixels)
    has_data = np.zeros((rows, cols), dtype=bool)
    for row in range(rows):
        for col in range(cols):
            block = ~coarse.mask[row * per_cell : (row + 1) * per_cell, col * per_cell : (col + 1) * per_cell]
            has_data[row, col] = bool(block.any())
    padded = np.zeros((rows + 2, cols + 2), dtype=bool)
    padded[1:-1, 1:-1] = has_data
    dilated = np.zeros_like(has_data)
    for row_shift in (0, 1, 2):
        for col_shift in (0, 1, 2):
            dilated |= padded[row_shift : row_shift + rows, col_shift : col_shift + cols]

    cells = []
    for row, col in zip(*np.where(dilated), strict=True):
        pixel_row = min(int(row) * cell_pixels + cell_pixels // 2, height - 1)
        pixel_col = min(int(col) * cell_pixels + cell_pixels // 2, width - 1)
        longitude, latitude = transform * (pixel_col + 0.5, pixel_row + 0.5)
        cells.append((int(row), int(col), float(latitude), float(longitude)))
    return cells, transform, width, height


def fetch_with_retry(
    batch: list[tuple[int, int, float, float]], start: date, end: date,
    cache_dir: Path, model: str, attempts: int = 6,
) -> list[tuple[int, int, tuple[float, float], list[nelson.WeatherHour]]]:
    """Retry Open-Meteo with backoff, waiting out the free tier's hourly quota.

    A national run needs one location per ~9 km cell, which exceeds the free
    tier's hourly allowance, so a 429 is paced to the next hour rather than
    treated as a failure.
    """
    attempt = 0
    while True:
        try:
            return nelson.fetch_hourly_weather_batch(batch, start, end, cache_dir, model)
        except nelson.NelsonModelError as exc:
            if "429" in str(exc):
                wait = (60 - datetime.now(timezone.utc).minute) * 60.0 + 30.0
                print(f"  rate limited; waiting {wait/60:.0f} min for the next quota window", flush=True)
                time.sleep(wait)
                continue
            attempt += 1
            if attempt >= attempts:
                raise
            time.sleep(min(120.0, 10.0 * 2**attempt))


def prefetch_weather(
    batches: list[list[tuple[int, int, float, float]]], start: date, end: date,
    cache_dir: Path, model: str, workers: int,
) -> None:
    """Warm the Open-Meteo cache so the NFDRS phase never blocks on the network."""
    pending = [batch for batch in batches if not nelson.cache_path(cache_dir, batch, start, end, model).exists()]
    if not pending:
        print(f"Weather: all {len(batches)} batches already cached")
        return
    print(f"Weather: fetching {len(pending)} of {len(batches)} batches")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_with_retry, batch, start, end, cache_dir, model): index
            for index, batch in enumerate(pending)
        }
        for done, future in enumerate(as_completed(futures), start=1):
            future.result()
            if done % 10 == 0 or done == len(pending):
                print(f"  weather batch {done}/{len(pending)}", flush=True)


def read_cell_terrain(
    sources: dict[str, rasterio.DatasetReader], row: int, col: int, cell_pixels: int, width: int, height: int,
) -> tuple[Window, np.ndarray, dict[str, np.ndarray]] | None:
    window = Window(
        col * cell_pixels, row * cell_pixels,
        min(cell_pixels, width - col * cell_pixels), min(cell_pixels, height - row * cell_pixels),
    )
    arrays = {
        name: source.read(1, window=window, masked=True).astype(np.float64).filled(np.nan)
        for name, source in sources.items()
    }
    valid = np.logical_and.reduce([np.isfinite(values) for values in arrays.values()])
    if not valid.any():
        return None
    return window, valid, arrays


def run_national(
    snapshot: datetime, dem_path: Path, slope_path: Path, aspect_path: Path,
    output_dir: Path, cache_dir: Path, checkpoint_path: Path,
    spinup_days: int, fuel_model: str, avg_annual_precip_in: float,
    processes: int, cell_pixels: int, weather_batch: int, weather_workers: int,
    elevation_bin_m: float, slope_bin_deg: float, aspect_bin_deg: float, flat_slope_deg: float,
    checkpoint_minutes: float, limit_cells: int | None, weather_model: str, weather_only: bool = False,
    cached_only: bool = False, terrain_precipitation: bool = True,
    precip_elevation_coefficient: float = DEFAULT_ELEVATION_COEFFICIENT,
    precip_aspect_weight: float = DEFAULT_ASPECT_WEIGHT,
    precip_reference_wind_kmh: float = DEFAULT_REFERENCE_WIND_KMH,
    precip_factor_min: float = DEFAULT_FACTOR_MIN, precip_factor_max: float = DEFAULT_FACTOR_MAX,
) -> list[Path]:
    cells, transform, width, height = cell_grid(dem_path, cell_pixels)
    if limit_cells:
        cells = cells[:limit_cells]
    batches = [cells[index : index + weather_batch] for index in range(0, len(cells), weather_batch)]
    start_date, end_date = (snapshot - timedelta(days=spinup_days)).date(), snapshot.date()
    if cached_only:
        all_batches = batches
        batches = [
            batch for batch in all_batches
            if nelson.cache_path(cache_dir, batch, start_date, end_date, weather_model).exists()
        ]
        if not batches:
            raise FileNotFoundError("No exact national weather batches are present in the cache")
        print(
            f"Cache-only: {len(batches)}/{len(all_batches)} batches, "
            f"{sum(len(batch) for batch in batches)}/{len(cells)} weather cells"
        )
    print(f"LC grid {width}x{height} -> {sum(len(batch) for batch in batches)} weather cells in {len(batches)} batches")
    if not cached_only:
        prefetch_weather(batches, start_date, end_date, cache_dir, weather_model, weather_workers)
    if weather_only:
        return []

    names = list(nelson.OUTPUT_COLUMNS)
    terrain_dfm = {name: np.full((height, width), np.nan, dtype=np.float32) for name in names}
    terrain_precipitation_snapshot = (
        np.full((height, width), np.nan, dtype=np.float32) if terrain_precipitation else None
    )
    baseline_cell = {name: np.full((-(-height // cell_pixels), -(-width // cell_pixels)), np.nan, dtype=np.float32) for name in names}
    done = np.zeros(len(batches), dtype=bool)
    if checkpoint_path.exists():
        saved = np.load(checkpoint_path)
        compatible = saved["done"].shape == done.shape and (
            not terrain_precipitation or "precipitation_snapshot" in saved.files
        )
        if compatible:
            for name in names:
                terrain_dfm[name] = saved[f"dfm_{name}"]
                baseline_cell[name] = saved[f"base_{name}"]
            if terrain_precipitation_snapshot is not None:
                terrain_precipitation_snapshot = saved["precipitation_snapshot"]
            done = saved["done"]
            print(f"Resumed from {checkpoint_path}: {int(done.sum())}/{len(batches)} batches already complete")

    sources = {
        "elevation_m": rasterio.open(dem_path), "slope_deg": rasterio.open(slope_path),
        "aspect_deg": rasterio.open(aspect_path),
    }
    started = time.perf_counter()
    last_checkpoint = started
    runs_total = 0
    completed = 0
    try:
        with ProcessPoolExecutor(max_workers=processes) as executor:
            for batch_index, batch in enumerate(batches):
                if done[batch_index]:
                    continue
                weather = fetch_with_retry(batch, start_date, end_date, cache_dir, weather_model)
                for (row, col, cell, weather_elevation_m, series) in weather:
                    series = [hour for hour in series if hour.timestamp <= snapshot]
                    if not series or series[-1].timestamp != snapshot:
                        raise nelson.NelsonModelError(f"Weather for cell {row},{col} does not reach {snapshot.isoformat()}")
                    read = read_cell_terrain(sources, row, col, cell_pixels, width, height)
                    if read is None:
                        continue
                    window, valid, terrain = read
                    pixel_rows, pixel_cols = np.where(valid)
                    latitude, longitude = cell
                    state_index, rep_elevation, rep_slope, rep_aspect, rep_extra = terrain_states(
                        terrain["elevation_m"][valid], terrain["slope_deg"][valid], terrain["aspect_deg"][valid],
                        elevation_bin_m, slope_bin_deg, aspect_bin_deg, flat_slope_deg,
                    )
                    corrected = terrain_adjust_weather_grid(
                        series, latitude, longitude, weather_elevation_m, rep_elevation, rep_slope, rep_aspect,
                    )
                    # Row 0 is the uncorrected coarse series, so the per-cell baseline
                    # comes out of the same parallel sweep as the terrain states.
                    stacked = {
                        key: np.vstack([
                            np.array([getattr(hour, key) for hour in series], dtype=np.float32)[None, :],
                            np.ascontiguousarray(corrected[key].T),
                        ])
                        for key in ("temperature_c", "relative_humidity_pct", "shortwave_wm2")
                    }
                    invariant = nelson.nfdrs_invariant_inputs(series)
                    stacked_precipitation = None
                    if terrain_precipitation:
                        # Row 0 remains coarse so the baseline is unaffected by redistribution.
                        state_counts = np.bincount(state_index, minlength=rep_elevation.size).astype(np.float64)
                        state_precipitation = downscale_hourly_precipitation(
                            series, rep_elevation, rep_slope, rep_aspect, pixel_weights=state_counts,
                            elevation_coefficient=precip_elevation_coefficient, aspect_weight=precip_aspect_weight,
                            reference_wind_kmh=precip_reference_wind_kmh, factor_min=precip_factor_min,
                            factor_max=precip_factor_max,
                        )
                        stacked_precipitation = np.vstack([
                            np.array([hour.precipitation_mm for hour in series], dtype=np.float32)[None, :],
                            np.ascontiguousarray(state_precipitation.T),
                        ])
                    total = stacked["temperature_c"].shape[0]
                    chunk = max(1, -(-total // processes))
                    payloads = [
                        (
                            invariant, latitude, fuel_model, avg_annual_precip_in, offset,
                            stacked["temperature_c"][offset : offset + chunk],
                            stacked["relative_humidity_pct"][offset : offset + chunk],
                            stacked["shortwave_wm2"][offset : offset + chunk],
                            None if stacked_precipitation is None else stacked_precipitation[offset : offset + chunk],
                        )
                        for offset in range(0, total, chunk)
                    ]
                    results: list[dict[str, float] | None] = [None] * total
                    for offset, values in executor.map(_run_states, payloads):
                        results[offset : offset + len(values)] = values
                    runs_total += total
                    for name in names:
                        baseline_cell[name][row, col] = results[0][name]
                        per_state = np.array([results[1 + index][name] for index in range(total - 1)], dtype=np.float32)
                        block = terrain_dfm[name][
                            window.row_off : window.row_off + window.height,
                            window.col_off : window.col_off + window.width,
                        ]
                        block[pixel_rows, pixel_cols] = per_state[state_index]
                    if terrain_precipitation_snapshot is not None and state_precipitation is not None:
                        precipitation_block = terrain_precipitation_snapshot[
                            window.row_off : window.row_off + window.height,
                            window.col_off : window.col_off + window.width,
                        ]
                        precipitation_block[pixel_rows, pixel_cols] = state_precipitation[-1][state_index]
                done[batch_index] = True
                completed += 1
                elapsed = time.perf_counter() - started
                remaining = (int((~done).sum())) * elapsed / completed
                print(
                    f"batch {int(done.sum())}/{len(batches)}  runs={runs_total:,}  "
                    f"elapsed={elapsed/3600:.2f} h  eta={remaining/3600:.2f} h",
                    flush=True,
                )
                if checkpoint_minutes > 0 and time.perf_counter() - last_checkpoint > checkpoint_minutes * 60:
                    save_checkpoint(checkpoint_path, terrain_dfm, baseline_cell, done, terrain_precipitation_snapshot)
                    last_checkpoint = time.perf_counter()
                    print(f"  checkpoint written to {checkpoint_path}", flush=True)
    finally:
        for source in sources.values():
            source.close()

    save_checkpoint(checkpoint_path, terrain_dfm, baseline_cell, done, terrain_precipitation_snapshot)
    return write_outputs(
        output_dir, snapshot, transform, terrain_dfm, baseline_cell, cell_pixels, terrain_precipitation_snapshot,
        {"elevation_bin_m": elevation_bin_m, "slope_bin_deg": slope_bin_deg,
         "aspect_bin_deg": aspect_bin_deg, "nfdrs_runs": runs_total,
         "precipitation_downscaling": (
             f"mass-conserving terrain: elevation_coefficient={precip_elevation_coefficient} "
             f"aspect_weight={precip_aspect_weight} reference_wind_kmh={precip_reference_wind_kmh} "
             f"factor_bounds=[{precip_factor_min},{precip_factor_max}]"
             if terrain_precipitation else "none: coarse cell value used for every pixel"
         )},
    )


def save_checkpoint(
    path: Path, terrain_dfm: dict[str, np.ndarray], baseline_cell: dict[str, np.ndarray], done: np.ndarray,
    precipitation_snapshot: np.ndarray | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.npz")
    arrays = (
        {f"dfm_{name}": values for name, values in terrain_dfm.items()}
        | {f"base_{name}": values for name, values in baseline_cell.items()}
    )
    if precipitation_snapshot is not None:
        arrays["precipitation_snapshot"] = precipitation_snapshot
    np.savez(temporary, done=done, **arrays)
    temporary.replace(path)


def write_outputs(
    output_dir: Path, snapshot: datetime, transform, terrain_dfm: dict[str, np.ndarray],
    baseline_cell: dict[str, np.ndarray], cell_pixels: int, precipitation_snapshot: np.ndarray | None,
    tags: dict[str, object],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.strftime("%Y-%m-%dT%H%MZ")
    string_tags = {key: str(value) for key, value in tags.items()} | {"snapshot_utc": snapshot.isoformat()}
    paths = []
    for name, values in terrain_dfm.items():
        path = output_dir / f"dfm_{name}_terrain_100m_{stamp}.tif"
        data = np.where(np.isfinite(values), values, NODATA).astype(np.float32)
        with rasterio.open(
            path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
            dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
            tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=3, BIGTIFF="YES",
        ) as destination:
            destination.write(data, 1)
            destination.set_band_description(1, f"Terrain-conditioned {name} dead fuel moisture (%)")
            destination.update_tags(**string_tags)
        paths.append(path)
    if precipitation_snapshot is not None:
        path = output_dir / f"precipitation_downscaled_100m_{stamp}.tif"
        data = np.where(np.isfinite(precipitation_snapshot), precipitation_snapshot, NODATA).astype(np.float32)
        with rasterio.open(
            path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
            dtype="float32", crs="EPSG:4326", transform=transform, nodata=NODATA,
            tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=3, BIGTIFF="YES",
        ) as destination:
            destination.write(data, 1)
            destination.set_band_description(1, "Mass-conserving terrain-conditioned precipitation (mm)")
            destination.update_tags(**string_tags)
        paths.append(path)
    coarse_transform = transform * rasterio.Affine.scale(cell_pixels, cell_pixels)
    for name, values in baseline_cell.items():
        path = output_dir / f"dfm_{name}_original_weathercell_{stamp}.tif"
        data = np.where(np.isfinite(values), values, NODATA).astype(np.float32)
        with rasterio.open(
            path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
            dtype="float32", crs="EPSG:4326", transform=coarse_transform, nodata=NODATA,
        ) as destination:
            destination.write(data, 1)
            destination.set_band_description(1, f"Coarse-weather {name} dead fuel moisture (%)")
            destination.update_tags(**string_tags)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2025-08-01")
    parser.add_argument("--snapshot-hour", type=int, default=23)
    parser.add_argument("--dem", type=Path, default=DEFAULT_DATA_DIR / "dem_100m.tif")
    parser.add_argument("--slope", type=Path, default=DEFAULT_DATA_DIR / "slope_100m.tif")
    parser.add_argument("--aspect", type=Path, default=DEFAULT_DATA_DIR / "aspect_100m.tif")
    parser.add_argument("--output-dir", type=Path, default=SRC_DIR / "output" / "nelsonmodel_national")
    parser.add_argument("--cache-dir", type=Path, default=SRC_DIR / ".cache" / "nelsonmodel")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Defaults to <output-dir>/checkpoint.npz")
    parser.add_argument("--spinup-days", type=int, default=14)
    parser.add_argument("--fuel-model", default="W")
    parser.add_argument("--average-annual-precipitation-in", type=float, default=30.0)
    parser.add_argument("--processes", type=int, default=8)
    parser.add_argument("--cell-pixels", type=int, default=DEFAULT_CELL_PIXELS, help="100 m pixels per weather cell")
    parser.add_argument("--weather-batch", type=int, default=64, help="Cells per Open-Meteo request")
    parser.add_argument("--weather-workers", type=int, default=2, help="Concurrent Open-Meteo requests")
    parser.add_argument("--weather-model", default=nelson.OPEN_METEO_MODEL)
    parser.add_argument("--elevation-bin-m", type=float, default=DEFAULT_ELEVATION_BIN_M)
    parser.add_argument("--slope-bin-deg", type=float, default=DEFAULT_SLOPE_BIN_DEG)
    parser.add_argument("--aspect-bin-deg", type=float, default=DEFAULT_ASPECT_BIN_DEG)
    parser.add_argument("--flat-slope-deg", type=float, default=DEFAULT_FLAT_SLOPE_DEG)
    parser.add_argument("--checkpoint-minutes", type=float, default=20.0, help="0 disables mid-run checkpoints")
    parser.add_argument("--limit-cells", type=int, default=None, help="Smoke-test on the first N cells")
    parser.add_argument("--weather-only", action="store_true", help="Download and cache weather, then stop")
    parser.add_argument("--cached-only", action="store_true", help="Run only exact cached weather batches; never fetch missing batches")
    parser.add_argument("--terrain-precipitation", action=argparse.BooleanOptionalAction, default=True, help="Mass-conserving elevation/wind precipitation redistribution")
    parser.add_argument("--precip-elevation-coefficient", type=float, default=DEFAULT_ELEVATION_COEFFICIENT)
    parser.add_argument("--precip-aspect-weight", type=float, default=DEFAULT_ASPECT_WEIGHT)
    parser.add_argument("--precip-reference-wind-kmh", type=float, default=DEFAULT_REFERENCE_WIND_KMH)
    parser.add_argument("--precip-factor-min", type=float, default=DEFAULT_FACTOR_MIN)
    parser.add_argument("--precip-factor-max", type=float, default=DEFAULT_FACTOR_MAX)
    args = parser.parse_args()

    snapshot = datetime.combine(
        date.fromisoformat(args.date), datetime.min.time(), timezone.utc,
    ).replace(hour=args.snapshot_hour)
    checkpoint = args.checkpoint or args.output_dir / "checkpoint.npz"
    paths = run_national(
        snapshot, args.dem, args.slope, args.aspect, args.output_dir, args.cache_dir, checkpoint,
        args.spinup_days, args.fuel_model, args.average_annual_precipitation_in,
        args.processes, args.cell_pixels, args.weather_batch, args.weather_workers,
        args.elevation_bin_m, args.slope_bin_deg, args.aspect_bin_deg, args.flat_slope_deg,
        args.checkpoint_minutes, args.limit_cells, args.weather_model, args.weather_only, args.cached_only,
        args.terrain_precipitation, args.precip_elevation_coefficient, args.precip_aspect_weight,
        args.precip_reference_wind_kmh, args.precip_factor_min, args.precip_factor_max,
    )
    if args.weather_only:
        print("Weather cached; stopping before the NFDRS4 phase")
        return
    print(f"Wrote {len(paths)} rasters to {args.output_dir}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
