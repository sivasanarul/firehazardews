"""SLIM hazard tiles and district reference geometry from the public S3 bucket."""
from __future__ import annotations

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
