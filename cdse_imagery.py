"""Sentinel-2 and Landsat 8/9 discovery and rendered imagery from CDSE."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

try:
    from .cdse_client import CDSEClient, get_cdse_client
except ImportError:  # Allow: python cdse_imagery.py
    from cdse_client import CDSEClient, get_cdse_client


TRUE_COLOR_EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["B04", "B03", "B02", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [2.5*s.B04, 2.5*s.B03, 2.5*s.B02, s.dataMask];
}
"""

SENTINEL2_SWIR_EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["B12", "B11", "B04", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [2.5*s.B12, 2.5*s.B11, 2.5*s.B04, s.dataMask];
}
"""

LANDSAT_SWIR_EVALSCRIPT = """//VERSION=3
function setup() {
  return { input: ["B07", "B06", "B04", "dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  return [2.5*s.B07, 2.5*s.B06, 2.5*s.B04, s.dataMask];
}
"""

SOURCE_CONFIG = {
    "sentinel-2": {
        "collection": "sentinel-2-l2a",
        "label": "Sentinel-2",
        "evalscripts": {
            "true-color": TRUE_COLOR_EVALSCRIPT,
            "swir": SENTINEL2_SWIR_EVALSCRIPT,
        },
    },
    "landsat-8-9": {
        "collection": "landsat-ot-l1",
        "label": "Landsat 8/9",
        "evalscripts": {
            "true-color": TRUE_COLOR_EVALSCRIPT,
            "swir": LANDSAT_SWIR_EVALSCRIPT,
        },
    },
}
SEARCH_SOURCES = (*SOURCE_CONFIG, "all")


class CDSEImageryError(RuntimeError):
    """Raised when acquisition metadata or rendered imagery is invalid."""


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise CDSEImageryError("Invalid satellite acquisition datetime") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def point_bbox(longitude: float, latitude: float, radius_km: float) -> list[float]:
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise CDSEImageryError("Longitude or latitude is outside its valid range")
    if not (0.1 <= radius_km <= 50):
        raise CDSEImageryError("radius_km must be between 0.1 and 50")
    lat_delta = radius_km / 111.32
    lon_delta = radius_km / max(1.0, 111.32 * math.cos(math.radians(latitude)))
    return [
        max(-180.0, longitude - lon_delta),
        max(-90.0, latitude - lat_delta),
        min(180.0, longitude + lon_delta),
        min(90.0, latitude + lat_delta),
    ]


def validate_bbox(bbox: list[float] | tuple[float, ...]) -> list[float]:
    if len(bbox) != 4:
        raise CDSEImageryError("bbox must contain west,south,east,north")
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise CDSEImageryError("bbox values must be numbers") from exc
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise CDSEImageryError("bbox is outside EPSG:4326 or has invalid ordering")
    return [west, south, east, north]


def _validate_source(source: str, *, allow_all: bool = False) -> str:
    valid = SEARCH_SOURCES if allow_all else tuple(SOURCE_CONFIG)
    if source not in valid:
        choices = ", ".join(valid)
        raise CDSEImageryError(f"source must be one of: {choices}")
    return source


def _feature_source(feature: dict[str, Any]) -> str | None:
    collection = str(feature.get("collection") or "").lower()
    properties = feature.get("properties") or {}
    platform = str(properties.get("platform") or "").lower()
    if collection == SOURCE_CONFIG["sentinel-2"]["collection"] or platform.startswith("sentinel-2"):
        return "sentinel-2"
    if collection == SOURCE_CONFIG["landsat-8-9"]["collection"] or platform.startswith("landsat"):
        return "landsat-8-9"
    return None


def search_satellite_acquisitions(
    longitude: float,
    latitude: float,
    *,
    source: str = "sentinel-2",
    days: int = 45,
    reference_datetime: str | datetime | None = None,
    before_days: int = 10,
    after_days: int = 15,
    limit: int = 6,
    radius_km: float = 5,
    max_cloud_cover: float = 100,
    now: datetime | None = None,
    client: CDSEClient | None = None,
) -> dict[str, Any]:
    """Find recent Sentinel-2 and/or Landsat products around a detection."""
    source = _validate_source(source, allow_all=True)
    if not (1 <= days <= 365):
        raise CDSEImageryError("days must be between 1 and 365")
    if not (0 <= before_days <= 90 and 0 <= after_days <= 90):
        raise CDSEImageryError("before_days and after_days must be between 0 and 90")
    if not (1 <= limit <= 20):
        raise CDSEImageryError("limit must be between 1 and 20")
    if not (0 <= max_cloud_cover <= 100):
        raise CDSEImageryError("max_cloud_cover must be between 0 and 100")
    bbox = point_bbox(longitude, latitude, radius_km)
    reference = _utc(reference_datetime) if reference_datetime else None
    if reference is not None:
        start = reference - timedelta(days=before_days)
        end = reference + timedelta(days=after_days)
    else:
        end = _utc(now or datetime.now(timezone.utc))
        start = end - timedelta(days=days)
    requested_sources = tuple(SOURCE_CONFIG) if source == "all" else (source,)
    request_body = {
        "bbox": bbox,
        "datetime": f"{_iso(start)}/{_iso(end)}",
        "limit": min(100, max(limit * 4, limit)),
    }
    cdse = client or get_cdse_client()
    features: list[dict[str, Any]] = []
    # CDSE rejects a multi-collection Catalog request even though STAC permits
    # one. Search each collection separately and merge the results here.
    for requested_source in requested_sources:
        collection_request = {
            **request_body,
            "collections": [SOURCE_CONFIG[requested_source]["collection"]],
        }
        response = cdse.request(
            "POST", "/catalog/v1/search", json=collection_request
        )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise CDSEImageryError("CDSE returned unreadable acquisition metadata") from exc
        features.extend(payload.get("features", []))

    acquisitions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for feature in features:
        properties = feature.get("properties") or {}
        observed = properties.get("datetime") or properties.get("start_datetime")
        if not observed:
            continue
        cloud = properties.get("eo:cloud_cover")
        try:
            cloud_value = float(cloud) if cloud is not None else None
        except (TypeError, ValueError):
            cloud_value = None
        if cloud_value is not None and cloud_value > max_cloud_cover:
            continue
        item_id = str(feature.get("id") or "")
        key = (item_id, str(observed))
        if key in seen:
            continue
        seen.add(key)
        feature_source = _feature_source(feature)
        if feature_source not in requested_sources:
            continue
        acquisitions.append(
            {
                "id": item_id,
                "datetime": _iso(_utc(str(observed))),
                "cloud_cover": cloud_value,
                "platform": properties.get("platform") or SOURCE_CONFIG[feature_source]["label"],
                "source": feature_source,
            }
        )
    acquisitions.sort(key=lambda item: item["datetime"], reverse=True)
    return {
        "bbox": bbox,
        "reference_datetime": _iso(reference) if reference else None,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "source": source,
        "acquisitions": acquisitions[:limit],
    }


def search_sentinel2_acquisitions(
    longitude: float,
    latitude: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """Backward-compatible Sentinel-2-only acquisition search."""
    return search_satellite_acquisitions(
        longitude, latitude, source="sentinel-2", **kwargs
    )


def render_satellite_image(
    acquisition_datetime: str,
    bbox: list[float] | tuple[float, ...],
    *,
    source: str = "sentinel-2",
    style: str = "true-color",
    width: int = 768,
    height: int = 768,
    cache_dir: str | Path | None = None,
    client: CDSEClient | None = None,
) -> bytes:
    """Render and cache a small satellite PNG centred on an acquisition."""
    source = _validate_source(source)
    evalscripts = SOURCE_CONFIG[source]["evalscripts"]
    if style not in evalscripts:
        raise CDSEImageryError("style must be true-color or swir")
    if not (128 <= width <= 1536 and 128 <= height <= 1536):
        raise CDSEImageryError("image dimensions must be between 128 and 1536 pixels")
    bounds = validate_bbox(bbox)
    observed = _utc(acquisition_datetime)
    start, end = observed - timedelta(minutes=5), observed + timedelta(minutes=5)
    request_body = {
        "input": {
            "bounds": {
                "bbox": bounds,
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
            },
            "data": [
                {
                    "type": SOURCE_CONFIG[source]["collection"],
                    "dataFilter": {
                        "timeRange": {"from": _iso(start), "to": _iso(end)},
                        "mosaickingOrder": "mostRecent",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/png"}}
            ],
        },
        "evalscript": evalscripts[style],
    }
    serialized = json.dumps(request_body, sort_keys=True, separators=(",", ":"))
    cache_root = Path(
        cache_dir if cache_dir is not None else os.getenv("CDSE_IMAGE_CACHE_DIR", ".cache/cdse")
    )
    cache_file = cache_root / f"{hashlib.sha256(serialized.encode()).hexdigest()}.png"
    if cache_file.is_file():
        return cache_file.read_bytes()

    response = (client or get_cdse_client()).request(
        "POST",
        "/process/v1",
        json=request_body,
        headers={"Accept": "image/png"},
        timeout=float(os.getenv("CDSE_PROCESS_TIMEOUT", "90")),
    )
    content = response.content
    if not content or not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CDSEImageryError("CDSE did not return a valid PNG image")
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(cache_file)
    return content


def render_sentinel2_image(
    acquisition_datetime: str,
    bbox: list[float] | tuple[float, ...],
    **kwargs: Any,
) -> bytes:
    """Backward-compatible Sentinel-2-only renderer."""
    return render_satellite_image(
        acquisition_datetime, bbox, source="sentinel-2", **kwargs
    )
