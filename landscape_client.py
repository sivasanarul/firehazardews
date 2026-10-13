"""SLIM hazard tiles and district reference geometry from the public S3 bucket."""
from __future__ import annotations

from calendar import monthrange
from datetime import date as calendar_date
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from PIL import Image
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader
from shapely.geometry import shape

try:
    from .burned_area_client import tile_bbox
except ImportError:
    from burned_area_client import tile_bbox

BASE_URL = "https://s3.waw3-1.cloudferro.com/slim/fire"
HAZARD_URL = f"{BASE_URL}/zambia_hazard_2025_continuous_cog.tif"
DISTRICTS_URL = f"{BASE_URL}/zambia_districts.geojson"
HAZARD_COLORS = ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"]

# SLIM burned-area masks published monthly (2025-2026) and as a 2025 yearly composite.
BURN_SCAR_BASE = f"{BASE_URL}/burnscar"
BURN_YEAR = 2025
BURN_MONTHS = [f"{BURN_YEAR}-{month:02d}" for month in range(1, 13)]
BURN_RECENT_YEAR = 2026
BURN_COLOR = "#c2410c"

# SLIM 2025 recovery-status raster (1 = recovered cell, 0 = nodata).
RECOVERY_YEAR = 2025
RECOVERY_URL = (f"{BASE_URL}/recovery/SLIM_RECOVERY_STATUS_"
                f"{RECOVERY_YEAR}0101_{RECOVERY_YEAR}1231_WGS84_cog.tif")
RECOVERY_COLOR = "#2f8f5b"


class LandscapeError(RuntimeError):
    """An upstream landscape layer could not be read."""


def hazard_style() -> dict:
    return {"year": 2025, "min": 0, "max": 1, "colors": HAZARD_COLORS,
            "label": "Hazard index", "source": HAZARD_URL}


def colorize_hazard(values, mask) -> bytes:
    """Use the same continuous 0–1 scale everywhere; zero is valid data."""
    colors = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in HAZARD_COLORS])
    valid = (mask > 0) & np.isfinite(values) & (values >= 0) & (values <= 1)
    safe = np.nan_to_num(values, nan=0, posinf=1, neginf=0).clip(0, 1)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    for channel in range(3):
        rgba[..., channel] = np.interp(safe, np.linspace(0, 1, len(colors)), colors[:, channel])
    rgba[..., 3] = np.where(valid, 255, 0)
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    return output.getvalue()


