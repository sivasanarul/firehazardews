"""Terrain-condition coarse precipitation without changing its cell mean.

The method is intentionally diagnostic and conservative rather than a claim of
new 100 m meteorological observations.  Within each coarse weather cell it:

1. computes an elevation-anomaly factor;
2. computes wind exposure from slope, aspect, wind direction and wind speed;
3. multiplies and bounds those factors; and
4. normalizes by their (optionally pixel-weighted) mean.

Consequently the valid-pixel mean of every wet hour equals the source coarse
precipitation.  Dry source hours remain dry everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

DEFAULT_ELEVATION_COEFFICIENT = 0.0005  # m-1: +500 m gives +25 percent
DEFAULT_ASPECT_WEIGHT = 0.5
DEFAULT_REFERENCE_WIND_KMH = 20.0
DEFAULT_FACTOR_MIN = 0.25
DEFAULT_FACTOR_MAX = 4.0


class PrecipitationDownscaleError(ValueError):
    """Raised when terrain precipitation cannot be normalized safely."""


def _terrain_arrays(elevation_m, slope_deg, aspect_deg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    elevation = np.asarray(elevation_m, dtype=np.float64)
    slope = np.asarray(slope_deg, dtype=np.float64)
    aspect = np.asarray(aspect_deg, dtype=np.float64)
    if elevation.shape != slope.shape or elevation.shape != aspect.shape:
        raise PrecipitationDownscaleError("elevation, slope and aspect must have identical shapes")
    return elevation, slope, aspect


def _valid_mask(elevation: np.ndarray, slope: np.ndarray, aspect: np.ndarray, valid_mask=None) -> np.ndarray:
    valid = np.isfinite(elevation) & np.isfinite(slope) & np.isfinite(aspect)
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.shape != elevation.shape:
            raise PrecipitationDownscaleError("valid_mask must match the terrain shape")
        valid &= mask
    if not valid.any():
        raise PrecipitationDownscaleError("terrain cell contains no valid pixels")
    return valid


def _weights(shape: tuple[int, ...], valid: np.ndarray, pixel_weights=None) -> np.ndarray:
    if pixel_weights is None:
        result = np.ones(shape, dtype=np.float64)
    else:
        result = np.asarray(pixel_weights, dtype=np.float64)
        if result.shape != shape:
            raise PrecipitationDownscaleError("pixel_weights must match the terrain shape")
    if np.any(~np.isfinite(result[valid])) or np.any(result[valid] <= 0.0):
        raise PrecipitationDownscaleError("valid pixel weights must be finite and positive")
    return result


def wind_exposure(
    slope_deg,
    aspect_deg,
    wind_direction_deg: float,
    wind_speed_kmh: float,
    reference_wind_kmh: float = DEFAULT_REFERENCE_WIND_KMH,
) -> np.ndarray:
    """Return signed wind exposure using meteorological wind-from direction.

    An aspect facing the incoming wind is positive (windward), the opposite
    aspect is negative (leeward), and flat ground is neutral.  Wind speed scales
    the strength up to twice ``reference_wind_kmh``.
    """
    if reference_wind_kmh <= 0.0:
        raise PrecipitationDownscaleError("reference_wind_kmh must be positive")
    slope = np.clip(np.asarray(slope_deg, dtype=np.float64), 0.0, 90.0)
    aspect = np.asarray(aspect_deg, dtype=np.float64)
    alignment = np.cos(np.radians(float(wind_direction_deg) - aspect))
    slope_strength = np.sin(np.radians(slope))
    speed_strength = np.clip(float(wind_speed_kmh) / reference_wind_kmh, 0.0, 2.0)
    return alignment * slope_strength * speed_strength


def terrain_precipitation_factor(
    elevation_m,
    slope_deg,
    aspect_deg,
    wind_direction_deg: float,
    wind_speed_kmh: float,
    *,
    valid_mask=None,
    pixel_weights=None,
    elevation_coefficient: float = DEFAULT_ELEVATION_COEFFICIENT,
    aspect_weight: float = DEFAULT_ASPECT_WEIGHT,
    reference_wind_kmh: float = DEFAULT_REFERENCE_WIND_KMH,
    factor_min: float = DEFAULT_FACTOR_MIN,
    factor_max: float = DEFAULT_FACTOR_MAX,
) -> np.ndarray:
    """Return a positive terrain factor normalized to weighted mean one."""
    if elevation_coefficient < 0.0 or aspect_weight < 0.0:
        raise PrecipitationDownscaleError("terrain coefficients must be non-negative")
    if not 0.0 < factor_min <= factor_max:
        raise PrecipitationDownscaleError("factors require 0 < factor_min <= factor_max")
    elevation, slope, aspect = _terrain_arrays(elevation_m, slope_deg, aspect_deg)
    valid = _valid_mask(elevation, slope, aspect, valid_mask)
    weights = _weights(elevation.shape, valid, pixel_weights)
    mean_elevation = np.average(elevation[valid], weights=weights[valid])
    elevation_factor = 1.0 + elevation_coefficient * (elevation - mean_elevation)
    exposure = wind_exposure(slope, aspect, wind_direction_deg, wind_speed_kmh, reference_wind_kmh)
    combined = np.clip(elevation_factor * (1.0 + aspect_weight * exposure), factor_min, factor_max)
    mean_factor = np.average(combined[valid], weights=weights[valid])
    if not np.isfinite(mean_factor) or mean_factor <= 0.0:
        raise PrecipitationDownscaleError("terrain factor has no positive finite mean")
    return np.where(valid, combined / mean_factor, np.nan)


def downscale_precipitation(coarse_precipitation_mm: float, normalized_factor, *, valid_mask=None) -> np.ndarray:
    """Redistribute one coarse precipitation value over a normalized fine grid."""
    precipitation = float(coarse_precipitation_mm)
    if not np.isfinite(precipitation) or precipitation < 0.0:
        raise PrecipitationDownscaleError("coarse precipitation must be finite and non-negative")
    factor = np.asarray(normalized_factor, dtype=np.float64)
    valid = np.isfinite(factor)
    if valid_mask is not None:
        mask = np.asarray(valid_mask, dtype=bool)
        if mask.shape != factor.shape:
            raise PrecipitationDownscaleError("valid_mask must match the factor shape")
        valid &= mask
    if not valid.any():
        raise PrecipitationDownscaleError("precipitation factor contains no valid pixels")
    result = np.full(factor.shape, np.nan, dtype=np.float32)
    result[valid] = 0.0 if precipitation == 0.0 else (precipitation * factor[valid]).astype(np.float32)
    return result


def downscale_hourly_precipitation(
    series: Sequence[object],
    elevation_m,
    slope_deg,
    aspect_deg,
    *,
    valid_mask=None,
    pixel_weights=None,
    elevation_coefficient: float = DEFAULT_ELEVATION_COEFFICIENT,
    aspect_weight: float = DEFAULT_ASPECT_WEIGHT,
    reference_wind_kmh: float = DEFAULT_REFERENCE_WIND_KMH,
    factor_min: float = DEFAULT_FACTOR_MIN,
    factor_max: float = DEFAULT_FACTOR_MAX,
) -> np.ndarray:
    """Return hourly terrain precipitation with shape ``(hours, *terrain.shape)``.

    Series items must expose ``precipitation_mm``, ``wind_azimuth_deg`` and
    ``wind_kmh`` attributes, as Nelson ``WeatherHour`` objects do.
    """
    elevation, slope, aspect = _terrain_arrays(elevation_m, slope_deg, aspect_deg)
    valid = _valid_mask(elevation, slope, aspect, valid_mask)
    output = np.full((len(series), *elevation.shape), np.nan, dtype=np.float32)
    for index, hour in enumerate(series):
        precipitation = float(getattr(hour, "precipitation_mm"))
        if precipitation == 0.0:
            output[index][valid] = 0.0
            continue
        factor = terrain_precipitation_factor(
            elevation, slope, aspect,
            float(getattr(hour, "wind_azimuth_deg")), float(getattr(hour, "wind_kmh")),
            valid_mask=valid, pixel_weights=pixel_weights,
            elevation_coefficient=elevation_coefficient, aspect_weight=aspect_weight,
            reference_wind_kmh=reference_wind_kmh, factor_min=factor_min, factor_max=factor_max,
        )
        output[index] = downscale_precipitation(precipitation, factor, valid_mask=valid)
    return output
