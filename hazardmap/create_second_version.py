#!/usr/bin/env python3
"""
Create First Version of Hazard Map with Available Data

This script creates a preliminary hazard map using only the available layers:
- Fuel Type & Continuity (from landcover and vegetation fractions)
- Terrain (from existing slope and aspect files)

Components not yet included:
- Fuel Load (requires burn history)
- Dryness (requires SPI timeseries)
- Fire Regime (requires burn history)
"""

import numpy as np
import rasterio
from pathlib import Path
import argparse
from datetime import datetime

from normalization import normalize_array, print_normalization_summary
from config import NODATA_VALUE, WORKING_RESOLUTION, PROCESSING_CRS


def load_and_normalize_raster(filepath, component_name):
    """Load a raster and normalize it to 0-1."""
    print(f"\n  Loading {component_name}: {filepath}")
    
    with rasterio.open(filepath) as src:
        data = src.read(1).astype(np.float32)
        profile = src.profile.copy()
    
    # Replace any existing nodata with standard
    if profile.get('nodata') is not None:
        data[data == profile['nodata']] = NODATA_VALUE
    
    # Normalize
    normalized, norm_meta = normalize_array(data)
    
    valid_mask = (normalized != NODATA_VALUE) & ~np.isnan(normalized)
    if np.sum(valid_mask) > 0:
        print(f"    Range: [{np.min(normalized[valid_mask]):.3f}, {np.max(normalized[valid_mask]):.3f}]")
        print(f"    Mean: {np.mean(normalized[valid_mask]):.3f}")
    
    return normalized, profile


def calculate_fuel_type_simple(landcover_path, veg_fractions_path):
    """Calculate simplified fuel type from landcover and vegetation fractions."""
    print("\n=== Calculating Fuel Type ===")
    
    # Load landcover
    with rasterio.open(landcover_path) as src:
        lc = src.read(1)
        profile = src.profile.copy()
    
    # Initialize fuel type
    fuel_type = np.zeros_like(lc, dtype=np.float32)
    
    # SLIM Land Cover fuel ratings (Zambia-specific)
    # Higher values = more flammable vegetation
    fuel_ratings = {
        11: 0.3,   # Tree cover (closed) - low hazard, minimal grass
        12: 0.7,   # Tree cover (open) - high hazard, grass understory
        21: 0.6,   # Shrubland (closed) - moderate-high, fine fuels
        22: 0.8,   # Shrubland (open) - high hazard, fine woody + grass
        30: 0.95,  # Permanent herbaceous - very high (continuous grass)
        40: 0.90,  # Periodically herbaceous - high (seasonal grass)
        50: 0.1,   # Settlement areas - very low hazard
        60: 0.2,   # Bare land and sparse vegetation - low hazard
        70: 0.0,   # Water Bodies - no hazard
        80: 0.0,   # Other (if exists) - no hazard
        0: 0.0,    # No data
    }
    
    # Apply ratings
    for lc_class, rating in fuel_ratings.items():
        fuel_type[lc == lc_class] = rating
    
    # Refine with vegetation fractions if available
    if veg_fractions_path and Path(veg_fractions_path).exists():
        print("  Refining with vegetation fractions...")
        with rasterio.open(veg_fractions_path) as src_veg:
            veg = src_veg.read()  # Bands: 1=Tree, 2=Herbaceous, 3=Bare
            
        if veg.shape[0] >= 2:
            tree_frac = veg[0] / 100.0      # Band 1: Tree cover
            herbaceous_frac = veg[1] / 100.0  # Band 2: Herbaceous (grass/forbs)
            
            # Refine tree cover classes based on grass understory
            # Class 12 (open tree) can vary greatly in grass cover
            open_tree_mask = (lc == 12)
            if np.sum(open_tree_mask) > 0:
                # Open woodland: 0.4-0.9 depending on grass layer
                fuel_type[open_tree_mask] = 0.4 + 0.5 * herbaceous_frac[open_tree_mask]
            
            # Refine closed tree cover - generally lower hazard but can have grass gaps
            closed_tree_mask = (lc == 11)
            if np.sum(closed_tree_mask) > 0:
                # Closed woodland: 0.2-0.5 depending on any grass in gaps
                fuel_type[closed_tree_mask] = 0.2 + 0.3 * herbaceous_frac[closed_tree_mask]
            
            # Refine shrubland based on grass component
            shrubland_mask = (lc == 21) | (lc == 22)
            if np.sum(shrubland_mask) > 0:
                # Shrubland with grass: 0.5-0.95 depending on grass density
                fuel_type[shrubland_mask] = 0.5 + 0.45 * herbaceous_frac[shrubland_mask]
            
            # Refine herbaceous classes (ensure high values are maintained)
            herbaceous_mask = (lc == 30) | (lc == 40)
            if np.sum(herbaceous_mask) > 0:
                # Pure grassland: 0.75-1.0 depending on density
                fuel_type[herbaceous_mask] = 0.75 + 0.25 * herbaceous_frac[herbaceous_mask]
    
    return fuel_type, profile


