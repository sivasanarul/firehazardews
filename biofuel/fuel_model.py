"""Vectorised, windowed dynamic total dry-fuel-load model.

This estimates surface/fire-carrying dry fuel. It is not an above-ground
biomass, carbon-stock, or fuel-moisture product.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling

from .config import (
    DEFAULT_DELTA_DAYS,
    DEFAULT_SPINUP_CYCLES,
    FUEL_PARAMETERS,
    LANDCOVER_CLASS_IDS,
    NODATA,
    OUTPUT_DESCRIPTION,
    FuelClassParameters,
    validate_configuration,
)
from .raster_utils import (
    TargetGrid,
    aligned_raster,
    dmp_unit_multiplier,
    output_profile,
    read_scaled_dmp,
    resample_dmp_to_target_grid,
    resample_landcover_to_target_grid,
    scale_and_offset,
    target_grid_from_config,
)

LOGGER = logging.getLogger(__name__)
DATE_PATTERN = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def _plain_and_valid(values: np.ndarray | np.ma.MaskedArray, nodata: float = NODATA) -> tuple[np.ndarray, np.ndarray]:
    data = np.asarray(np.ma.getdata(values))
    valid = ~np.ma.getmaskarray(values) & np.isfinite(data) & (data != nodata)
    return data, valid


def apply_landcover_parameters(
    landcover: np.ndarray | np.ma.MaskedArray,
    class_ids: dict[str, int] = LANDCOVER_CLASS_IDS,
    parameters: dict[str, FuelClassParameters] = FUEL_PARAMETERS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Create allocation, loss and capacity arrays with vectorised class masks."""
    validate_configuration(class_ids, parameters)
    values, source_valid = _plain_and_valid(landcover)
    allocation = np.zeros(values.shape, dtype=np.float32)
    loss = np.zeros(values.shape, dtype=np.float32)
    fmax = np.zeros(values.shape, dtype=np.float32)
    recognised = np.zeros(values.shape, dtype=bool)
    # Iteration is over the eight classes, never over pixels.
    for name, class_id in class_ids.items():
        mask = source_valid & (values == class_id)
        calibration = parameters[name]
        allocation[mask] = calibration.allocation
        loss[mask] = calibration.loss_10day
        fmax[mask] = calibration.fmax_mg_ha
        recognised |= mask
    return allocation, loss, fmax, recognised


def calculate_fuel_input(dmp_kg_ha_day: np.ndarray, allocation: np.ndarray, delta_days: float) -> np.ndarray:
    """Convert DMP to the allocated potential fuel input in Mg/ha."""
    if delta_days <= 0:
        raise ValueError("delta_days must be positive")
    return allocation * np.asarray(dmp_kg_ha_day, dtype=np.float32) * np.float32(delta_days / 1000.0)


def update_fuel_load(
    previous_fuel: np.ndarray | np.ma.MaskedArray,
    dmp_kg_ha_day: np.ndarray | np.ma.MaskedArray,
    landcover: np.ndarray | np.ma.MaskedArray,
    delta_days: float = DEFAULT_DELTA_DAYS,
    class_ids: dict[str, int] = LANDCOVER_CLASS_IDS,
    parameters: dict[str, FuelClassParameters] = FUEL_PARAMETERS,
    nodata: float = NODATA,
) -> np.ndarray:
    """Advance one timestep; all pixel calculations are NumPy-vectorised."""
    previous, previous_valid = _plain_and_valid(previous_fuel, nodata)
    dmp, dmp_valid = _plain_and_valid(dmp_kg_ha_day, nodata)
    if previous.shape != dmp.shape or previous.shape != np.shape(landcover):
        raise ValueError("previous_fuel, dmp and landcover must have identical shapes")
    allocation, loss_10day, fmax, cover_valid = apply_landcover_parameters(landcover, class_ids, parameters)
    valid = previous_valid & dmp_valid & cover_valid
    output = np.full(previous.shape, nodata, dtype=np.float32)

    # A fractional timestep applies proportional loss while preserving the
    # specified equation exactly at the normal ten-day timestep.
    loss = 1.0 - np.power(1.0 - loss_10day, np.float32(delta_days / 10.0))
    combustible = valid & (fmax > 0.0)
    capacity = np.zeros(previous.shape, dtype=np.float32)
    np.divide(previous, fmax, out=capacity, where=combustible)
    capacity = np.maximum(0.0, 1.0 - capacity)
    fuel_input = calculate_fuel_input(dmp, allocation, delta_days)
    calculated = previous * (1.0 - loss) + fuel_input * capacity
    calculated = np.maximum(calculated, 0.0)
    np.minimum(calculated, fmax, out=calculated)
    output[combustible] = calculated[combustible]
    output[valid & (fmax == 0.0)] = 0.0  # Built-up and water.
    return output


