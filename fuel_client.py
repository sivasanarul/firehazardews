"""Cached proxy client for the GWIS global fuel-map WMS layer."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import requests


GWIS_WMS_URL = "https://maps.effis.emergency.copernicus.eu/gwis"
# Layer name advertised by the GWIS WMS GetCapabilities document.
GWIS_FUEL_LAYER = "fuel_map"
GWIS_FUEL_LAYERS = frozenset({GWIS_FUEL_LAYER})
WEB_MERCATOR_LIMIT = 20_037_508.342789244
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FuelError(RuntimeError):
    """Raised when a GWIS fuel tile cannot be retrieved."""


def tile_bbox(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Return an XYZ tile bounding box in EPSG:3857."""
    valid_types = all(
        isinstance(value, int) and not isinstance(value, bool) for value in (x, y, z)
    )
    if not valid_types:
        raise ValueError("tile coordinates and zoom must be integers")
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


def get_fuel_tile(
    x: int,
    y: int,
    z: int,
    fuel_layer: str = GWIS_FUEL_LAYER,
    cache_dir: str | Path = ".cache/gwis_fuel",
    timeout: int | float = 60,
) -> bytes:
    """Fetch one GWIS fuel WMS tile, using a local PNG cache."""
    if not isinstance(fuel_layer, str) or fuel_layer not in GWIS_FUEL_LAYERS:
        choices = ", ".join(sorted(GWIS_FUEL_LAYERS))
        raise ValueError(f"fuel_layer must be one of: {choices}")
    valid_timeout = (
        isinstance(timeout, (int, float))
        and not isinstance(timeout, bool)
        and timeout > 0
    )
    if not valid_timeout:
        raise ValueError("timeout must be a positive number")

    bbox = tile_bbox(x, y, z)
    params = {
        "service": "WMS",
        "request": "GetMap",
        "version": "1.1.1",
        "layers": fuel_layer,
        "styles": "",
        "format": "image/png",
        "transparent": "true",
        "srs": "EPSG:3857",
        "bbox": ",".join(format(value, ".6f") for value in bbox),
        "width": "256",
        "height": "256",
    }
    prepared = requests.Request("GET", GWIS_WMS_URL, params=params).prepare()
    cache_path = Path(cache_dir) / f"{sha256(prepared.url.encode()).hexdigest()}.png"
    if cache_path.exists():
        try:
            cached_content = cache_path.read_bytes()
        except OSError as exc:
            raise FuelError(f"Could not read cached GWIS fuel data: {exc}") from exc
        if cached_content.startswith(PNG_SIGNATURE):
            return cached_content

    try:
        response = requests.get(GWIS_WMS_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FuelError(f"Could not retrieve GWIS fuel data: {exc}") from exc
    if not response.content.startswith(PNG_SIGNATURE):
        raise FuelError("GWIS returned an unexpected non-PNG response")

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
    except OSError as exc:
        raise FuelError(f"Could not cache GWIS fuel data: {exc}") from exc
    return response.content
