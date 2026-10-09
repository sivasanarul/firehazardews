"""
Configuration file for Static Fire Hazard Map creation for Zambia.

This module contains all configuration parameters, weights, and constants
used in the hazard map calculation.

Based on the methodology:
    FHI = 0.45*FH + 0.25*DH + 0.15*TH + 0.15*RH

Where:
    - FH: Fuel Hazard
    - DH: Chronic Dryness Hazard
    - TH: Terrain Hazard
    - RH: Historical Fire Regime Hazard
"""

from pathlib import Path
from typing import Dict, Tuple
import numpy as np

from slim_fire.geography import (
    DELIVERY_CRS,
    PROCESSING_CRS,
    WORKING_RESOLUTION,
    ZAMBIA_EXTENT,
)

# =============================================================================
# PATHS AND DIRECTORIES
# =============================================================================

# Base directories
BASE_DIR = Path(__file__).parent.parent.parent  # src directory
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output" / "hazardmap"

# Input data paths
LANDCOVER_DIR = DATA_DIR / "landcover"
DEM_DIR = DATA_DIR / "dem"
FIRE_HISTORY_DIR = DATA_DIR / "fire_history"
CLIMATE_DIR = DATA_DIR / "climate"
VEGETATION_DIR = DATA_DIR / "vegetation"

# Output subdirectories
FUEL_OUTPUT_DIR = OUTPUT_DIR / "fuel"
TERRAIN_OUTPUT_DIR = OUTPUT_DIR / "terrain"
DRYNESS_OUTPUT_DIR = OUTPUT_DIR / "dryness"
FIRE_REGIME_OUTPUT_DIR = OUTPUT_DIR / "fire_regime"
FINAL_OUTPUT_DIR = OUTPUT_DIR / "final"

