#!/usr/bin/env python3
"""
Helper to derive vegetation fractions from SLIM landcover.

When MODIS VCF is not available, estimates tree/herbaceous/bare fractions
from SLIM landcover classes.
"""

import numpy as np
import rasterio
from pathlib import Path
from typing import Dict, Tuple


# SLIM LC class to vegetation fraction mapping
# Based on SLIM LC 2024 class definitions for Zambia
# Format: {lc_class: {'tree': %, 'herb': %, 'bare': %}}
SLIM_LC_TO_VEG_FRACTIONS = {
    # Tree cover classes
    11: {'tree': 75, 'herb': 20, 'bare': 5},   # Tree cover (closed) - dense canopy
    12: {'tree': 45, 'herb': 45, 'bare': 10},  # Tree cover (open) - scattered trees with grass
    
    # Shrubland classes
    21: {'tree': 5, 'herb': 80, 'bare': 15},   # Shrubland (closed) - dense shrubs/bushes
    22: {'tree': 5, 'herb': 60, 'bare': 35},   # Shrubland (open) - sparse shrubs
    
    # Herbaceous/grassland classes
    30: {'tree': 0, 'herb': 95, 'bare': 5},    # Permanent herbaceous - continuous grassland
    40: {'tree': 0, 'herb': 75, 'bare': 25},   # Periodically herbaceous - seasonal grassland
    
    # Built-up and settlement
    50: {'tree': 5, 'herb': 20, 'bare': 75},   # Settlement areas - buildings, roads
    
    # Bare and sparse vegetation
    60: {'tree': 0, 'herb': 15, 'bare': 85},   # Bare land and sparse vegetation
    
    # Water
    70: {'tree': 0, 'herb': 0, 'bare': 0},     # Water Bodies - permanent water
    
    # Wetlands
    80: {'tree': 0, 'herb': 90, 'bare': 10},   # Wetland - dambos with herbaceous vegetation
}


def derive_veg_fractions_from_landcover(
    landcover_path: Path,
    output_path: Path,
    lc_mapping: Dict[int, Dict[str, int]] = None
) -> Path:
    """
    Derive vegetation fractions (tree/herb/bare) from landcover.
    
    Parameters
    ----------
    landcover_path : Path
        Path to SLIM landcover file
    output_path : Path
        Output path for 3-band vegetation fraction raster
        Band 1: Tree cover (%)
        Band 2: Herbaceous cover (%)
        Band 3: Bare cover (%)
    lc_mapping : dict, optional
        Custom LC class to vegetation fraction mapping
        
    Returns
    -------
    Path
        Path to created vegetation fraction file
    """
    print("\n" + "=" * 70)
    print("DERIVING VEGETATION FRACTIONS FROM LANDCOVER")
    print("=" * 70)
    print(f"Input: {landcover_path}")
    print(f"Output: {output_path}")
    
    if lc_mapping is None:
        lc_mapping = SLIM_LC_TO_VEG_FRACTIONS
    
    # Read landcover
    with rasterio.open(landcover_path) as src:
        lc_data = src.read(1)
        profile = src.profile.copy()
        nodata = src.nodata or 0
    
    # Initialize output bands
    tree_cover = np.full_like(lc_data, 0, dtype=np.uint8)
    herb_cover = np.full_like(lc_data, 0, dtype=np.uint8)
    bare_cover = np.full_like(lc_data, 0, dtype=np.uint8)
    
    # Map LC classes to vegetation fractions
    classes_found = []
    classes_missing = []
    
    for lc_class in np.unique(lc_data):
        if lc_class == nodata or lc_class == 0:
            continue
        
        if lc_class in lc_mapping:
            mask = (lc_data == lc_class)
            fracs = lc_mapping[lc_class]
            tree_cover[mask] = fracs['tree']
            herb_cover[mask] = fracs['herb']
            bare_cover[mask] = fracs['bare']
            classes_found.append(lc_class)
        else:
            classes_missing.append(lc_class)
            # Default: assume mixed
            mask = (lc_data == lc_class)
            tree_cover[mask] = 20
            herb_cover[mask] = 60
            bare_cover[mask] = 20
    
    # Handle nodata
    nodata_mask = (lc_data == nodata) | (lc_data == 0)
    tree_cover[nodata_mask] = 255  # Use 255 as nodata for uint8
    herb_cover[nodata_mask] = 255
    bare_cover[nodata_mask] = 255
    
    # Update profile for 3-band output
    profile.update({
        'count': 3,
        'dtype': 'uint8',
        'nodata': 255,
        'compress': 'lzw',
        'tiled': True
    })
    
    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(tree_cover, 1)
        dst.write(herb_cover, 2)
        dst.write(bare_cover, 3)
        
        dst.set_band_description(1, 'Percent Tree Cover')
        dst.set_band_description(2, 'Percent Herbaceous Cover')
        dst.set_band_description(3, 'Percent Bare/NonVegetated')
        
        dst.update_tags(
            derived_from=str(landcover_path),
            method='SLIM_LC_to_VegFractions',
            note='Approximate fractions derived from landcover classes'
        )
    
    print(f"\n✓ Vegetation fractions created")
    print(f"  Classes mapped: {len(classes_found)}")
    if classes_missing:
        print(f"  ⚠ Unknown classes (using default): {classes_missing}")
    
    # Print statistics
    valid_mask = ~nodata_mask
    print(f"\n  Tree cover:  {tree_cover[valid_mask].mean():.1f}% ± {tree_cover[valid_mask].std():.1f}%")
    print(f"  Herb cover:  {herb_cover[valid_mask].mean():.1f}% ± {herb_cover[valid_mask].std():.1f}%")
    print(f"  Bare cover:  {bare_cover[valid_mask].mean():.1f}% ± {bare_cover[valid_mask].std():.1f}%")
    
    print("\n" + "=" * 70)
    
    return output_path


def main():
    """Command-line interface for deriving vegetation fractions."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Derive vegetation fractions from SLIM landcover"
    )
    parser.add_argument(
        '--landcover',
        type=Path,
        default='/mnt/hddarchive.nfs/slim/hazardmap/data/SLIM_LC_LandCover_2024_100m_cog.tif',
        help='Input landcover file'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default='/mnt/hddarchive.nfs/slim/hazardmap/data/derived_veg_fractions.tif',
        help='Output vegetation fractions file'
    )
    
    args = parser.parse_args()
    
    derive_veg_fractions_from_landcover(args.landcover, args.output)


if __name__ == "__main__":
    main()