def get_hazard_tile(x: int, y: int, z: int, cache_dir=".cache/hazard") -> bytes:
    tile_bbox(x, y, z)
    key = sha256(f"{HAZARD_URL}/{z}/{x}/{y}/{HAZARD_COLORS}/v1".encode()).hexdigest()
    path = Path(cache_dir) / f"{key}.png"
    if path.exists():
        return path.read_bytes()
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT="45"):
            with Reader(HAZARD_URL) as cog:
                try:
                    tile = cog.tile(x, y, z, tilesize=256)
                    content = colorize_hazard(tile.data[0], tile.mask)
                except TileOutsideBounds:
                    content = colorize_hazard(np.zeros((256, 256)), np.zeros((256, 256)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return content
    except Exception as exc:
        raise LandscapeError("Could not load the 2025 hazard raster. Please retry.") from exc


def burn_periods() -> dict:
    """Monthly masks for 2026 (up to the current month) and the 2025 composite."""
    today = calendar_date.today()
    last_month = today.month if today.year == BURN_RECENT_YEAR else 12
    recent = [f"{BURN_RECENT_YEAR}-{month:02d}" for month in range(1, last_month + 1)]
    return {"year": BURN_YEAR, "months": list(BURN_MONTHS), "recent_year": BURN_RECENT_YEAR,
            "recent": recent, "color": BURN_COLOR, "label": "SLIM burned area",
            "source": f"{BURN_SCAR_BASE}/{BURN_YEAR}"}


def burn_mask_url(period: str) -> str:
    """Resolve 'yearly' or a 'YYYY-MM' month id to its published S3 COG."""
    if period == "yearly":
        return (f"{BURN_SCAR_BASE}/{BURN_YEAR}/SLIM_BURN_MASK_"
                f"{BURN_YEAR}0101_{BURN_YEAR}1231_WGS84_cog.tif")
    if not isinstance(period, str) or len(period.split("-")) != 2:
        raise ValueError("period must be 'yearly' or a 'YYYY-MM' month")
    try:
        year, month = (int(part) for part in period.split("-"))
    except ValueError as exc:
        raise ValueError("period must be 'yearly' or a 'YYYY-MM' month") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"no burned-area month is available for {period}")
    published = BURN_MONTHS if year == BURN_YEAR else (
        burn_periods()["recent"] if year == BURN_RECENT_YEAR else [])
    if f"{year:04d}-{month:02d}" not in published:
        raise ValueError(f"no burned-area month is available for {period}")
    last = monthrange(year, month)[1]
    return (f"{BURN_SCAR_BASE}/{year}/SLIM_BURN_MASK_"
            f"{year:04d}{month:02d}01_{year:04d}{month:02d}{last:02d}_WGS84_cog.tif")


def colorize_burn_mask(values, mask) -> bytes:
    """Render burn-mask cells (value 1) in a single ember tone, else transparent."""
    burned = (mask > 0) & (values == 1)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 0xC2, 0x41, 0x0C
    rgba[..., 3] = np.where(burned, 255, 0)
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    return output.getvalue()


def get_burn_tile(x: int, y: int, z: int, period: str, cache_dir=".cache/burnscars") -> bytes:
    url = burn_mask_url(period)
    tile_bbox(x, y, z)
    key = sha256(f"{url}/{z}/{x}/{y}/v1".encode()).hexdigest()
    path = Path(cache_dir) / f"{key}.png"
    if path.exists():
        return path.read_bytes()
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT="45"):
            with Reader(url) as cog:
                try:
                    tile = cog.tile(x, y, z, tilesize=256)
                    content = colorize_burn_mask(tile.data[0], tile.mask)
                except TileOutsideBounds:
                    content = colorize_burn_mask(np.zeros((256, 256)), np.zeros((256, 256)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return content
    except Exception as exc:
        raise LandscapeError("Could not load the burned-area raster. Please retry.") from exc


def recovery_style() -> dict:
    return {"year": RECOVERY_YEAR, "color": RECOVERY_COLOR, "label": "SLIM recovery status",
            "source": RECOVERY_URL}


def colorize_recovery(values, mask) -> bytes:
    """Render recovery-status cells (value 1) in a single green tone, else transparent."""
    recovered = (mask > 0) & (values == 1)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 0x2F, 0x8F, 0x5B
    rgba[..., 3] = np.where(recovered, 255, 0)
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    return output.getvalue()


def get_recovery_tile(x: int, y: int, z: int, cache_dir=".cache/recovery") -> bytes:
    tile_bbox(x, y, z)
    key = sha256(f"{RECOVERY_URL}/{z}/{x}/{y}/v1".encode()).hexdigest()
    path = Path(cache_dir) / f"{key}.png"
    if path.exists():
        return path.read_bytes()
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                          CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT="45"):
            with Reader(RECOVERY_URL) as cog:
                try:
                    tile = cog.tile(x, y, z, tilesize=256)
                    content = colorize_recovery(tile.data[0], tile.mask)
                except TileOutsideBounds:
                    content = colorize_recovery(np.zeros((256, 256)), np.zeros((256, 256)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return content
    except Exception as exc:
        raise LandscapeError("Could not load the recovery raster. Please retry.") from exc


def prepare_districts(data: dict) -> dict:
    """Reproject the supplied ESRI:102022 geometry, retaining names and codes."""
    crs = data.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
    frame = gpd.GeoDataFrame.from_features(data["features"], crs=crs)
    frame = frame.to_crs("EPSG:4326")
    columns = [c for c in ("DISTRICT", "PROVINCE", "DIST_CODE", "PROV_CODE", "geometry") if c in frame]
    return frame[columns].__geo_interface__


@lru_cache(maxsize=1)
def get_districts() -> dict:
    try:
        response = requests.get(DISTRICTS_URL, timeout=60)
        response.raise_for_status()
        return prepare_districts(response.json())
    except Exception as exc:
        raise LandscapeError("Could not load district boundaries. Please retry.") from exc


@lru_cache(maxsize=1)
def get_district_summary() -> list[dict]:
    """Names, codes and bounding boxes only, so the area picker stays lightweight."""
    districts = get_districts()
    summaries: list[dict] = []
    for feature in districts["features"]:
        properties = feature.get("properties", {})
        west, south, east, north = shape(feature["geometry"]).bounds
        summaries.append({
            "name": properties.get("DISTRICT"),
            "province": properties.get("PROVINCE"),
            "code": properties.get("DIST_CODE"),
            "bbox": [round(value, 6) for value in (west, south, east, north)],
        })
    summaries.sort(key=lambda item: item["name"] or "")
    return summaries
