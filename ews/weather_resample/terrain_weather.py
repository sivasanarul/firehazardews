"""Terrain-conditioned adjustment of coarse weather before Nelson/NFDRS4.

Coarse (~9 km) Open-Meteo weather is not downscaled into new meteorology.
Instead, three simple physical corrections are applied at a DEM/slope/aspect
sample point so NFDRS4 sees terrain-conditioned inputs:

    temperature   -> elevation lapse-rate correction
    humidity      -> recomputed from the original dew point and corrected temperature
    solar         -> scaled by a slope/aspect/sun-position radiation factor
    precipitation -> left untouched (no terrain correction)
    wind          -> left untouched (no terrain correction)

The output is "100 m terrain-conditioned dead fuel moisture", not "100 m
weather": the underlying meteorological information is still coarse.

Use ``sample_terrain`` to read elevation/slope/aspect (as produced by
``slim_fire/ews/dem/create_dem.py``) at a point, then ``terrain_adjust_weather``
to correct a coarse ``WeatherHour`` series before calling
``slim_fire.ews.nelsonmodel.nfdrs4_pipeline.run_nfdrs4_point``.

All correction math is written with numpy so it accepts either scalars or
arrays: ``terrain_adjust_weather_grid`` applies it to a full elevation/slope/
aspect array (e.g. every 100 m pixel inside one coarse weather cell) at once,
preserving each pixel's exact continuous terrain value with no quantization.
NFDRS4 itself has no array API, so ``run_terrain_comparison`` runs it once per distinct
terrain state (see ``terrain_states``) rather than once per pixel.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform
from scipy.ndimage import gaussian_filter

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from slim_fire.ews.nelsonmodel.nfdrs4_pipeline import WeatherHour  # noqa: E402

LAPSE_RATE_C_PER_M = 0.0065
SOLAR_FACTOR_MIN = 0.5
SOLAR_FACTOR_MAX = 1.5
PRECIP_SMOOTHING_M = 1500.0
PRECIP_SENSITIVITY_S_PER_M = 2.0
PRECIP_WEIGHT_MIN = 0.5
PRECIP_WEIGHT_MAX = 2.0
METERS_PER_DEGREE_LAT = 110574.0
METERS_PER_DEGREE_LON = 111320.0


class TerrainResampleError(RuntimeError):
    """Raised when terrain rasters cannot be sampled at a requested point."""


@dataclass(frozen=True)
class TerrainPoint:
    """Elevation, slope, and aspect for one 100 m grid cell."""

    elevation_m: float
    slope_deg: float
    aspect_deg: float


def sample_terrain(dem_path: Path, slope_path: Path, aspect_path: Path, latitude: float, longitude: float) -> TerrainPoint:
    """Sample the DEM/slope/aspect GeoTIFFs written by dem/create_dem.py at a point."""
    values = []
    for path in (dem_path, slope_path, aspect_path):
        with rasterio.open(path) as src:
            xs, ys = warp_transform("EPSG:4326", src.crs, [longitude], [latitude])
            row, col = src.index(xs[0], ys[0])
            if not (0 <= row < src.height and 0 <= col < src.width):
                raise TerrainResampleError(f"{latitude:.5f},{longitude:.5f} is outside {path}")
            value = src.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
            if src.nodata is not None and value == src.nodata:
                raise TerrainResampleError(f"No terrain data at {latitude:.5f},{longitude:.5f} in {path}")
            values.append(float(value))
    return TerrainPoint(*values)


def calculate_dewpoint(temperature_c, relative_humidity_pct):
    """Magnus-formula dew point (degrees C); scalars or numpy arrays."""
    a, b = 17.625, 243.04
    rh = np.maximum(np.asarray(relative_humidity_pct, dtype=np.float64), 0.1) / 100.0
    gamma = np.log(rh) + (a * temperature_c) / (b + temperature_c)
    return (b * gamma) / (a - gamma)


def rh_from_temperature_dewpoint(temperature_c, dewpoint_c):
    """Relative humidity (%) implied by a temperature and a fixed dew point; scalars or arrays."""
    a, b = 17.625, 243.04
    numerator = np.exp((a * dewpoint_c) / (b + dewpoint_c))
    denominator = np.exp((a * temperature_c) / (b + temperature_c))
    return np.clip(100.0 * numerator / denominator, 0.0, 100.0)


def elevation_temperature_correction(temperature_c, dem_elevation_m, weather_elevation_m, lapse_rate: float = LAPSE_RATE_C_PER_M):
    """Apply a fixed lapse-rate correction for the DEM/weather elevation difference; scalars or arrays."""
    return temperature_c - lapse_rate * (dem_elevation_m - weather_elevation_m)


def solar_position(timestamp: datetime, latitude, longitude):
    """Approximate sun (azimuth, elevation) in degrees for one timestamp; latitude/longitude may be arrays."""
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = timestamp.astimezone(timezone.utc)
    day_of_year = utc.timetuple().tm_yday
    hour_decimal = utc.hour + utc.minute / 60.0 + utc.second / 3600.0

    fractional_year = 2 * np.pi / 365.0 * (day_of_year - 1 + (hour_decimal - 12) / 24.0)
    equation_of_time = 229.18 * (
        0.000075 + 0.001868 * np.cos(fractional_year) - 0.032077 * np.sin(fractional_year)
        - 0.014615 * np.cos(2 * fractional_year) - 0.040849 * np.sin(2 * fractional_year)
    )
    declination = (
        0.006918 - 0.399912 * np.cos(fractional_year) + 0.070257 * np.sin(fractional_year)
        - 0.006758 * np.cos(2 * fractional_year) + 0.000907 * np.sin(2 * fractional_year)
        - 0.002697 * np.cos(3 * fractional_year) + 0.00148 * np.sin(3 * fractional_year)
    )
    longitude = np.asarray(longitude, dtype=np.float64)
    latitude = np.asarray(latitude, dtype=np.float64)
    true_solar_time = hour_decimal * 60.0 + equation_of_time + 4.0 * longitude
    hour_angle = np.radians(true_solar_time / 4.0 - 180.0)

    lat_rad = np.radians(latitude)
    cos_zenith = np.clip(
        np.sin(lat_rad) * np.sin(declination) + np.cos(lat_rad) * np.cos(declination) * np.cos(hour_angle),
        -1.0, 1.0,
    )
    zenith = np.arccos(cos_zenith)
    elevation = 90.0 - np.degrees(zenith)

    cos_azimuth = np.clip(
        (np.sin(declination) - np.sin(lat_rad) * cos_zenith) / (np.cos(lat_rad) * np.sin(zenith) + 1e-12),
        -1.0, 1.0,
    )
    azimuth = np.degrees(np.arccos(cos_azimuth))
    azimuth = np.where(hour_angle > 0, 360.0 - azimuth, azimuth)
    return azimuth, elevation


def solar_terrain_factor(
    slope_deg, aspect_deg, sun_azimuth_deg, sun_elevation_deg,
    factor_min: float = SOLAR_FACTOR_MIN, factor_max: float = SOLAR_FACTOR_MAX,
):
    """Ratio of sloped-surface to flat-surface direct irradiance, clamped to a safe range; scalars or arrays."""
    slope = np.radians(slope_deg)
    aspect = np.radians(aspect_deg)
    zenith = np.radians(90.0 - sun_elevation_deg)
    sun_azimuth = np.radians(sun_azimuth_deg)

    cos_incidence = np.maximum(
        np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(sun_azimuth - aspect), 0.0,
    )
    cos_flat = np.maximum(np.cos(zenith), 1e-3)
    factor = np.clip(cos_incidence / cos_flat, factor_min, factor_max)
    return np.where(np.asarray(sun_elevation_deg) <= 0.0, 1.0, factor)  # sun below horizon: shortwave is already ~0


def terrain_adjust_weather(
    series: list[WeatherHour], latitude: float, longitude: float, weather_elevation_m: float, terrain: TerrainPoint,
    lapse_rate: float = LAPSE_RATE_C_PER_M,
) -> list[WeatherHour]:
    """Apply the temperature/RH/solar terrain corrections to a coarse weather series.

    Precipitation and wind are passed through unchanged, per the simplified
    downscaling design: only elevation and slope/aspect introduce local variability.
    """
    adjusted = []
    for hour in series:
        dewpoint = calculate_dewpoint(hour.temperature_c, hour.relative_humidity_pct)
        temperature_c = elevation_temperature_correction(hour.temperature_c, terrain.elevation_m, weather_elevation_m, lapse_rate)
        relative_humidity_pct = rh_from_temperature_dewpoint(temperature_c, dewpoint)
        sun_azimuth, sun_elevation = solar_position(hour.timestamp, latitude, longitude)
        factor = solar_terrain_factor(terrain.slope_deg, terrain.aspect_deg, sun_azimuth, sun_elevation)
        adjusted.append(replace(
            hour,
            temperature_c=float(temperature_c),
            relative_humidity_pct=float(relative_humidity_pct),
            shortwave_wm2=float(hour.shortwave_wm2 * factor),
        ))
    return adjusted


def terrain_adjust_weather_grid(
    series: list[WeatherHour], weather_latitude: float, weather_longitude: float, weather_elevation_m: float,
    elevation_m: np.ndarray, slope_deg: np.ndarray, aspect_deg: np.ndarray, lapse_rate: float = LAPSE_RATE_C_PER_M,
) -> dict[str, np.ndarray]:
    """Vectorized ``terrain_adjust_weather`` over an array of pixels, per hour.

    ``elevation_m``/``slope_deg``/``aspect_deg`` are arrays of any shape holding
    each pixel's exact continuous terrain value (e.g. every 100 m pixel inside
    one coarse weather cell) -- no binning/quantization is applied. Only the
    single weather cell's latitude/longitude is used for sun position, since it
    is effectively constant across a ~9 km cell. Returned arrays have shape
    ``(len(series), *elevation_m.shape)``.
    """
    elevation_m = np.asarray(elevation_m, dtype=np.float32)
    slope_deg = np.asarray(slope_deg, dtype=np.float32)
    aspect_deg = np.asarray(aspect_deg, dtype=np.float32)
    shape = (len(series), *elevation_m.shape)
    temperature_c = np.empty(shape, dtype=np.float32)
    relative_humidity_pct = np.empty(shape, dtype=np.float32)
    shortwave_wm2 = np.empty(shape, dtype=np.float32)

    for index, hour in enumerate(series):
        dewpoint = calculate_dewpoint(hour.temperature_c, hour.relative_humidity_pct)
        temperature_c[index] = elevation_temperature_correction(hour.temperature_c, elevation_m, weather_elevation_m, lapse_rate)
        relative_humidity_pct[index] = rh_from_temperature_dewpoint(temperature_c[index], dewpoint)
        sun_azimuth, sun_elevation = solar_position(hour.timestamp, weather_latitude, weather_longitude)
        factor = solar_terrain_factor(slope_deg, aspect_deg, sun_azimuth, sun_elevation)
        shortwave_wm2[index] = hour.shortwave_wm2 * factor

    return {"temperature_c": temperature_c, "relative_humidity_pct": relative_humidity_pct, "shortwave_wm2": shortwave_wm2}


def weather_series_for_pixel(series: list[WeatherHour], corrected: dict[str, np.ndarray], pixel_index: tuple[int, ...]) -> list[WeatherHour]:
    """Rebuild one pixel's corrected hourly series from ``terrain_adjust_weather_grid`` output."""
    return [
        replace(
            hour,
            temperature_c=float(corrected["temperature_c"][(hour_index, *pixel_index)]),
            relative_humidity_pct=float(corrected["relative_humidity_pct"][(hour_index, *pixel_index)]),
            shortwave_wm2=float(corrected["shortwave_wm2"][(hour_index, *pixel_index)]),
        )
        for hour_index, hour in enumerate(series)
    ]