def apply_disturbance_reset(fuel: np.ndarray, landcover: np.ndarray | None = None) -> np.ndarray:
    """V1 extension point for future periodic-herbaceous disturbance logic."""
    return fuel


def apply_fire_reset(
    fuel: np.ndarray,
    burned_mask: np.ndarray | None,
    consumption_fraction: float | np.ndarray,
) -> np.ndarray:
    """Reduce, rather than automatically zero, fuel in burned pixels."""
    if burned_mask is None:
        return fuel
    fraction = np.asarray(consumption_fraction, dtype=np.float32)
    if np.any((fraction < 0.0) | (fraction > 1.0)):
        raise ValueError("consumption_fraction must be in [0, 1]")
    result = np.array(fuel, dtype=np.float32, copy=True)
    mask = np.asarray(burned_mask, dtype=bool)
    result[mask] *= 1.0 - fraction if fraction.ndim == 0 else 1.0 - fraction[mask]
    return result


def date_from_dmp_path(path: str | Path) -> date:
    match = DATE_PATTERN.search(Path(path).stem)
    if match is None:
        raise ValueError(f"DMP filename must contain YYYYMMDD: {path}")
    return date.fromisoformat(f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}")


@dataclass
class TimestepDiagnostics:
    dmp_sum: float = 0.0
    dmp_count: int = 0
    fuel_sum: float = 0.0
    fuel_count: int = 0
    fuel_max: float = float("-inf")
    histogram: np.ndarray = field(default_factory=lambda: np.zeros(8001, dtype=np.int64))
    class_sums: dict[str, float] = field(default_factory=dict)
    class_counts: dict[str, int] = field(default_factory=dict)

    def add(self, dmp: np.ndarray, fuel: np.ndarray, landcover: np.ndarray, class_ids: dict[str, int]) -> None:
        valid_dmp = dmp[np.isfinite(dmp) & (dmp != NODATA)]
        valid_fuel = fuel[np.isfinite(fuel) & (fuel != NODATA)]
        self.dmp_sum += float(valid_dmp.sum(dtype=np.float64))
        self.dmp_count += valid_dmp.size
        self.fuel_sum += float(valid_fuel.sum(dtype=np.float64))
        self.fuel_count += valid_fuel.size
        if valid_fuel.size:
            self.fuel_max = max(self.fuel_max, float(valid_fuel.max()))
            self.histogram += np.histogram(valid_fuel, bins=8001, range=(0.0, 8.001))[0]
        for name, class_id in class_ids.items():
            values = fuel[(landcover == class_id) & np.isfinite(fuel) & (fuel != NODATA)]
            self.class_sums[name] = self.class_sums.get(name, 0.0) + float(values.sum(dtype=np.float64))
            self.class_counts[name] = self.class_counts.get(name, 0) + values.size

    def as_dict(self) -> dict[str, object]:
        cumulative = np.cumsum(self.histogram)
        median_bin = int(np.searchsorted(cumulative, (self.fuel_count + 1) // 2)) if self.fuel_count else 0
        return {
            "mean_dmp_kg_ha_day": self.dmp_sum / self.dmp_count if self.dmp_count else float("nan"),
            "mean_fuel_mg_ha": self.fuel_sum / self.fuel_count if self.fuel_count else float("nan"),
            "median_fuel_mg_ha": (median_bin + 0.5) * 0.001 if self.fuel_count else float("nan"),
            "max_fuel_mg_ha": self.fuel_max if self.fuel_count else float("nan"),
            "mean_fuel_by_class": {
                name: self.class_sums.get(name, 0.0) / count
                for name, count in self.class_counts.items() if count
            },
        }


def _write_one_timestep(
    dmp_path: Path,
    landcover_path: Path,
    previous_path: Path | None,
    output_path: Path,
    target: TargetGrid,
    observation_date: date,
    delta_days: float,
    dmp_units: str | None,
    class_ids: dict[str, int],
    parameters: dict[str, FuelClassParameters],
    kgm2_path: Path | None,
) -> dict[str, object]:
    profile = output_profile(target)
    with rasterio.open(dmp_path) as dmp_source, rasterio.open(landcover_path) as lc_source:
        scale, offset = scale_and_offset(dmp_source)
        multiplier = dmp_unit_multiplier(dmp_source, dmp_units)
        with resample_dmp_to_target_grid(dmp_source, target) as dmp_vrt, resample_landcover_to_target_grid(lc_source, target) as lc_vrt:
            previous_source = rasterio.open(previous_path) if previous_path is not None else None
            previous_context = aligned_raster(previous_source, target, Resampling.bilinear) if previous_source else None
            previous_vrt = previous_context.__enter__() if previous_context else None
            diagnostics = TimestepDiagnostics()
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(output_path, "w", **profile) as output:
                    kgm2 = rasterio.open(kgm2_path, "w", **profile) if kgm2_path is not None else None
                    try:
                        for _, window in output.block_windows(1):
                            dmp_masked = read_scaled_dmp(dmp_vrt, window, scale, offset, multiplier)
                            landcover = lc_vrt.read(1, window=window, masked=True)
                            previous = (
                                previous_vrt.read(1, window=window, masked=True)
                                if previous_vrt is not None
                                else np.zeros(dmp_masked.shape, dtype=np.float32)
                            )
                            fuel = update_fuel_load(
                                previous, dmp_masked, landcover, delta_days,
                                class_ids=class_ids, parameters=parameters,
                            )
                            fuel = apply_disturbance_reset(fuel, np.asarray(landcover))
                            output.write(fuel, 1, window=window)
                            if kgm2 is not None:
                                converted = np.where(fuel == NODATA, NODATA, fuel * 0.1).astype(np.float32)
                                kgm2.write(converted, 1, window=window)
                            diagnostics.add(np.asarray(dmp_masked.filled(NODATA)), fuel, np.asarray(landcover), class_ids)
                        common_tags = {
                            "date": observation_date.isoformat(),
                            "timestep_days": str(delta_days),
                            "description": OUTPUT_DESCRIPTION,
                            "dmp_resampling": "bilinear",
                            "landcover_resampling": "nearest",
                        }
                        output.update_tags(units="Mg dry matter / ha", **common_tags)
                        output.set_band_description(1, "total dry fuel load")
                        if kgm2 is not None:
                            kgm2.update_tags(units="kg dry matter / m2", **common_tags)
                            kgm2.set_band_description(1, "total dry fuel load")
                    finally:
                        if kgm2 is not None:
                            kgm2.close()
            finally:
                if previous_context is not None:
                    previous_context.__exit__(None, None, None)
                if previous_source is not None:
                    previous_source.close()
    result = diagnostics.as_dict()
    LOGGER.info("fuel diagnostics date=%s %s", observation_date.isoformat(), result)
    return result


def run_fuel_model(
    dmp_paths: Sequence[str | Path] | Iterable[str | Path],
    landcover_path: str | Path,
    output_dir: str | Path,
    *,
    initial_fuel_path: str | Path | None = None,
    delta_days: float = DEFAULT_DELTA_DAYS,
    target_grid: TargetGrid | None = None,
    class_ids: dict[str, int] = LANDCOVER_CLASS_IDS,
    parameters: dict[str, FuelClassParameters] = FUEL_PARAMETERS,
    dmp_units: str | None = None,
    write_kgm2: bool = False,
    spinup: bool = False,
    spinup_cycles: int = DEFAULT_SPINUP_CYCLES,
) -> list[Path]:
    """Process ordered DMP timesteps, carrying each output into the next.

    With ``spinup=True``, the supplied sequence is first replayed
    ``spinup_cycles`` times without publishing those intermediate rasters.
    """
    validate_configuration(class_ids, parameters)
    paths = sorted((Path(path) for path in dmp_paths), key=date_from_dmp_path)
    if not paths:
        raise ValueError("At least one DMP raster is required")
    if delta_days <= 0:
        raise ValueError("delta_days must be positive")
    if spinup and spinup_cycles < 1:
        raise ValueError("spinup_cycles must be at least one")
    landcover_path = Path(landcover_path)
    if not landcover_path.exists():
        raise FileNotFoundError(landcover_path)
    target = target_grid or target_grid_from_config()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    previous = Path(initial_fuel_path) if initial_fuel_path is not None else None
    if previous is not None and not previous.exists():
        raise FileNotFoundError(previous)

    if spinup:
        spinup_dir = output_dir / ".spinup"
        spinup_dir.mkdir(parents=True, exist_ok=True)
        for cycle in range(spinup_cycles):
            for index, dmp_path in enumerate(paths):
                scratch = spinup_dir / f"fuel_cycle{cycle:03d}_{index:04d}.tif"
                _write_one_timestep(
                    dmp_path, landcover_path, previous, scratch, target,
                    date_from_dmp_path(dmp_path), delta_days, dmp_units,
                    class_ids, parameters, None,
                )
                previous = scratch

    outputs: list[Path] = []
    for dmp_path in paths:
        observation_date = date_from_dmp_path(dmp_path)
        stamp = observation_date.strftime("%Y%m%d")
        output = output_dir / f"fuel_load_{stamp}.tif"
        kgm2 = output_dir / f"fuel_load_kgm2_{stamp}.tif" if write_kgm2 else None
        _write_one_timestep(
            dmp_path, landcover_path, previous, output, target,
            observation_date, delta_days, dmp_units, class_ids, parameters, kgm2,
        )
        outputs.append(output)
        previous = output
    return outputs
