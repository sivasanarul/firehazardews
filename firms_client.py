"""NASA FIRMS Area API client with source selection and a small disk cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"
MAX_DAYS_PER_REQUEST = 5

# Standard Processing is listed first so that it is preferred for historical
# periods. NOAA-21 currently has no Standard Processing Area API product.
SENSOR_SOURCES = {
    "modis": ("MODIS_SP", "MODIS_NRT"),
    "viirs_snpp": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "viirs_noaa20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "viirs_noaa21": ("VIIRS_NOAA21_NRT",),
}


class FirmsError(RuntimeError):
    """Raised when FIRMS cannot satisfy a request."""


@dataclass(frozen=True)
class Availability:
    source: str
    min_date: date
    max_date: date

    def contains(self, value: date) -> bool:
        return self.min_date <= value <= self.max_date


@dataclass(frozen=True)
class DownloadChunk:
    source: str
    start: date
    days: int


def validate_bbox(bbox: Iterable[float]) -> tuple[float, float, float, float]:
    """Validate and normalize west, south, east, north coordinates."""
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox must contain four numbers: west,south,east,north") from exc

    if not (-180 <= west < east <= 180):
        raise ValueError("bbox west/east must satisfy -180 <= west < east <= 180")
    if not (-90 <= south < north <= 90):
        raise ValueError("bbox south/north must satisfy -90 <= south < north <= 90")
    return west, south, east, north


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Parse an API bbox query parameter."""
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must contain four numbers: west,south,east,north")
    return validate_bbox(parts)


def requested_sensors(sensor: str) -> tuple[str, ...]:
    """Expand the public sensor name to one or more sensor families."""
    normalized = sensor.strip().lower()
    if normalized == "all":
        return tuple(SENSOR_SOURCES)
    if normalized not in SENSOR_SOURCES:
        choices = ", ".join(("all", *SENSOR_SOURCES))
        raise ValueError(f"unknown sensor '{sensor}'; choose one of: {choices}")
    return (normalized,)


def plan_downloads(
    start: date,
    end: date,
    sensors: Iterable[str],
    availability: dict[str, Availability],
) -> list[DownloadChunk]:
    """Choose SP before NRT and combine adjacent days into <=5-day calls."""
    if start > end:
        raise ValueError("start must be on or before end")

    chunks: list[DownloadChunk] = []
    for sensor in sensors:
        sources = SENSOR_SOURCES[sensor]
        current = start
        while current <= end:
            source = next(
                (
                    item
                    for item in sources
                    if item in availability and availability[item].contains(current)
                ),
                None,
            )
            if source is None:
                current += timedelta(days=1)
                continue

            days = 1
            while days < MAX_DAYS_PER_REQUEST and current + timedelta(days=days) <= end:
                next_date = current + timedelta(days=days)
                preferred = next(
                    (
                        item
                        for item in sources
                        if item in availability and availability[item].contains(next_date)
                    ),
                    None,
                )
                if preferred != source:
                    break
                days += 1

            chunks.append(DownloadChunk(source=source, start=current, days=days))
            current += timedelta(days=days)
    return chunks


class FirmsClient:
    """Download and normalize FIRMS detections for a bbox and date range."""

    def __init__(
        self,
        map_key: str,
        cache_dir: str | Path = ".cache/firms",
        timeout: int = 120,
        session: requests.Session | None = None,
    ) -> None:
        if not map_key:
            raise ValueError("FIRMS_MAP_KEY is not set")
        self.map_key = map_key
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get_csv(self, url: str) -> pd.DataFrame:
        cache_key = sha256(url.encode("utf-8")).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.csv"
        if cache_file.exists():
            return pd.read_csv(cache_file)

        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FirmsError(f"Could not reach FIRMS: {exc}") from exc
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise FirmsError(f"FIRMS returned HTTP {response.status_code}") from exc

        text = response.text.strip()
        if not text:
            return pd.DataFrame()
        if text.lower().startswith(("error", "invalid")) or "," not in text:
            raise FirmsError(f"Unexpected FIRMS response: {text[:200]}")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")
        try:
            return pd.read_csv(StringIO(text))
        except pd.errors.ParserError as exc:
            raise FirmsError("FIRMS returned malformed CSV") from exc

    def availability(self) -> dict[str, Availability]:
        url = f"{FIRMS_BASE_URL}/data_availability/csv/{self.map_key}/all"
        frame = self._get_csv(url)
        required = {"data_id", "min_date", "max_date"}
        if not required.issubset(frame.columns):
            raise FirmsError("FIRMS availability response is missing required columns")

        result: dict[str, Availability] = {}
        for row in frame.itertuples(index=False):
            result[row.data_id] = Availability(
                source=row.data_id,
                min_date=date.fromisoformat(str(row.min_date)),
                max_date=date.fromisoformat(str(row.max_date)),
            )
        return result

    def _download_chunk(
        self,
        bbox: tuple[float, float, float, float],
        chunk: DownloadChunk,
    ) -> pd.DataFrame:
        area = ",".join(format(value, "g") for value in bbox)
        url = (
            f"{FIRMS_BASE_URL}/area/csv/{self.map_key}/{chunk.source}/"
            f"{area}/{chunk.days}/{chunk.start.isoformat()}"
        )
        frame = self._get_csv(url)
        if not frame.empty:
            frame["firms_source"] = chunk.source
        return frame

    def download(
        self,
        bbox: Iterable[float],
        start: date,
        end: date,
        sensor: str = "all",
    ) -> gpd.GeoDataFrame:
        normalized_bbox = validate_bbox(bbox)
        sensors = requested_sensors(sensor)
        chunks = plan_downloads(start, end, sensors, self.availability())
        # Multi-sensor requests consist of independent FIRMS calls. A small
        # worker pool avoids multiplying latency by every sensor/date chunk.
        configured_workers = int(os.getenv("FIRMS_DOWNLOAD_WORKERS", "4"))
        worker_count = max(1, min(configured_workers, len(chunks))) if chunks else 1
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            frames = list(
                executor.map(
                    lambda chunk: self._download_chunk(normalized_bbox, chunk),
                    chunks,
                )
            )
        nonempty = [frame for frame in frames if not frame.empty]
        if not nonempty:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

        frame = pd.concat(nonempty, ignore_index=True)
        required = {"latitude", "longitude", "acq_date", "acq_time"}
        if not required.issubset(frame.columns):
            raise FirmsError("FIRMS area response is missing required columns")

        frame["acq_time"] = frame["acq_time"].astype(str).str.replace(
            r"\.0$", "", regex=True
        ).str.zfill(4)
        frame["acq_datetime_utc"] = pd.to_datetime(
            frame["acq_date"].astype(str) + " " + frame["acq_time"],
            format="%Y-%m-%d %H%M",
            errors="coerce",
            utc=True,
        ).map(lambda value: value.isoformat() if not pd.isna(value) else None)
        frame = frame.drop_duplicates(
            subset=["latitude", "longitude", "acq_date", "acq_time", "satellite"],
        )
        return gpd.GeoDataFrame(
            frame,
            geometry=gpd.points_from_xy(frame["longitude"], frame["latitude"]),
            crs="EPSG:4326",
        )