def calculate_fuel_continuity(veg_fractions_path):
    """Calculate fuel continuity from vegetation fractions.
    
    Uses herbaceous (grass) cover as the primary indicator of fine fuel connectivity.
    In savannas, continuous grass layer is the main driver of fire spread.
    
    Band order: 1=Tree, 2=Herbaceous, 3=Bare
    """
    print("\n=== Calculating Fuel Continuity ===")
    
    with rasterio.open(veg_fractions_path) as src:
        veg = src.read()  # Bands: 1=Tree, 2=Herbaceous, 3=Bare
        profile = src.profile.copy()
    
    if veg.shape[0] < 2:
        raise ValueError("Vegetation fractions file needs at least 2 bands (tree, herbaceous)")
    
    # Band 2 = Herbaceous cover (grass/forbs) - primary fine fuel
    herbaceous_frac = veg[1] / 100.0  # Band 2 (index 1)
    
    # Band 3 = Bare ground
    if veg.shape[0] >= 3:
        bare_frac = veg[2] / 100.0  # Band 3 (index 2)
        
        # In sparse grass areas, some "bare" might have scattered grass
        # Add a small contribution from non-dense bare areas
        sparse_grass_contribution = np.where(
            (herbaceous_frac > 0.05) & (herbaceous_frac < 0.3) & (bare_frac > 0.3),
            0.1 * bare_frac,  # 10% of bare might have sparse grass
            0.0
        )
        
        continuity = herbaceous_frac + sparse_grass_contribution
    else:
        continuity = herbaceous_frac
    
    # Clip to valid range
    continuity = np.clip(continuity, 0, 1).astype(np.float32)
    
    print(f"    Herbaceous cover range: {np.nanmin(herbaceous_frac):.3f} - {np.nanmax(herbaceous_frac):.3f}")
    print(f"    Mean herbaceous: {np.nanmean(herbaceous_frac):.3f}")
    print(f"    Fuel continuity range: {np.nanmin(continuity):.3f} - {np.nanmax(continuity):.3f}")
    
    return continuity, profile


def calculate_terrain_from_existing(slope_path, aspect_path, weights=(0.7, 0.3)):
    """Calculate terrain hazard from existing slope and aspect files."""
    print("\n=== Calculating Terrain Hazard ===")
    
    # Load and normalize slope
    slope_norm, profile = load_and_normalize_raster(slope_path, "Slope")
    
    # Load aspect and convert to northness
    print(f"\n  Loading Aspect: {aspect_path}")
    with rasterio.open(aspect_path) as src:
        aspect = src.read(1).astype(np.float32)
    
    # Convert aspect to northness (Southern Hemisphere)
    # North (0°) gets highest value (1.0), South (180°) gets lowest (0.0)
    aspect_rad = np.deg2rad(aspect)
    northness = (np.cos(aspect_rad) + 1) / 2.0
    
    # Handle flat areas (aspect = -1 or nodata)
    flat_mask = (aspect < 0) | (aspect == NODATA_VALUE)
    northness[flat_mask] = 0.5  # Neutral
    
    # Normalize northness
    northness_norm, _ = normalize_array(northness)
    
    print(f"    Northness range: [{np.nanmin(northness_norm):.3f}, {np.nanmax(northness_norm):.3f}]")
    
    # Combine terrain components
    terrain_hazard = weights[0] * slope_norm + weights[1] * northness_norm
    
    return terrain_hazard, profile

def calculate_drought_from_existing(drought_path):
    print("\n=== Calculating Drought Hazard ===")
    # Load aspect and convert to northness
    print(f"\n  Loading Aspect: {drought_path}")
    with rasterio.open(drought_path) as src:
        drought = src.read(1).astype(np.float32)
    print(f"    Drought range: [{np.nanmin(drought):.3f}, {np.nanmax(drought):.3f}]")
    return drought, None