def pixel_size_meters(transform, latitude: float) -> tuple[float, float]:
    """Approximate EPSG:4326 pixel width/height in metres at a latitude."""
    longitude_scale = METERS_PER_DEGREE_LON * float(np.cos(np.radians(latitude)))
    return abs(transform.a) * longitude_scale, abs(transform.e) * METERS_PER_DEGREE_LAT


def smoothed_terrain_gradient(
    elevation_m: np.ndarray, dx_m: float, dy_m: float, smoothing_m: float = PRECIP_SMOOTHING_M,
) -> tuple[np.ndarray, np.ndarray]:
    """Terrain gradient (m/m) after Gaussian smoothing, for orographic uplift.

    Smoothing is not optional tuning: orographic precipitation responds to
    terrain at kilometre scales, and raw 100 m gradients are dominated by DEM
    noise that would produce spurious pixel-to-pixel rainfall contrasts.
    """
    values = np.asarray(elevation_m, dtype=np.float64)
    if smoothing_m > 0:
        valid = np.isfinite(values).astype(np.float64)
        sigma = (smoothing_m / dy_m, smoothing_m / dx_m)
        # Normalising by a smoothed validity mask keeps NaN edges from bleeding inward.
        weight = gaussian_filter(valid, sigma, mode="nearest")
        total = gaussian_filter(np.where(np.isfinite(values), values, 0.0), sigma, mode="nearest")
        values = np.divide(total, weight, out=np.full_like(total, np.nan), where=weight > 1e-6)
    gradient_y, gradient_x = np.gradient(values, dy_m, dx_m)
    return gradient_x, gradient_y


