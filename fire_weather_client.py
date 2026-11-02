"""Numerical Fire Weather Index retrieval and extent layers from GWIS and EWDS."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import urlencode
from zipfile import BadZipFile, ZipFile

import numpy as np
import requests
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


GWIS_URL = "https://maps.effis.emergency.copernicus.eu/gwis"
EWDS_URL = "https://ewds.climate.copernicus.eu/api"
EWDS_DATASET = "cems-fire-historical-v1"
EWDS_CATALOGUE_URL = (
    f"https://ewds.climate.copernicus.eu/api/catalogue/v1/collections/{EWDS_DATASET}"
)
FWI_PRODUCT = "fire_weather_index"
RECENT_DAYS = 7
REQUEST_TIMEOUT = (10, 45)
MAX_RETRIES = 2
FWI_COLORS = ("#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026")

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ewds-fwi")
_JOBS: dict[str, Future] = {}
_JOBS_LOCK = threading.Lock()


class FireWeatherError(RuntimeError):
    """A fire-weather provider failed or returned malformed data."""


class FireWeatherUnavailable(RuntimeError):
    """A provider returned no numerical FWI for the requested selection."""


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "td":
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self.in_cell:
            self.row.append("".join(self.cell_parts).strip())
            self.in_cell = False
        elif tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = []


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def route_fwi_source(selected_date: date, now: datetime | None = None) -> str:
    """Route by UTC calendar date: recent GWIS, older EWDS, future unavailable."""
    today = (now or utc_now()).astimezone(timezone.utc).date()
    if selected_date > today:
        return "future"
    if selected_date >= today - timedelta(days=RECENT_DAYS - 1):
        return "gwis"
    return "ewds"


def validate_location(longitude: float, latitude: float) -> None:
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")


def validate_extent(
    extent: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if extent is None:
        return None
    if len(extent) != 4:
        raise ValueError("extent must contain west, south, east and north")
    west, south, east, north = extent
    if not all(math.isfinite(value) for value in extent):
        raise ValueError("extent coordinates must be finite")
    if not (-180 <= west < east <= 180):
        raise ValueError("extent west/east must satisfy -180 <= west < east <= 180")
    if not (-90 <= south < north <= 90):
        raise ValueError("extent south/north must satisfy -90 <= south < north <= 90")
    return extent


def lonlat_to_web_mercator(longitude: float, latitude: float) -> tuple[float, float]:
    """Project WGS84 to EPSG:3857 without an optional GIS dependency."""
    validate_location(longitude, latitude)
    latitude = max(-85.05112878, min(85.05112878, latitude))
    radius = 6_378_137.0
    x = radius * math.radians(longitude)
    y = radius * math.log(math.tan(math.pi / 4 + math.radians(latitude) / 2))
    return x, y


def gwis_parameters(selected_date: date, longitude: float, latitude: float) -> dict[str, str]:
    x, y = lonlat_to_web_mercator(longitude, latitude)
    bbox = (x - 5_000, y - 5_000, x + 5_000, y + 5_000)
    return {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": "ecmwf.query",
        "query_layers": "ecmwf.query",
        "time": selected_date.isoformat(),
        "srs": "EPSG:3857",
        "info_format": "text/html",
        "format": "image/png",
        "styles": "",
        "bbox": ",".join(f"{value:.6f}" for value in bbox),
        "width": "101",
        "height": "101",
        "x": "50",
        "y": "50",
    }


def parse_gwis_fwi(html: str) -> float:
    parser = _TableParser()
    parser.feed(html)
    for row in parser.rows:
        if len(row) >= 2 and row[0].strip().casefold() == "fire weather index (fwi)".casefold():
            raw = row[1].strip()
            if not raw or raw.casefold() in {"none", "null", "nan", "n/a", "no data"}:
                raise FireWeatherUnavailable("GWIS has no numerical FWI at this date and location")
            try:
                value = float(raw)
            except ValueError as exc:
                raise FireWeatherError("GWIS returned an invalid FWI value") from exc
            if not math.isfinite(value):
                raise FireWeatherUnavailable("GWIS has no numerical FWI at this date and location")
            return value
    raise FireWeatherUnavailable("GWIS did not return a Fire Weather Index (FWI) row")


def _session() -> requests.Session:
    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _base_result(
    selected_date: date,
    longitude: float,
    latitude: float,
    source: str,
    status: str,
    *,
    message: str | None = None,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "date": selected_date.isoformat(),
        "location": {"longitude": longitude, "latitude": latitude},
        "source": source,
        "product": FWI_PRODUCT,
        "fwi": None,
        "unit": "dimensionless",
        "retrieval_time": utc_now().isoformat(),
        "status": status,
        "sampling": "point query",
    }
    if extent is not None:
        result["extent"] = list(extent)
    if message:
        result["message"] = message
    return result


def _ewds_result(
    selected_date: date,
    longitude: float,
    latitude: float,
    source: str,
    status: str,
    *,
    message: str | None = None,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    result = _base_result(
        selected_date,
        longitude,
        latitude,
        source,
        status,
        message=message,
        extent=extent,
    )
    result["sampling"] = "nearest grid cell"
    result["dataset"] = EWDS_DATASET
    return result


def _cache_key(
    source: str,
    selected_date: date,
    longitude: float,
    latitude: float,
    extent: tuple[float, float, float, float] | None = None,
) -> str:
    spatial = (
        ",".join(f"{value:.5f}" for value in extent)
        if extent is not None
        else f"{longitude:.5f},{latitude:.5f}"
    )
    selection = f"{source}|{FWI_PRODUCT}|{selected_date.isoformat()}|{spatial}"
    return sha256(selection.encode()).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)


def get_gwis_fwi(
    selected_date: date,
    longitude: float,
    latitude: float,
    cache_dir: str | Path,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    source = "GWIS numerical FWI"
    key = _cache_key("gwis-image-v2", selected_date, longitude, latitude, extent)
    path = Path(cache_dir) / "gwis" / f"{key}.json"
    cached = _read_json(path)
    if cached and cached.get("status") == "available":
        return cached
    try:
        response = _session().get(
            GWIS_URL,
            params=gwis_parameters(selected_date, longitude, latitude),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        value = parse_gwis_fwi(response.text)
    except FireWeatherUnavailable as exc:
        result = _base_result(
            selected_date,
            longitude,
            latitude,
            source,
            "unavailable",
            message=str(exc),
            extent=extent,
        )
        if extent is not None:
            result.update(
                coverage="single provider-rendered WMS image across selected extent",
                raster_url=_gwis_raster_url(selected_date, extent),
            )
        return result
    except requests.RequestException as exc:
        raise FireWeatherError(f"GWIS FWI request failed after bounded retries: {exc}") from exc
    result = _base_result(
        selected_date, longitude, latitude, source, "available", extent=extent
    )
    result["fwi"] = value
    if extent is not None:
        result.update(
            coverage="single provider-rendered WMS image across selected extent",
            raster_url=_gwis_raster_url(selected_date, extent),
        )
    _write_json(path, result)
    return result


def _gwis_raster_url(
    selected_date: date, extent: tuple[float, float, float, float]
) -> str:
    return "/api/fire-weather-index/gwis/raster.png?" + urlencode(
        {
            "date": selected_date.isoformat(),
            "bbox": ",".join(str(value) for value in extent),
        }
    )


def get_gwis_fwi_raster(
    selected_date: date,
    extent: tuple[float, float, float, float],
    cache_dir: str | Path,
    max_size: int = 1024,
) -> bytes:
    """Fetch one styled GWIS WMS image covering the complete selected extent."""
    if route_fwi_source(selected_date) != "gwis":
        raise ValueError("GWIS FWI imagery is only available for the recent-date route")
    west, south, east, north = validate_extent(extent)
    min_x, min_y = lonlat_to_web_mercator(west, south)
    max_x, max_y = lonlat_to_web_mercator(east, north)
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x >= span_y:
        width = max_size
        height = max(256, round(max_size * span_y / span_x))
    else:
        height = max_size
        width = max(256, round(max_size * span_x / span_y))
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": "ecmwf.fwi",
        "styles": "",
        "time": selected_date.isoformat(),
        "srs": "EPSG:3857",
        "format": "image/png",
        "transparent": "true",
        "bbox": ",".join(
            f"{value:.6f}" for value in (min_x, min_y, max_x, max_y)
        ),
        "width": str(width),
        "height": str(height),
    }
    prepared = requests.Request("GET", GWIS_URL, params=params).prepare()
    key = sha256(prepared.url.encode()).hexdigest()
    path = Path(cache_dir) / "gwis" / "rasters" / f"{key}.png"
    if path.exists():
        return path.read_bytes()
    try:
        response = _session().get(GWIS_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FireWeatherError(
            f"Could not retrieve the GWIS FWI area image after bounded retries: {exc}"
        ) from exc
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FireWeatherError("GWIS returned an unexpected non-PNG FWI area image")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def _tile_bbox(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    if not 0 <= z <= 22:
        raise ValueError("zoom must be between 0 and 22")
    tile_count = 2**z
    if not (0 <= x < tile_count and 0 <= y < tile_count):
        raise ValueError("tile coordinates are outside the requested zoom")
    limit = 20_037_508.342789244
    span = 2 * limit / tile_count
    min_x = -limit + x * span
    max_x = min_x + span
    max_y = limit - y * span
    return min_x, max_y - span, max_x, max_y


def get_gwis_fwi_tile(
    selected_date: date,
    x: int,
    y: int,
    z: int,
    cache_dir: str | Path,
) -> bytes:
    """Return the official styled GWIS FWI WMS tile for an extent view."""
    if route_fwi_source(selected_date) != "gwis":
        raise ValueError("GWIS FWI tiles are only available for the recent-date route")
    bbox = _tile_bbox(x, y, z)
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": "ecmwf.fwi",
        "styles": "",
        "time": selected_date.isoformat(),
        "srs": "EPSG:3857",
        "format": "image/png",
        "transparent": "true",
        "bbox": ",".join(f"{value:.6f}" for value in bbox),
        "width": "256",
        "height": "256",
    }
    prepared = requests.Request("GET", GWIS_URL, params=params).prepare()
    key = sha256(prepared.url.encode()).hexdigest()
    path = Path(cache_dir) / "gwis" / "tiles" / f"{key}.png"
    if path.exists():
        return path.read_bytes()
    try:
        response = _session().get(GWIS_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FireWeatherError(
            f"Could not retrieve the GWIS FWI area layer after bounded retries: {exc}"
        ) from exc
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FireWeatherError("GWIS returned an unexpected non-PNG FWI tile")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def _snap(value: float, direction: str) -> float:
    scaled = value / 0.05
    snapped = math.ceil(scaled) if direction == "up" else math.floor(scaled)
    return round(snapped * 0.05, 2)


def ewds_area(
    longitude: float,
    latitude: float,
    extent: tuple[float, float, float, float] | None = None,
) -> list[float]:
    """Form-valid [north, west, south, east] area for a point or extent."""
    if extent is None:
        west, south, east, north = (
            longitude - 0.3,
            latitude - 0.3,
            longitude + 0.3,
            latitude + 0.3,
        )
    else:
        west, south, east, north = validate_extent(extent)
    north = min(89.95, _snap(north, "up"))
    south = max(-89.95, _snap(south, "down"))
    west = max(-179.95, _snap(west, "down"))
    east = min(179.95, _snap(east, "up"))
    return [north, west, south, east]


def _ewds_credentials_available() -> bool:
    if os.getenv("EWDS_API_KEY"):
        return True
    if os.getenv("CDSAPI_KEY") and os.getenv("EWDS_API_URL", "").startswith(EWDS_URL):
        return True
    try:
        from cdsapi.api import get_url_key_verify

        configured_url, configured_key, _ = get_url_key_verify(None, None, None)
        return configured_url.startswith(EWDS_URL) and bool(configured_key)
    except Exception:
        return False


def _catalogue_constraints(cache_dir: Path) -> list[dict[str, Any]]:
    cache_path = cache_dir / "ewds" / "constraints.json"
    cached = _read_json(cache_path)
    try:
        cache_age = utc_now().timestamp() - cache_path.stat().st_mtime
    except OSError:
        cache_age = math.inf
    if (
        cache_age < 6 * 60 * 60
        and cached
        and isinstance(cached.get("constraints"), list)
    ):
        return cached["constraints"]
    response = _session().get(EWDS_CATALOGUE_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    catalogue = response.json()
    link = next((item["href"] for item in catalogue.get("links", []) if item.get("rel") == "constraints"), None)
    if not link:
        raise FireWeatherError("EWDS catalogue does not publish dataset constraints")
    constraints_response = _session().get(link, timeout=REQUEST_TIMEOUT)
    constraints_response.raise_for_status()
    payload = constraints_response.json()
    constraints = payload.get("constraints") if isinstance(payload, dict) else payload
    if not isinstance(constraints, list):
        raise FireWeatherError("EWDS returned an invalid dataset constraints schema")
    _write_json(cache_path, {"constraints": constraints, "retrieval_time": utc_now().isoformat()})
    return constraints


def choose_ewds_dataset_type(selected_date: date, constraints: list[dict[str, Any]]) -> str:
    matches: list[str] = []
    for item in constraints:
        if not all(
            value in item.get(field, [])
            for field, value in (
                ("year", f"{selected_date.year:04d}"),
                ("month", f"{selected_date.month:02d}"),
                ("day", f"{selected_date.day:02d}"),
                ("product_type", "reanalysis"),
                ("variable", FWI_PRODUCT),
                ("grid", "0.25/0.25"),
                ("data_format", "netcdf"),
                ("system_version", "4_1"),
            )
        ):
            continue
        matches.extend(item.get("dataset_type", []))
    if "consolidated_dataset" in matches:
        return "consolidated_dataset"
    if "intermediate_dataset" in matches:
        return "intermediate_dataset"
    raise FireWeatherUnavailable("EWDS does not list this date for numerical FWI download")


def ewds_request(
    selected_date: date,
    longitude: float,
    latitude: float,
    dataset_type: str,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    return {
        # StringChoiceWidget fields are scalar in the current form-generated
        # request; StringList* fields remain lists even for one selection.
        "product_type": "reanalysis",
        "variable": [FWI_PRODUCT],
        "dataset_type": dataset_type,
        "system_version": ["4_1"],
        "year": [f"{selected_date.year:04d}"],
        "month": [f"{selected_date.month:02d}"],
        "day": [f"{selected_date.day:02d}"],
        "grid": "0.25/0.25",
        "data_format": "netcdf",
        "area": ewds_area(longitude, latitude, extent),
    }


def _find_netcdf(download: Path, extraction_dir: Path) -> Path:
    with download.open("rb") as stream:
        signature = stream.read(2)
    if signature == b"PK":
        try:
            with ZipFile(download) as archive:
                safe = [name for name in archive.namelist() if not Path(name).is_absolute() and ".." not in Path(name).parts]
                archive.extractall(extraction_dir, members=safe)
        except BadZipFile as exc:
            raise FireWeatherError("EWDS returned an invalid ZIP download") from exc
        candidates = sorted(extraction_dir.rglob("*.nc"))
        if not candidates:
            raise FireWeatherError("EWDS ZIP did not contain a NetCDF file")
        return candidates[0]
    return download


def _select_ewds_fwi(dataset: Any, selected_date: date) -> tuple[Any, str, str, str]:
    variables = []
    for name, variable in dataset.data_vars.items():
        description = " ".join(
            str(variable.attrs.get(key, ""))
            for key in ("long_name", "standard_name", "short_name")
        ).casefold()
        if name.casefold() in {"fwi", FWI_PRODUCT} or "fire weather index" in description:
            variables.append(name)
    if not variables:
        raise FireWeatherError(
            f"EWDS NetCDF has no identifiable FWI variable (found: {', '.join(dataset.data_vars)})"
        )
    data = dataset[variables[0]]
    latitude_name = next(
        (name for name in ("latitude", "lat") if name in data.coords), None
    )
    longitude_name = next(
        (name for name in ("longitude", "lon") if name in data.coords), None
    )
    if not latitude_name or not longitude_name:
        raise FireWeatherError("EWDS NetCDF is missing latitude/longitude coordinates")
    if "time" in data.coords:
        times = np.asarray(data["time"].values).astype("datetime64[D]")
        target = np.datetime64(selected_date.isoformat())
        if target not in times:
            raise FireWeatherUnavailable("EWDS download did not contain the selected date")
        data = data.sel(time=target)
    for dimension in tuple(data.dims):
        if dimension not in {latitude_name, longitude_name} and data.sizes[dimension] == 1:
            data = data.isel({dimension: 0}, drop=True)
    unsupported = set(data.dims) - {latitude_name, longitude_name}
    if unsupported:
        raise FireWeatherError(
            f"EWDS FWI retains unsupported dimensions: {sorted(unsupported)}"
        )
    return data, variables[0], latitude_name, longitude_name


def sample_ewds_netcdf(
    path: Path, selected_date: date, longitude: float, latitude: float
) -> dict[str, float]:
    import xarray as xr

    with xr.open_dataset(path, decode_cf=True, mask_and_scale=True) as dataset:
        data, variable, latitude_name, longitude_name = _select_ewds_fwi(
            dataset, selected_date
        )
        lon_values = np.asarray(data[longitude_name].values, dtype=float)
        sample_longitude = (
            longitude % 360
            if np.nanmin(lon_values) >= 0 and longitude < 0
            else longitude
        )
        sampled = data.sel(
            {latitude_name: latitude, longitude_name: sample_longitude},
            method="nearest",
        ).squeeze(drop=True)
        if sampled.size != 1:
            raise FireWeatherError(
                f"EWDS FWI retains unsupported dimensions: {dict(sampled.sizes)}"
            )
        value = float(sampled.values)
        if not math.isfinite(value):
            raise FireWeatherUnavailable("EWDS nearest grid cell has no numerical FWI")
        return {
            "fwi": value,
            "longitude": float(sampled[longitude_name].values),
            "latitude": float(sampled[latitude_name].values),
            "variable": variable,
        }


def _longitude_mask(longitude: Any, west: float, east: float) -> Any:
    if float(longitude.min()) < 0:
        return (longitude >= west) & (longitude <= east)
    west, east = west % 360, east % 360
    if west <= east:
        return (longitude >= west) & (longitude <= east)
    return (longitude >= west) | (longitude <= east)


def summarize_ewds_netcdf(
    path: Path,
    selected_date: date,
    extent: tuple[float, float, float, float],
) -> dict[str, float | int]:
    import xarray as xr

    west, south, east, north = extent
    with xr.open_dataset(path, decode_cf=True, mask_and_scale=True) as dataset:
        data, _, latitude_name, longitude_name = _select_ewds_fwi(
            dataset, selected_date
        )
        longitude = data[longitude_name]
        selected = data.where(
            (data[latitude_name] >= south)
            & (data[latitude_name] <= north)
            & _longitude_mask(longitude, west, east),
            drop=True,
        )
        values = np.asarray(selected.values, dtype=float)
        valid = values[np.isfinite(values)]
        if not valid.size:
            raise FireWeatherUnavailable(
                "EWDS has no numerical FWI grid cells inside the selected extent"
            )
        return {
            "min": float(valid.min()),
            "max": float(valid.max()),
            "mean": float(valid.mean()),
            "cell_count": int(valid.size),
        }


def _colorize_fwi(values: np.ndarray, minimum: float, maximum: float) -> bytes:
    colors = np.array(
        [[int(color[index : index + 2], 16) for index in (1, 3, 5)] for color in FWI_COLORS]
    )
    valid = np.isfinite(values)
    if maximum <= minimum:
        maximum = minimum + 1
    normalized = np.nan_to_num(
        (values - minimum) / (maximum - minimum), nan=0, posinf=1, neginf=0
    ).clip(0, 1)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    for channel in range(3):
        rgba[..., channel] = np.interp(
            normalized, np.linspace(0, 1, len(colors)), colors[:, channel]
        )
    rgba[..., 3] = np.where(valid, 220, 0)
    output = BytesIO()
    Image.fromarray(rgba).save(output, format="PNG")
    return output.getvalue()


def get_ewds_fwi_raster(raster_id: str, cache_dir: str | Path) -> bytes:
    """Render the cached numerical EWDS grid for its requested extent."""
    if not re.fullmatch(r"[0-9a-f]{64}", raster_id):
        raise ValueError("invalid FWI raster identifier")
    cache_root = Path(cache_dir)
    result_path = cache_root / "ewds" / f"{raster_id}.json"
    result = _read_json(result_path)
    if not result or result.get("status") != "available":
        raise FireWeatherUnavailable("The historical FWI raster is not available yet")
    extent = validate_extent(tuple(result.get("extent", ())))
    if extent is None:
        raise FireWeatherError("The cached FWI result has no spatial extent")
    image_path = cache_root / "ewds" / raster_id / "raster.png"
    if image_path.exists():
        return image_path.read_bytes()
    job_dir = cache_root / "ewds" / raster_id
    netcdf = _find_netcdf(job_dir / "download.nc", job_dir / "extracted")
    import xarray as xr

    west, south, east, north = extent
    with xr.open_dataset(netcdf, decode_cf=True, mask_and_scale=True) as dataset:
        data, _, latitude_name, longitude_name = _select_ewds_fwi(
            dataset, date.fromisoformat(result["date"])
        )
        longitude = data[longitude_name]
        selected = data.where(
            (data[latitude_name] >= south)
            & (data[latitude_name] <= north)
            & _longitude_mask(longitude, west, east),
            drop=True,
        )
        values = np.asarray(selected.values, dtype=float)
        if values.ndim != 2 or not values.size:
            raise FireWeatherUnavailable(
                "EWDS has no numerical FWI grid inside the selected extent"
            )
        latitudes = np.asarray(selected[latitude_name].values, dtype=float)
        longitudes = np.asarray(selected[longitude_name].values, dtype=float)
        order = [selected.dims.index(latitude_name), selected.dims.index(longitude_name)]
        values = np.transpose(values, order)
        if latitudes[0] < latitudes[-1]:
            values = np.flip(values, axis=0)
        display_longitudes = np.where(longitudes > 180, longitudes - 360, longitudes)
        values = values[:, np.argsort(display_longitudes)]
    statistics = result.get("statistics", {})
    content = _colorize_fwi(
        values, float(statistics.get("min", 0)), float(statistics.get("max", 1))
    )
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    return content


def _download_and_sample_ewds(
    selected_date: date,
    longitude: float,
    latitude: float,
    cache_root: Path,
    key: str,
    extent: tuple[float, float, float, float] | None,
) -> dict[str, Any]:
    source = "Copernicus EWDS ERA5 historical FWI"
    result_path = cache_root / "ewds" / f"{key}.json"
    try:
        constraints = _catalogue_constraints(cache_root)
        dataset_type = choose_ewds_dataset_type(selected_date, constraints)
        if dataset_type == "intermediate_dataset":
            source = "Copernicus EWDS ERA5T intermediate historical FWI"
        job_dir = cache_root / "ewds" / key
        job_dir.mkdir(parents=True, exist_ok=True)
        download = job_dir / "download.nc"
        if not download.exists():
            import cdsapi

            client = cdsapi.Client(
                url=os.getenv("EWDS_API_URL", EWDS_URL),
                key=os.getenv("EWDS_API_KEY") or os.getenv("CDSAPI_KEY"),
                timeout=60,
                retry_max=3,
                sleep_max=30,
                quiet=True,
                progress=False,
                session=requests.Session(),
            )
            client.retrieve(
                EWDS_DATASET,
                ewds_request(
                    selected_date, longitude, latitude, dataset_type, extent
                ),
                str(download),
            )
        netcdf = _find_netcdf(download, job_dir / "extracted")
        sampled = sample_ewds_netcdf(netcdf, selected_date, longitude, latitude)
        result = _ewds_result(
            selected_date,
            longitude,
            latitude,
            source,
            "available",
            extent=extent,
        )
        result.update(
            fwi=sampled["fwi"],
            sampled_location={
                "longitude": sampled["longitude"],
                "latitude": sampled["latitude"],
            },
            dataset_type=dataset_type,
            variable=sampled["variable"],
        )
        if extent is not None:
            result.update(
                coverage="numerical raster across selected extent",
                raster_id=key,
                raster_url=f"/api/fire-weather-index/rasters/{key}.png",
                statistics=summarize_ewds_netcdf(netcdf, selected_date, extent),
            )
    except FireWeatherUnavailable as exc:
        result = _ewds_result(
            selected_date,
            longitude,
            latitude,
            source,
            "unavailable",
            message=str(exc),
            extent=extent,
        )
    except Exception as exc:
        result = _ewds_result(
            selected_date,
            longitude,
            latitude,
            source,
            "failed",
            message=f"EWDS FWI retrieval failed: {exc}",
            extent=extent,
        )
    _write_json(result_path, result)
    return result


def get_ewds_fwi(
    selected_date: date,
    longitude: float,
    latitude: float,
    cache_dir: str | Path,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    source = "Copernicus EWDS ERA5 historical FWI"
    cache_root = Path(cache_dir)
    key = _cache_key("ewds", selected_date, longitude, latitude, extent)
    result_path = cache_root / "ewds" / f"{key}.json"
    with _JOBS_LOCK:
        future = _JOBS.get(key)
        if future is not None and future.done():
            _JOBS.pop(key, None)
            return future.result()
    cached = _read_json(result_path)
    if cached and cached.get("status") == "available":
        return cached
    try:
        result_age = utc_now().timestamp() - result_path.stat().st_mtime
    except OSError:
        result_age = math.inf
    if cached and cached.get("status") in {"unavailable", "failed"} and result_age < 5 * 60:
        return cached
    if not _ewds_credentials_available():
        return _ewds_result(
            selected_date,
            longitude,
            latitude,
            source,
            "unavailable",
            message=(
                "EWDS credentials are not configured. Set backend-only EWDS_API_KEY "
                "and optionally EWDS_API_URL, or configure the service account .cdsapirc; "
                "the configured URL must be https://ewds.climate.copernicus.eu/api. "
                "Accept the dataset CC-BY terms in EWDS first."
            ),
            extent=extent,
        )
    with _JOBS_LOCK:
        future = _JOBS.get(key)
        if future is None:
            future = _EXECUTOR.submit(
                _download_and_sample_ewds,
                selected_date,
                longitude,
                latitude,
                cache_root,
                key,
                extent,
            )
            _JOBS[key] = future
    return _ewds_result(
        selected_date,
        longitude,
        latitude,
        source,
        "pending",
        message="EWDS download is queued or processing. Update the map later to read the cached result.",
        extent=extent,
    )


def get_fire_weather_index(
    selected_date: date,
    longitude: float,
    latitude: float,
    cache_dir: str | Path = ".cache/fire_weather",
    now: datetime | None = None,
    extent: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    validate_location(longitude, latitude)
    extent = validate_extent(extent)
    route = route_fwi_source(selected_date, now)
    if route == "future":
        return _base_result(
            selected_date,
            longitude,
            latitude,
            "No provider selected",
            "unavailable",
            message="Future dates are not routed to GWIS or EWDS historical data.",
            extent=extent,
        )
    if route == "gwis":
        return get_gwis_fwi(
            selected_date, longitude, latitude, cache_dir, extent=extent
        )
    return get_ewds_fwi(
        selected_date, longitude, latitude, cache_dir, extent=extent
    )


def failed_fire_weather_result(
    selected_date: date,
    longitude: float,
    latitude: float,
    message: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    route = route_fwi_source(selected_date, now)
    source = {
        "gwis": "GWIS numerical FWI",
        "ewds": "Copernicus EWDS ERA5 historical FWI",
        "future": "No provider selected",
    }[route]
    return _base_result(
        selected_date, longitude, latitude, source, "failed", message=message
    )
