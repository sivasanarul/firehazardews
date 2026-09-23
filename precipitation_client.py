"""Cached client for precipitation data used by the fire propagation model."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math

import requests

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

WEB_MERCATOR_LIMIT = 20_037_508.342789244

TIME_WINDOWS_HOURS = {
    "1h": 1,
    "6h": 6,
    "24h": 24,
    "72h": 72,
    "7d": 7 * 24,
    "14d": 14 * 24,
}

class PrecipitationError(RuntimeError):
    """Raised when precipitation data cannot be retrieved."""

def tile_bbox(
    x: int,
    y: int,
    z: int,
) -> tuple[float, float, float, float]:
    """Return XYZ tile bounding box in EPSG:3857."""

    if not 0 <= z <= 22:
        raise ValueError("zoom must be between 0 and 22")

    tile_count = 2 ** z

    if not (0 <= x < tile_count and 0 <= y < tile_count):
        raise ValueError("tile coordinates are outside the requested zoom")

    span = 2 * WEB_MERCATOR_LIMIT / tile_count

    min_x = -WEB_MERCATOR_LIMIT + x * span
    max_x = min_x + span

    max_y = WEB_MERCATOR_LIMIT - y * span
    min_y = max_y - span

    return min_x, min_y, max_x, max_y

def web_mercator_to_lonlat(
    x: float,
    y: float,
) -> tuple[float, float]:
    """Convert EPSG:3857 coordinates to longitude/latitude."""

    lon = (x / WEB_MERCATOR_LIMIT) * 180.0

    lat = math.degrees(
        math.atan(
            math.sinh(
                y / WEB_MERCATOR_LIMIT * math.pi
            )
        )
    )

    return lon, lat

def tile_center_lonlat(
    x: int,
    y: int,
    z: int,
) -> tuple[float, float]:
    """Return longitude/latitude of the center of an XYZ tile."""

    min_x, min_y, max_x, max_y = tile_bbox(x, y, z)

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    return web_mercator_to_lonlat(center_x, center_y)

def get_precipitation(
    latitude: float,
    longitude: float,
    time_window: str = "24h",
    observation_date: date | None = None,
    cache_dir: str | Path = ".cache/precipitation",
    timeout: int = 30,
) -> dict:
    """
    Retrieve accumulated precipitation for a location.

    Parameters
    ----------
    latitude:
        Latitude in WGS84.

    longitude:
        Longitude in WGS84.

    time_window:
        Accumulation interval:
        1h, 6h, 24h, 72h, 7d or 14d.

    observation_date:
        End date of the precipitation accumulation.
        Defaults to today.

    cache_dir:
        Local cache directory.

    timeout:
        HTTP request timeout.

    Returns
    -------
    dict
        {
            "latitude": ...,
            "longitude": ...,
            "time_window": "24h",
            "precipitation_mm": 12.4,
            "start_time": "...",
            "end_time": "...",
            "hourly": [...]
        }
    """

    if time_window not in TIME_WINDOWS_HOURS:
        raise ValueError(
            f"time_window must be one of "
            f"{list(TIME_WINDOWS_HOURS)}"
        )

    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")

    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")

    if observation_date is None:
        observation_date = date.today()

    hours = TIME_WINDOWS_HOURS[time_window]

    # End at the end of observation_date.
    end_dt = datetime.combine(
        observation_date,
        datetime.max.time(),
    ).replace(
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=timezone.utc,
    )

    start_dt = end_dt - timedelta(hours=hours - 1)

    cache_path = (
        Path(cache_dir)
        / time_window
        / observation_date.isoformat()
        / f"{latitude:.5f}_{longitude:.5f}.json"
    )

    if cache_path.exists():
        return json.loads(cache_path.read_text())

    today = date.today()

    #
    # Recent/current weather:
    # use the forecast API.
    #
    # Older observations:
    # use the historical/archive API.
    #
    if observation_date >= today - timedelta(days=5):
        url = OPEN_METEO_FORECAST_URL

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation",
            "past_days": min(14, max(1, hours // 24 + 1)),
            "forecast_days": 1,
            "timezone": "UTC",
        }

    else:
        url = OPEN_METEO_ARCHIVE_URL

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation",
            "start_date": start_dt.date().isoformat(),
            "end_date": end_dt.date().isoformat(),
            "timezone": "UTC",
        }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException as exc:
        raise PrecipitationError(
            f"Could not retrieve precipitation: {exc}"
        ) from exc

    except ValueError as exc:
        raise PrecipitationError(
            "Weather service returned invalid JSON"
        ) from exc

    hourly = data.get("hourly")

    if not hourly:
        raise PrecipitationError(
            "Weather service returned no hourly data"
        )

    times = hourly.get("time", [])
    precipitation = hourly.get("precipitation", [])

    if len(times) != len(precipitation):
        raise PrecipitationError(
            "Invalid precipitation response"
        )

    selected = []

    for timestamp, value in zip(times, precipitation):

        dt = datetime.fromisoformat(timestamp).replace(
            tzinfo=timezone.utc
        )

        if start_dt <= dt <= end_dt:

            selected.append(
                {
                    "time": timestamp,
                    "precipitation_mm": value or 0.0,
                }
            )

    total_precipitation = sum(
        item["precipitation_mm"]
        for item in selected
    )

    result = {
        "latitude": latitude,
        "longitude": longitude,
        "time_window": time_window,
        "precipitation_mm": round(
            total_precipitation,
            3,
        ),
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "hourly": selected,
    }

    cache_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_path.write_text(
        json.dumps(
            result,
            indent=2,
        )
    )

    return result

def get_precipitation_for_tile(
    x: int,
    y: int,
    z: int,
    time_window: str = "24h",
    observation_date: date | None = None,
    cache_dir: str | Path = ".cache/precipitation",
    timeout: int = 30,
) -> dict:
    """
    Retrieve precipitation for the center of an XYZ tile.

    This is intended for model calculations, not map rendering.
    """

    longitude, latitude = tile_center_lonlat(
        x,
        y,
        z,
    )

    return get_precipitation(
        latitude=latitude,
        longitude=longitude,
        time_window=time_window,
        observation_date=observation_date,
        cache_dir=cache_dir,
        timeout=timeout,
    )