def combine_hazards(fuel_type, fuel_continuity, terrain_hazard, drought_hazard, profile, 
                     output_path, analysis_year, landcover_path):
    """Combine available hazard components."""
    print("\n" + "=" * 70)
    print("COMBINING HAZARD COMPONENTS (First Version)")
    print("=" * 70)
    
    # Normalize each component
    fuel_type_norm, _ = normalize_array(fuel_type)
    fuel_cont_norm, _ = normalize_array(fuel_continuity)
    terrain_norm, _ = normalize_array(terrain_hazard)
    drought_norm, _ = normalize_array(drought_hazard)
    
    # For first version, use simplified fuel hazard (no fuel load component)
    # Fuel hazard = 0.60 * fuel_type + 0.40 * fuel_continuity
    fuel_hazard = 0.6 * fuel_type_norm + 0.4 * fuel_cont_norm
    
    # Combine fuel and terrain
    # Weights: Fuel 70%, Terrain 30% (adjusted for missing components)
    print("\n  Component Weights (adjusted for available data):")
    print(f"    Fuel:    0.55")
    print(f"    Terrain: 0.20")
    print(f"    Drought: 0.25")
    
    final_hazard = 0.55 * fuel_hazard + 0.2 * terrain_norm + 0.25 * drought_norm
    
    # Handle nodata
    nodata_mask = (
        (fuel_type_norm == NODATA_VALUE) |
        (fuel_cont_norm == NODATA_VALUE) |
        (terrain_norm == NODATA_VALUE) |
        np.isnan(fuel_type_norm) |
        np.isnan(fuel_cont_norm) |
        np.isnan(terrain_norm)
    )
    final_hazard[nodata_mask] = NODATA_VALUE
    
    # Statistics
    valid_mask = (final_hazard != NODATA_VALUE) & ~np.isnan(final_hazard)
    
    print(f"\n  Final Hazard Statistics:")
    print(f"    Range:  [{np.min(final_hazard[valid_mask]):.3f}, {np.max(final_hazard[valid_mask]):.3f}]")
    print(f"    Mean:   {np.mean(final_hazard[valid_mask]):.3f}")
    print(f"    Median: {np.median(final_hazard[valid_mask]):.3f}")
    print(f"    Std:    {np.std(final_hazard[valid_mask]):.3f}")
    
    # Save continuous hazard
    profile.update(dtype=rasterio.float32, count=1, nodata=NODATA_VALUE, compress='lzw')
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(final_hazard, 1)
        dst.update_tags(
            product='static_fire_hazard_index_v1_preliminary',
            version='1.0_preliminary',
            analysis_year=analysis_year,
            calculation_date=datetime.now().isoformat(),
            components='fuel_type, fuel_continuity, terrain_slope, terrain_aspect',
            missing_components='fuel_load, dryness_hazard, fire_regime_hazard',
            weight_fuel=0.70,
            weight_terrain=0.30,
            description='First version hazard map with available data only'
        )
    
    print(f"\n  ✓ Hazard map saved: {output_path}")
    
    # Apply landcover nodata mask
    print("\n=== Applying Landcover Nodata Mask ===")
    print(f"  Reading landcover: {landcover_path}")
    with rasterio.open(landcover_path) as lc_src:
        lc_data = lc_src.read(1)
        lc_nodata = lc_src.nodata
    
    # Create mask where landcover is nodata
    if lc_nodata is not None:
        lc_nodata_mask = (lc_data == lc_nodata) | np.isnan(lc_data)
    else:
        # If no nodata value defined, check for common nodata values
        lc_nodata_mask = (lc_data == 0) | (lc_data == 255)
    
    # Apply mask to final_hazard
    pixels_masked = np.sum(lc_nodata_mask & (final_hazard != NODATA_VALUE))
    final_hazard = np.clip(final_hazard, 0, 1)
    final_hazard[lc_nodata_mask] = NODATA_VALUE
    
    print(f"  Masked {pixels_masked:,} pixels where landcover is nodata")
    
    # Re-save the masked hazard map
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(final_hazard, 1)
        dst.update_tags(
            product='static_fire_hazard_index_v1_preliminary',
            version='1.0_preliminary',
            analysis_year=analysis_year,
            calculation_date=datetime.now().isoformat(),
            components='fuel_type, fuel_continuity, terrain_slope, terrain_aspect',
            missing_components='fuel_load, dryness_hazard, fire_regime_hazard',
            weight_fuel=0.70,
            weight_terrain=0.30,
            description='First version hazard map with available data only',
            landcover_masked='true'
        )
    
    print(f"  ✓ Masked hazard map saved: {output_path}")
    
    # Update valid mask for classification
    valid_mask = (final_hazard != NODATA_VALUE) & ~np.isnan(final_hazard)
    
    # Create simple classification
    create_classified_map(final_hazard, profile, output_path, valid_mask)
    
    return final_hazard


