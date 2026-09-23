"""Cached proxy client for the GWIS MODIS & VIIRS NRT burned-area WMS."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import pi
from pathlib import Path

import requests


GWIS_WMS_URL = "https://maps.effis.emergency.copernicus.eu/gwis"
GWIS_BURNED_AREA_LAYER = "nrt.ba"
WEB_MERCATOR_LIMIT = 20_037_508.342789244


class BurnedAreaError(RuntimeError):
    """Raised when a GWIS burned-area tile cannot be retrieved."""


def tile_bbox(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Return an XYZ tile bounding box in EPSG:3857."""
    if not 0 <= z <= 22:
        raise ValueError("zoom must be between 0 and 22")
    tile_count = 2**z
    if not (0 <= x < tile_count and 0 <= y < tile_count):
        raise ValueError("tile coordinates are outside the requested zoom")
    span = 2 * WEB_MERCATOR_LIMIT / tile_count
    min_x = -WEB_MERCATOR_LIMIT + x * span
    max_x = min_x + span
    max_y = WEB_MERCATOR_LIMIT - y * span
    min_y = max_y - span
    return min_x, min_y, max_x, max_y


def get_burned_area_tile(
    x: int,
    y: int,
    z: int,
    start: date,
    end: date,
    cache_dir: str | Path = ".cache/gwis_burned_area",
    timeout: int = 60,
) -> bytes:
    """Fetch one time-filtered GWIS WMS tile, using a local PNG cache."""
    if start > end:
        raise ValueError("start must be on or before end")
    bbox = tile_bbox(x, y, z)
    params = {
        "service": "WMS",
        "request": "GetMap",
        "version": "1.1.1",
        "layers": GWIS_BURNED_AREA_LAYER,
        "styles": "",
        "format": "image/png",
        "transparent": "true",
        "srs": "EPSG:3857",
        "bbox": ",".join(format(value, ".6f") for value in bbox),
        "width": "256",
        "height": "256",
        "time": f"{start.isoformat()}/{end.isoformat()}",
    }
    prepared = requests.Request("GET", GWIS_WMS_URL, params=params).prepare()
    cache_path = Path(cache_dir) / f"{sha256(prepared.url.encode()).hexdigest()}.png"
    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        response = requests.get(GWIS_WMS_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BurnedAreaError(f"Could not retrieve GWIS burned area: {exc}") from exc
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BurnedAreaError("GWIS returned an unexpected non-PNG response")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(response.content)
    return response.content
