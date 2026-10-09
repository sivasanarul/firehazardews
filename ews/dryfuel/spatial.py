"""Shared types and helpers used by every dry-fuel data source module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

DEFAULT_CRS = "EPSG:4326"
NODATA = -9999.0


@dataclass(frozen=True)
class BBox:
    """A geographic bounding box in EPSG:4326 (west, south, east, north)."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("bbox requires west < east within [-180, 180]")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("bbox requires south < north within [-90, 90]")

    @property
    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    def cache_key(self) -> str:
        return sha256(f"{self.west:.5f}_{self.south:.5f}_{self.east:.5f}_{self.north:.5f}".encode()).hexdigest()[:16]


@dataclass
class Grid:
    """A north-up raster grid covering a BBox at a fixed pixel size (degrees)."""

    bbox: BBox
    pixel_size: float
    crs: str = DEFAULT_CRS

    @property
    def width(self) -> int:
        return max(1, round((self.bbox.east - self.bbox.west) / self.pixel_size))

    @property
    def height(self) -> int:
        return max(1, round((self.bbox.north - self.bbox.south) / self.pixel_size))

    @property
    def transform(self):
        return from_bounds(*self.bbox.as_tuple, self.width, self.height)


@dataclass
class Raster:
    """An in-memory single-band raster with its georeferencing."""

    data: np.ndarray
    transform: object
    crs: str = DEFAULT_CRS
    nodata: float = NODATA

    def resampled_to(self, grid: Grid, resampling: Resampling = Resampling.bilinear) -> "Raster":
        """Reproject/resample this raster onto the given target grid."""
        destination = np.full((grid.height, grid.width), self.nodata, dtype=np.float32)
        reproject(
            source=self.data.astype(np.float32),
            destination=destination,
            src_transform=self.transform,
            src_crs=self.crs,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            src_nodata=self.nodata,
            dst_nodata=self.nodata,
            resampling=resampling,
        )
        return Raster(destination, grid.transform, grid.crs, self.nodata)

    def write(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "height": self.data.shape[0],
            "width": self.data.shape[1],
            "crs": self.crs,
            "transform": self.transform,
            "nodata": self.nodata,
            "compress": "lzw",
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(self.data.astype(np.float32), 1)


def load_state(path: str | Path) -> Raster | None:
    """Load a previously persisted state raster, if one exists."""
    path = Path(path)
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        return Raster(src.read(1), src.transform, str(src.crs), src.nodata if src.nodata is not None else NODATA)


def save_state(path: str | Path, raster: Raster) -> None:
    raster.write(path)


def state_path(state_dir: str | Path, name: str, bbox: BBox) -> Path:
    return Path(state_dir) / f"{name}_{bbox.cache_key()}.tif"


def days_between(start: date, end: date) -> int:
    if start > end:
        raise ValueError("start must be on or before end")
    return (end - start).days