def create_classified_map(hazard_array, profile, base_output_path, valid_mask):
    """Create quantile-based classified hazard map."""
    print("\n=== Creating Classified Map ===")
    
    valid_data = hazard_array[valid_mask]
    
    # 5-class quantile classification
    quantiles = np.percentile(valid_data, [0, 20, 40, 60, 80, 100])
    
    classified = np.full_like(hazard_array, 255, dtype=np.uint8)
    
    for i in range(5):
        if i == 0:
            mask = valid_mask & (hazard_array >= quantiles[i]) & (hazard_array <= quantiles[i+1])
        else:
            mask = valid_mask & (hazard_array > quantiles[i]) & (hazard_array <= quantiles[i+1])
        classified[mask] = i + 1
    
    classified[~valid_mask] = 0
    
    # Save classified map
    classified_path = str(base_output_path).replace('_continuous.tif', '_classified.tif')
    
    profile_class = profile.copy()
    profile_class.update(dtype=rasterio.uint8, nodata=0, compress='lzw')
    
    with rasterio.open(classified_path, 'w', **profile_class) as dst:
        dst.write(classified, 1)
        dst.update_tags(
            class_1='Very Low (0-20th percentile)',
            class_2='Low (20-40th percentile)',
            class_3='Moderate (40-60th percentile)',
            class_4='High (60-80th percentile)',
            class_5='Very High (80-100th percentile)'
        )
    
    print(f"  ✓ Classified map saved: {classified_path}")
    
    # Print class statistics
    print("\n  Class Distribution:")
    for i in range(1, 6):
        count = np.sum(classified == i)
        pct = 100 * count / np.sum(valid_mask)
        print(f"    Class {i}: {count:,} pixels ({pct:.1f}%)")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description='Create first version hazard map with available data'
    )
    parser.add_argument('--landcover', required=True, help='Land cover raster')
    parser.add_argument('--veg-fractions', required=True, help='Vegetation fractions raster')
    parser.add_argument('--slope', required=True, help='Slope raster')
    parser.add_argument('--aspect', required=True, help='Aspect raster')
    parser.add_argument('--drought', required=True, help='Drought raster')
    parser.add_argument('--output', required=True, help='Output hazard map path')
    parser.add_argument('--analysis-year', type=int, default=2025, help='Analysis year')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("CREATING FIRST VERSION HAZARD MAP")
    print("=" * 70)
    print(f"\nInputs:")
    print(f"  Land Cover:  {args.landcover}")
    print(f"  Veg Fractions: {args.veg_fractions}")
    print(f"  Slope: {args.slope}")
    print(f"  Aspect: {args.aspect}")
    print(f"  Drought: {args.drought}")
    print(f"\nOutput: {args.output}")
    print(f"Analysis Year: {args.analysis_year}")
    
    # Calculate components
    fuel_type, profile = calculate_fuel_type_simple(args.landcover, args.veg_fractions)
    fuel_continuity, _ = calculate_fuel_continuity(args.veg_fractions)
    terrain_hazard, _ = calculate_terrain_from_existing(args.slope, args.aspect)
    drought_hazard, _ = calculate_drought_from_existing(args.drought)
    
    # Combine and save
    final_hazard = combine_hazards(
        fuel_type, fuel_continuity, terrain_hazard, drought_hazard,
        profile, args.output, args.analysis_year, args.landcover
    )


    
    print("\n" + "=" * 70)
    print("✓ FIRST VERSION COMPLETE")
    print("=" * 70)
    print("\nNote: This is a preliminary version using available data.")
    print("Future versions will include:")
    print("  - Fuel load/age (requires burn history)")
    print("  - Chronic dryness hazard (requires SPI timeseries)")
    print("  - Fire regime hazard (requires burn history)")


if __name__ == "__main__":
    main()
