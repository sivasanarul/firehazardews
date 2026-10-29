"""Raster-grid and metadata helpers for the dynamic biofuel model."""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from slim_fire.geography import PROCESSING_CRS, WORKING_RESOLUTION, ZAMBIA_EXTENT

from .config import NODATA


class BiofuelRasterError(RuntimeError):
    """Raised for unusable input rasters or grid configuration."""


@dataclass(frozen=True)
class TargetGrid:
    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def profile(self) -> dict[str, object]:
        return {
            "crs": self.crs,
            "transform": self.transform,
            "width": self.width,
            "height": self.height,
        }


def load_hazardmap_grid_config() -> tuple[dict[str, float], str, float]:
    """Return the shared Zambia extent and processing-grid configuration."""
    extent = {key: float(ZAMBIA_EXTENT[key]) for key in ("xmin", "ymin", "xmax", "ymax")}
    return extent, PROCESSING_CRS, float(WORKING_RESOLUTION)


def target_grid_from_config() -> TargetGrid:
    """Build an outward-snapped grid from the shared geographic configuration."""
    extent, crs_value, resolution = load_hazardmap_grid_config()
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", crs_value,
        extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"],
        densify_pts=21,
    )
    left = math.floor(left / resolution) * resolution
    bottom = math.floor(bottom / resolution) * resolution
    right = math.ceil(right / resolution) * resolution
    top = math.ceil(top / resolution) * resolution
    width = int(round((right - left) / resolution))
    height = int(round((top - bottom) / resolution))
    return TargetGrid(CRS.from_user_input(crs_value), Affine(resolution, 0.0, left, 0.0, -resolution, top), width, height)


def grid_from_raster(path: str | Path) -> TargetGrid:
    """Return a raster's grid, useful for focused tests and regional runs."""
    with rasterio.open(path) as source:
        if source.crs is None:
            raise BiofuelRasterError(f"Raster has no CRS: {path}")
        return TargetGrid(source.crs, source.transform, source.width, source.height)


@contextmanager
def aligned_raster(
    source: rasterio.io.DatasetReader,
    target: TargetGrid,
    resampling: Resampling,
    nodata: float = NODATA,
) -> Iterator[WarpedVRT]:
    """Expose ``source`` on exactly ``target`` without materialising it in RAM."""
    if source.crs is None:
        raise BiofuelRasterError(f"Raster has no CRS: {source.name}")
    with WarpedVRT(
        source,
        crs=target.crs,
        transform=target.transform,
        width=target.width,
        height=target.height,
        src_nodata=source.nodata,
        nodata=nodata,
        resampling=resampling,
    ) as vrt:
        yield vrt


def resample_dmp_to_target_grid(source: rasterio.io.DatasetReader, target: TargetGrid):
    """Return a context manager that bilinearly aligns continuous DMP data."""
    return aligned_raster(source, target, Resampling.bilinear)


def resample_landcover_to_target_grid(source: rasterio.io.DatasetReader, target: TargetGrid):
    """Return a context manager that nearest-neighbour aligns categorical cover."""
    return aligned_raster(source, target, Resampling.nearest)


def scale_and_offset(source: rasterio.io.DatasetReader) -> tuple[float, float]:
    """Read GDAL band scale/offset, falling back to common metadata keys."""
    tags = {key.lower(): value for key, value in source.tags(1).items()}
    scale = source.scales[0] if source.scales and source.scales[0] is not None else tags.get("scale_factor", 1.0)
    offset = source.offsets[0] if source.offsets and source.offsets[0] is not None else tags.get("add_offset", 0.0)
    return float(scale), float(offset)


def dmp_unit_multiplier(source: rasterio.io.DatasetReader, units: str | None = None) -> float:
    """Return the multiplier that standardises DMP to kg DM/ha/day."""
    if units is None:
        tags = {key.lower(): value for key, value in source.tags(1).items()}
        units = tags.get("units") or source.tags().get("units") or "kg/ha/day"
    normalised = str(units).lower().replace("dry matter", "dm").replace(" ", "")
    normalised = normalised.replace("m²", "m2").replace("ha-1", "/ha").replace("day-1", "/day")
    aliases = {
        "kg/ha/day": 1.0,
        "kgdm/ha/day": 1.0,
        "g/m2/day": 10.0,
        "gdm/m2/day": 10.0,
        "kg/m2/day": 10000.0,
        "mg/ha/day": 1000.0,
        "mgdm/ha/day": 1000.0,
    }
    if normalised not in aliases:
        raise BiofuelRasterError(
            f"Unsupported DMP units {units!r}; use kg/ha/day, g/m2/day, kg/m2/day, or Mg/ha/day"
        )
    return aliases[normalised]


def read_scaled_dmp(
    aligned: rasterio.io.DatasetReader,
    window,
    scale: float,
    offset: float,
    unit_multiplier: float,
) -> np.ma.MaskedArray:
    raw = aligned.read(1, window=window, masked=True).astype(np.float32)
    return raw * np.float32(scale * unit_multiplier) + np.float32(offset * unit_multiplier)


def output_profile(target: TargetGrid, nodata: float = NODATA) -> dict[str, object]:
    return {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 1,
        **target.profile,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "BIGTIFF": "IF_SAFER",
    }