def wind_components(wind_kmh, wind_azimuth_deg) -> tuple[np.ndarray, np.ndarray]:
    """Eastward/northward wind in m/s from Open-Meteo speed and direction-from."""
    speed = np.asarray(wind_kmh, dtype=np.float64) / 3.6
    azimuth = np.radians(np.asarray(wind_azimuth_deg, dtype=np.float64))
    return -speed * np.sin(azimuth), -speed * np.cos(azimuth)


def upslope_precipitation_weights(
    gradient_x: np.ndarray, gradient_y: np.ndarray, wind_u: float, wind_v: float,
    sensitivity: float = PRECIP_SENSITIVITY_S_PER_M,
    weight_min: float = PRECIP_WEIGHT_MIN, weight_max: float = PRECIP_WEIGHT_MAX,
) -> np.ndarray:
    """Mass-conserving orographic weights for one hour's wind vector.

    Windward slopes (positive ``wind . grad h`` uplift) are enhanced and lee
    slopes suppressed; rescaling by the mean keeps the cell total unchanged.
    """
    uplift = wind_u * gradient_x + wind_v * gradient_y
    weights = np.clip(1.0 + sensitivity * uplift, weight_min, weight_max)
    mean = np.nanmean(weights)
    if not np.isfinite(mean) or mean <= 0:
        return np.ones_like(weights)
    return weights / mean


def upslope_precipitation_grid(
    series: list[WeatherHour], gradient_x: np.ndarray, gradient_y: np.ndarray,
    sensitivity: float = PRECIP_SENSITIVITY_S_PER_M,
    weight_min: float = PRECIP_WEIGHT_MIN, weight_max: float = PRECIP_WEIGHT_MAX,
) -> np.ndarray:
    """Per-hour, per-pixel precipitation (mm) redistributed by terrain-forced uplift.

    Returns shape ``(len(series), *gradient_x.shape)``. Dry hours short-circuit,
    which matters because most fire-season hours have no rain to redistribute.
    """
    result = np.zeros((len(series), *gradient_x.shape), dtype=np.float32)
    for index, hour in enumerate(series):
        if hour.precipitation_mm <= 0.0:
            continue
        wind_u, wind_v = wind_components(hour.wind_kmh, hour.wind_azimuth_deg)
        weights = upslope_precipitation_weights(
            gradient_x, gradient_y, float(wind_u), float(wind_v), sensitivity, weight_min, weight_max,
        )
        result[index] = hour.precipitation_mm * weights
    return result