# Create output directories
for dir_path in [FUEL_OUTPUT_DIR, TERRAIN_OUTPUT_DIR, DRYNESS_OUTPUT_DIR,
                 FIRE_REGIME_OUTPUT_DIR, FINAL_OUTPUT_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# =============================================================================
# ANALYSIS GRID PARAMETERS
# =============================================================================

# NoData value
NODATA_VALUE = -9999

# Resampling methods
RESAMPLING_METHODS = {
    'categorical': 'nearest',
    'continuous': 'bilinear',
    'fractional': 'average',
    'binary': 'nearest'
}

# =============================================================================
# TEMPORAL PARAMETERS
# =============================================================================

# Default years for analysis
DEFAULT_LC_YEAR = 2024  # Latest land cover year
DEFAULT_ANALYSIS_YEAR = 2025  # Year for which hazard is calculated
FIRE_HISTORY_START_YEAR = 2001  # Start of MODIS era
CLIMATE_BASELINE_YEARS = (1991, 2020)  # 30-year climate normal

# =============================================================================
# HAZARD COMPONENT WEIGHTS (Level 1)
# =============================================================================

# Main component weights (must sum to 1.0)
COMPONENT_WEIGHTS = {
    'fuel': 0.45,         # Fuel Hazard (FH)
    'dryness': 0.25,      # Chronic Dryness Hazard (DH)
    'terrain': 0.15,      # Terrain Hazard (TH)
    'fire_regime': 0.15   # Historical Fire Regime (RH)
}

# =============================================================================
# FUEL HAZARD SUB-WEIGHTS (Level 2)
# =============================================================================

# FH = 0.45*FT + 0.25*FC + 0.30*FL
FUEL_SUB_WEIGHTS = {
    'fuel_type': 0.45,        # Fuel Type (FT)
    'fuel_continuity': 0.25,  # Fuel Continuity (FC)
    'fuel_load': 0.30         # Fuel Load/Age (FL)
}

# Fuel type relative ratings (0-1 scale)
# Based on Zambia-specific fuel complexes
FUEL_TYPE_RATINGS = {
    'water': 0.0,
    'built_area': 0.0,
    'bare_soil': 0.0,
    'closed_moist_forest': 0.3,
    'cropland': 0.4,
    'closed_woodland': 0.5,
    'shrubland': 0.7,
    'open_miombo_grass': 0.9,  # High hazard due to grass understorey
    'dry_continuous_grass': 1.0,
    'dambo_grassland': 0.8,
    'recently_burned': 0.1
}

# Fuel accumulation parameters
# FL = 1 - exp(-k * Age)
FUEL_ACCUMULATION_RATE = 0.5  # k parameter, controls accumulation speed
MAX_FUEL_AGE_YEARS = 10  # Beyond this, fuel load plateaus

# =============================================================================
# TERRAIN HAZARD SUB-WEIGHTS (Level 2)
# =============================================================================

# TH = 0.70*Slope + 0.30*Northness
TERRAIN_SUB_WEIGHTS = {
    'slope': 0.70,
    'aspect': 0.30
}

# Slope parameters (degrees)
SLOPE_PARAMS = {
    'low_threshold': 2,    # Below this is flat
    'high_threshold': 30   # Above this is very steep
}

# =============================================================================
# CHRONIC DRYNESS SUB-WEIGHTS (Level 2)
# =============================================================================

# DH = 0.50*Drought_Frequency + 0.50*Drought_Severity
DRYNESS_SUB_WEIGHTS = {
    'drought_frequency': 0.50,
    'drought_severity': 0.50
}

# SPI thresholds for drought
SPI_THRESHOLDS = {
    'moderate_drought': -1.0,
    'severe_drought': -1.5
}

# Drought Code (DC) parameters
DC_PARAMS = {
    'high_percentile': 90,  # P90 for frequency analysis
    'seasonal_window': [5, 10]  # May to October (fire season)
}

# =============================================================================
# FIRE REGIME SUB-WEIGHTS (Level 2)
# =============================================================================

# RH = 0.45*BF + 0.35*LSF + 0.20*FRP
FIRE_REGIME_SUB_WEIGHTS = {
    'burn_frequency': 0.45,      # Burn Frequency (BF)
    'late_season_fraction': 0.35, # Late Season Fraction (LSF)
    'frp_intensity': 0.20        # Fire Radiative Power (optional)
}

# Fire season definition (month numbers, 1=Jan)
FIRE_SEASON = {
    'early_season': [4, 5, 6, 7],      # Apr-Jul
    'late_season': [8, 9, 10, 11]      # Aug-Nov (more damaging)
}

# FRP aggregation parameters
FRP_PARAMS = {
    'support_cell_size': 2000,  # meters (2 km)
    'min_detections': 10,       # minimum fires per cell
    'percentile': 95,           # P95 for intensity
    'moving_window_km': 5       # km for smoothing
}

# =============================================================================
# NORMALIZATION PARAMETERS
# =============================================================================

# Percentile-based normalization
# X' = clip((X - P2) / (P98 - P2), 0, 1)
NORM_PERCENTILES = {
    'lower': 2,   # P2
    'upper': 98   # P98
}

# Variables where smaller values = higher hazard (need inversion)
INVERT_HAZARD = [
    'fuel_age',  # Recent fire = low hazard
    'elevation'  # Not typically inverted, but depends on context
]

# =============================================================================
# CLASSIFICATION SCHEMES
# =============================================================================

# Quantile-based classification (for presentation)
QUANTILE_CLASSES = {
    'very_low': (0, 40),
    'low': (40, 65),
    'moderate': (65, 82),
    'high': (82, 94),
    'very_high': (94, 100)
}

# Fixed threshold classification (for temporal comparison)
FIXED_THRESHOLDS = {
    'very_low': (0.0, 0.2),
    'low': (0.2, 0.4),
    'moderate': (0.4, 0.6),
    'high': (0.6, 0.8),
    'very_high': (0.8, 1.0)
}

# Class color scheme (RGB)
CLASS_COLORS = {
    'very_low': (0, 128, 0),      # Green
    'low': (173, 255, 47),        # Yellow-green
    'moderate': (255, 255, 0),    # Yellow
    'high': (255, 165, 0),        # Orange
    'very_high': (255, 0, 0)      # Red
}

# =============================================================================
# VALIDATION PARAMETERS
# =============================================================================

# Validation split
VALIDATION_YEAR = 2025  # Use this year for validation

# Metrics to calculate
VALIDATION_METRICS = [
    'auc',           # Area Under Curve
    'lift',          # Hazard class lift
    'boyce_index',   # Boyce index for presence-only data
    'capture_rate'   # % of fires in high/very high classes
]

# =============================================================================
# QUALITY CONTROL PARAMETERS
# =============================================================================

# Correlation threshold for multicollinearity check
CORRELATION_THRESHOLD = 0.7

# Variance Inflation Factor threshold
VIF_THRESHOLD = 5.0

# Sample size for QC checks
QC_SAMPLE_SIZE = 100000  # Random pixels

# =============================================================================
# DATA SOURCE METADATA
# =============================================================================

DATA_SOURCES = {
    'landcover': {
        'name': 'ESA WorldCover',
        'resolution': 10,  # meters
        'url': 'https://esa-worldcover.org/'
    },
    'dem': {
        'name': 'Copernicus DEM GLO-30',
        'resolution': 30,  # meters
        'url': 'https://spacedata.copernicus.eu/'
    },
    'vegetation_fraction': {
        'name': 'MODIS VCF',
        'resolution': 250,  # meters
        'url': 'https://lpdaac.usgs.gov/products/mod44bv006/'
    },
    'burned_area': {
        'name': 'MODIS MCD64A1',
        'resolution': 500,  # meters
        'url': 'https://lpdaac.usgs.gov/products/mcd64a1v006/'
    },
    'fire_detections': {
        'name': 'FIRMS VIIRS',
        'url': 'https://firms.modaps.eosdis.nasa.gov/'
    },
    'precipitation': {
        'name': 'CHIRPS',
        'resolution': 5000,  # meters
        'url': 'https://www.chc.ucsb.edu/data/chirps'
    }
}

# =============================================================================
# PROCESSING PARAMETERS
# =============================================================================

# Parallel processing
N_WORKERS = 4  # Number of parallel workers
CHUNK_SIZE = 1000  # Rows per chunk for block processing

# Memory management
MAX_MEMORY_MB = 8192  # Maximum memory for operations

# Compression for output files
COMPRESSION = 'LZW'
COMPRESSION_LEVEL = 6

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def validate_weights(weights: Dict[str, float], tolerance: float = 0.01) -> bool:
    """
    Validate that weights sum to 1.0 within tolerance.

    Parameters
    ----------
    weights : dict
        Dictionary of weights
    tolerance : float
        Acceptable deviation from 1.0

    Returns
    -------
    bool
        True if valid, False otherwise
    """
    total = sum(weights.values())
    return abs(total - 1.0) < tolerance


def get_output_filename(component: str, year: int, suffix: str = '') -> str:
    """
    Generate standardized output filename.

    Parameters
    ----------
    component : str
        Component name (fuel, terrain, dryness, fire_regime, final)
    year : int
        Analysis year
    suffix : str
        Optional suffix (e.g., '_normalized', '_classified')

    Returns
    -------
    str
        Filename
    """
    return f"zambia_hazard_{component}_{year}{suffix}.tif"


def print_config_summary():
    """Print summary of configuration."""
    print("=" * 70)
    print("STATIC FIRE HAZARD MAP - CONFIGURATION SUMMARY")
    print("=" * 70)
    print(f"\nWorking Resolution: {WORKING_RESOLUTION} m")
    print(f"Processing CRS: {PROCESSING_CRS}")
    print(f"Delivery CRS: {DELIVERY_CRS}")

    print("\n--- Component Weights (Level 1) ---")
    for comp, weight in COMPONENT_WEIGHTS.items():
        print(f"  {comp:15s}: {weight:.2f}")

    print("\n--- Fuel Sub-Weights (Level 2) ---")
    for sub, weight in FUEL_SUB_WEIGHTS.items():
        print(f"  {sub:20s}: {weight:.2f}")

    print("\n--- Terrain Sub-Weights (Level 2) ---")
    for sub, weight in TERRAIN_SUB_WEIGHTS.items():
        print(f"  {sub:20s}: {weight:.2f}")

    print("\n--- Dryness Sub-Weights (Level 2) ---")
    for sub, weight in DRYNESS_SUB_WEIGHTS.items():
        print(f"  {sub:20s}: {weight:.2f}")

    print("\n--- Fire Regime Sub-Weights (Level 2) ---")
    for sub, weight in FIRE_REGIME_SUB_WEIGHTS.items():
        print(f"  {sub:25s}: {weight:.2f}")

    # Validate weights
    print("\n--- Weight Validation ---")
    if validate_weights(COMPONENT_WEIGHTS):
        print("  ✓ Component weights sum to 1.0")
    else:
        print("  ✗ WARNING: Component weights do not sum to 1.0!")

    if validate_weights(FUEL_SUB_WEIGHTS):
        print("  ✓ Fuel sub-weights sum to 1.0")
    else:
        print("  ✗ WARNING: Fuel sub-weights do not sum to 1.0!")

    print("=" * 70)


if __name__ == "__main__":
    print_config_summary()
