"""Dynamic map tiles for the SLIM annual land-cover COGs."""

from __future__ import annotations

from calendar import isleap
from datetime import date
from hashlib import sha256
from pathlib import Path

import rasterio
from rio_tiler.io import Reader

try:
    from .burned_area_client import tile_bbox
    from .qml_style import load_qml_style
except ImportError:  # Allow direct module execution/import from slim_fire.
    from burned_area_client import tile_bbox
    from qml_style import load_qml_style


LAND_COVER_YEARS = (2000, 2005, 2010, 2015, 2020, 2024)
LAND_COVER_RESOLUTION_M = {
    2000: 30,
    2005: 30,
    2010: 30,
    2015: 30,
    2020: 10,
    2024: 10,
}
LAND_COVER_BASE_URL = (
    "https://s3.waw3-1.cloudferro.com/swift/v1/slim/results_lulc/cogs"
)

class LandCoverError(RuntimeError):
    """Raised when a land-cover COG tile cannot be produced."""


def closest_landcover_year(start: date, end: date) -> int:
    """Choose the available year nearest the midpoint of a requested period."""
    if start > end:
        raise ValueError("start must be on or before end")
    midpoint = start + (end - start) / 2
    year_length = 366 if isleap(midpoint.year) else 365
    decimal_year = midpoint.year + (midpoint.timetuple().tm_yday - 1) / year_length
    # Prefer the newer product when a midpoint is exactly equidistant.
    return min(LAND_COVER_YEARS, key=lambda year: (abs(year - decimal_year), -year))


def landcover_url(year: int) -> str:
    if year not in LAND_COVER_YEARS:
        raise ValueError(f"land-cover year must be one of {LAND_COVER_YEARS}")
    resolution = LAND_COVER_RESOLUTION_M[year]
    return f"{LAND_COVER_BASE_URL}/SLIM_LC_LandCover_{year}_{resolution}m_cog.tif"


def get_landcover_tile(
    x: int,
    y: int,
    z: int,
    year: int,
    cache_dir: str | Path = ".cache/landcover",
) -> bytes:
    """Read, colorize, and cache one Web Mercator tile from a remote COG."""
    tile_bbox(x, y, z)  # Validate XYZ coordinates before opening the COG.
    url = landcover_url(year)
    style = load_qml_style()
    style_signature = sha256(repr(style.entries).encode()).hexdigest()
    cache_key = sha256(f"{url}/{z}/{x}/{y}/{style_signature}".encode()).hexdigest()
    cache_path = Path(cache_dir) / str(year) / f"{cache_key}.png"
    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
            GDAL_HTTP_MULTIPLEX="YES",
        ):
            with Reader(url, options={"nodata": 0}) as cog:
                image = cog.tile(x, y, z, tilesize=256)
                content = image.render(colormap=style.colormap)
    except Exception as exc:
        raise LandCoverError(f"Could not render land-cover {year}: {exc}") from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    return content
