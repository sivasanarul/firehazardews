"""Configuration for the prototype dynamic total-fuel-load model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuelClassParameters:
    """Calibratable parameters for one land-cover class."""

    allocation: float
    loss_10day: float
    fmax_mg_ha: float


# Numeric IDs are deliberately kept separate from the scientific parameters so
# another land-cover product can be used without changing model code.
LANDCOVER_CLASS_IDS: dict[str, int] = {
    "closed_forest": 11,
    "open_forest": 12,
    "closed_shrubland": 21,
    "open_shrubland": 22,
    "permanent_herbaceous": 30,
    "periodic_herbaceous": 40,
    "built_up": 50,
    "bare_land": 60,
    "water": 70,
    "wetland": 80,
}

FUEL_PARAMETERS: dict[str, FuelClassParameters] = {
    "closed_forest": FuelClassParameters(0.35, 0.015, 8.0),
    "open_forest": FuelClassParameters(0.50, 0.018, 7.0),
    "closed_shrubland": FuelClassParameters(0.60, 0.020, 7.0),
    "open_shrubland": FuelClassParameters(0.70, 0.025, 6.0),
    "permanent_herbaceous": FuelClassParameters(0.75, 0.025, 7.0),
    "periodic_herbaceous": FuelClassParameters(0.75, 0.030, 7.0),
    "built_up": FuelClassParameters(0.00, 0.000, 0.0),
    "bare_land": FuelClassParameters(0.00, 0.000, 0.0),
    "water": FuelClassParameters(0.00, 0.000, 0.0),
    # Prototype assumption: mapped wetlands are fire-carrying herbaceous wetlands.
    "wetland": FuelClassParameters(0.75, 0.025, 7.0),
}

TARGET_CRS = "EPSG:32735"
TARGET_RESOLUTION_METRES = 100.0
NODATA = -9999.0
DEFAULT_DELTA_DAYS = 10
DEFAULT_SPINUP_CYCLES = 3
OUTPUT_DESCRIPTION = "Estimated total surface/fire-carrying dry fuel load"


def validate_configuration(
    class_ids: dict[str, int] = LANDCOVER_CLASS_IDS,
    parameters: dict[str, FuelClassParameters] = FUEL_PARAMETERS,
) -> None:
    """Fail early when class mappings or calibration values are inconsistent."""
    if set(class_ids) != set(parameters):
        missing_ids = set(parameters) - set(class_ids)
        missing_parameters = set(class_ids) - set(parameters)
        raise ValueError(
            f"land-cover configuration mismatch; missing IDs={sorted(missing_ids)}, "
            f"missing parameters={sorted(missing_parameters)}"
        )
    if len(set(class_ids.values())) != len(class_ids):
        raise ValueError("land-cover numeric IDs must be unique")
    for name, values in parameters.items():
        if not 0.0 <= values.allocation <= 1.0:
            raise ValueError(f"{name}: allocation must be in [0, 1]")
        if not 0.0 <= values.loss_10day <= 1.0:
            raise ValueError(f"{name}: loss_10day must be in [0, 1]")
        if values.fmax_mg_ha < 0.0:
            raise ValueError(f"{name}: fmax_mg_ha cannot be negative")
