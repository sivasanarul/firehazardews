#!/usr/bin/env python3
"""
Prepare Static Drought Hazard Layer

This script processes the SLIM drought data to create a static drought hazard layer:
1. Loads drought data from slim_drought_2024121_2024305_norm.tif
2. Normalizes to 0-1 using percentile 2 and 98 as min/max
3. Resamples to match LC template raster grid (EPSG:4326, exact dimensions)

The resulting layer represents chronic dryness patterns that contribute to fire hazard.
Output matches the LC raster grid exactly for consistent analysis.
"""

import numpy as np
import rasterio
from pathlib import Path
import argparse
from datetime import datetime
from typing import Tuple

from config import (
    NODATA_VALUE, 
    WORKING_RESOLUTION, 
    DELIVERY_CRS,
    ZAMBIA_EXTENT,
    DRYNESS_OUTPUT_DIR
)


def normalize_with_percentiles(
    data: np.ndarray,
    percentile_min: float = 2.0,
    percentile_max: float = 98.0,
    nodata: float = NODATA_VALUE
) -> Tuple[np.ndarray, dict]:
    """
    Normalize array to 0-1 using percentile-based scaling.
    
    This approach is robust to outliers and provides better contrast
    in the normalized output.
    
    Parameters
    ----------
    data : np.ndarray
        Input array to normalize
    percentile_min : float
        Lower percentile for minimum value (default: 2.0)
    percentile_max : float
        Upper percentile for maximum value (default: 98.0)
    nodata : float
        NoData value to preserve
        
    Returns
    -------
    tuple
        (normalized_array, metadata_dict)
    """
    # Create valid data mask
    valid_mask = (data != nodata) & ~np.isnan(data) & np.isfinite(data)
    
    if not np.any(valid_mask):
        print("  WARNING: No valid data found!")
        return data.copy(), {'valid_pixels': 0}
    
    # Get valid data
    valid_data = data[valid_mask]
    
    # Calculate percentiles
    pmin = np.percentile(valid_data, percentile_min)
    pmax = np.percentile(valid_data, percentile_max)
    
    # Create output array
    normalized = np.full_like(data, nodata, dtype=np.float32)
    
    # Normalize valid pixels
    if pmax > pmin:
        # Clip to percentile range
        clipped = np.clip(valid_data, pmin, pmax)
        # Scale to 0-1
        scaled = (clipped - pmin) / (pmax - pmin)
        normalized[valid_mask] = scaled
    else:
        print(f"  WARNING: pmin ({pmin}) >= pmax ({pmax}), setting all valid to 0.5")
        normalized[valid_mask] = 0.5
    
    # Gather metadata
    metadata = {
        'valid_pixels': np.sum(valid_mask),
        'original_min': np.min(valid_data),
        'original_max': np.max(valid_data),
        'original_mean': np.mean(valid_data),
        'original_std': np.std(valid_data),
        'percentile_min': pmin,
        'percentile_max': pmax,
        'percentile_min_value': percentile_min,
        'percentile_max_value': percentile_max,
        'normalized_min': np.min(normalized[valid_mask]),
        'normalized_max': np.max(normalized[valid_mask]),
        'normalized_mean': np.mean(normalized[valid_mask])
    }
    
    return normalized, metadata


def process_static_drought(
    input_path: str,
    output_path: str,
    temp_dir: Path,
    template_raster: str,
    percentile_min: float = 2.0,
    percentile_max: float = 98.0
) -> None:
    """
    Process static drought data: normalize and resample to standard grid.
    
    Parameters
    ----------
    input_path : str
        Path to input drought raster
    output_path : str
        Path to output standardized drought layer
    temp_dir : Path
        Directory for temporary files
    template_raster : str
        Path to template raster (LC) for matching grid dimensions
    percentile_min : float
        Lower percentile for normalization (default: 2.0)
    percentile_max : float
        Upper percentile for normalization (default: 98.0)
    """
    print("\n" + "=" * 70)
    print("STATIC DROUGHT HAZARD PREPARATION")
    print("=" * 70)
    
    # Create temp directory
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Load and normalize
    print(f"\nStep 1: Loading and normalizing drought data")
    print(f"  Input: {input_path}")
    
    with rasterio.open(input_path) as src:
        data = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        
        print(f"  Original shape: {data.shape}")
        print(f"  Original CRS: {src.crs}")
        print(f"  Original resolution: {src.res}")
    
    # Handle existing nodata
    if profile.get('nodata') is not None:
        data[data == profile['nodata']] = NODATA_VALUE
    
    # Normalize using percentiles
    print(f"\n  Normalizing with percentiles {percentile_min}-{percentile_max}...")
    normalized, metadata = normalize_with_percentiles(
        data, 
        percentile_min=percentile_min,
        percentile_max=percentile_max
    )
    
    # Print statistics
    print("\n  Normalization Summary:")
    print(f"    Valid pixels: {metadata['valid_pixels']:,}")
    print(f"    Original range: [{metadata['original_min']:.4f}, {metadata['original_max']:.4f}]")
    print(f"    Original mean: {metadata['original_mean']:.4f} ± {metadata['original_std']:.4f}")
    print(f"    Percentile {percentile_min}%: {metadata['percentile_min']:.4f}")
    print(f"    Percentile {percentile_max}%: {metadata['percentile_max']:.4f}")
    print(f"    Normalized range: [{metadata['normalized_min']:.4f}, {metadata['normalized_max']:.4f}]")
    print(f"    Normalized mean: {metadata['normalized_mean']:.4f}")
    
    # Step 2: Save temporary normalized file
    temp_normalized = temp_dir / "drought_normalized.tif"
    print(f"\n  Saving normalized data: {temp_normalized}")
    
    profile.update({
        'dtype': 'float32',
        'nodata': NODATA_VALUE,
        'compress': 'lzw'
    })
    
    with rasterio.open(temp_normalized, 'w', **profile) as dst:
        dst.write(normalized, 1)
    
    # Step 3: Read template raster for exact grid specifications
    print(f"\nStep 2: Reading template raster (LC) for grid specifications")
    print(f"  Template: {template_raster}")
    
    with rasterio.open(template_raster) as template:
        template_profile = template.profile.copy()
        template_shape = template.shape
        template_transform = template.transform
        template_crs = template.crs
        template_bounds = template.bounds
        
        print(f"  Template CRS: {template_crs}")
        print(f"  Template shape: {template_shape} (height x width)")
        print(f"  Template resolution: {template.res}")
        print(f"  Template bounds: {template_bounds}")
    
    # Step 4: Resample to match template grid exactly
    print(f"\nStep 3: Resampling to match template grid")
    
    from rasterio.warp import reproject, Resampling
    
    # Read normalized data
    with rasterio.open(temp_normalized) as src:
        src_data = src.read(1)
        
        # Create output array matching template
        out_data = np.full(template_shape, NODATA_VALUE, dtype=np.float32)
        
        # Reproject to template grid
        reproject(
            source=src_data,
            destination=out_data,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=template_transform,
            dst_crs=template_crs,
            resampling=Resampling.bilinear,
            src_nodata=NODATA_VALUE,
            dst_nodata=NODATA_VALUE
        )
    
    # Save output with template profile
    print(f"  Saving output: {output_path}")
    output_profile = template_profile.copy()
    output_profile.update({
        'dtype': 'float32',
        'nodata': NODATA_VALUE,
        'compress': 'lzw',
        'count': 1
    })
    
    with rasterio.open(output_path, 'w', **output_profile) as dst:
        dst.write(out_data, 1)
    
    # Step 5: Verify output
    print(f"\nStep 4: Verifying output")
    with rasterio.open(output_path) as src:
        out_data = src.read(1)
        valid_mask = (out_data != NODATA_VALUE) & ~np.isnan(out_data)
        
        print(f"  Output path: {output_path}")
        print(f"  Output shape: {out_data.shape}")
        print(f"  Output CRS: {src.crs}")
        print(f"  Output resolution: {src.res}")
        print(f"  Output bounds: {src.bounds}")
        print(f"  Valid pixels: {np.sum(valid_mask):,}")
        
        if np.any(valid_mask):
            print(f"  Value range: [{np.min(out_data[valid_mask]):.4f}, {np.max(out_data[valid_mask]):.4f}]")
            print(f"  Mean value: {np.mean(out_data[valid_mask]):.4f}")
    
    print("\n" + "=" * 70)
    print("PROCESSING COMPLETE")
    print("=" * 70)


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Prepare static drought hazard layer from SLIM drought data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  # Process with default settings (percentiles 2-98, using LC template)
  python prepare_static_drought.py
  
  # Use custom percentiles
  python prepare_static_drought.py --percentile-min 5 --percentile-max 95
  
  # Specify custom input/output paths and template
  python prepare_static_drought.py -i /path/to/input.tif -o /path/to/output.tif -t /path/to/template.tif
        """
    )
    
    parser.add_argument(
        '-i', '--input',
        type=str,
        default='/mnt/hddarchive.nfs/slim/hazardmap/data/slim_drought_2024121_2024305_norm.tif',
        help='Path to input drought raster (default: /mnt/hddarchive.nfs/slim/hazardmap/data/slim_drought_2024121_2024305_norm.tif)'
    )
    
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Path to output standardized raster (default: DRYNESS_OUTPUT_DIR/static_drought_hazard.tif)'
    )
    
    parser.add_argument(
        '-t', '--template',
        type=str,
        default='/mnt/hddarchive.nfs/slim/hazardmap/data/SLIM_LC_LandCover_2024_100m_cog.tif',
        help='Path to template raster (LC) for grid specifications (default: SLIM_LC_LandCover_2024_100m_cog.tif)'
    )
    
    parser.add_argument(
        '--percentile-min',
        type=float,
        default=2.0,
        help='Lower percentile for normalization (default: 2.0)'
    )
    
    parser.add_argument(
        '--percentile-max',
        type=float,
        default=98.0,
        help='Upper percentile for normalization (default: 98.0)'
    )
    
    parser.add_argument(
        '--temp-dir',
        type=str,
        default=None,
        help='Directory for temporary files (default: DRYNESS_OUTPUT_DIR/temp)'
    )
    
    args = parser.parse_args()
    
    # Set default output path if not specified
    if args.output is None:
        output_path = DRYNESS_OUTPUT_DIR / "static_drought_hazard.tif"
    else:
        output_path = Path(args.output)
    
    # Set temp directory
    if args.temp_dir is None:
        temp_dir = DRYNESS_OUTPUT_DIR / "temp"
    else:
        temp_dir = Path(args.temp_dir)
    
    # Create output directory
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check input exists
    if not Path(args.input).exists():
        print(f"ERROR: Input file not found: {args.input}")
        return 1
    
    # Check template exists
    if not Path(args.template).exists():
        print(f"ERROR: Template raster not found: {args.template}")
        return 1
    
    # Validate percentiles
    if not (0 <= args.percentile_min < args.percentile_max <= 100):
        print("ERROR: Invalid percentile range. Must have 0 <= min < max <= 100")
        return 1
    
    # Process
    try:
        process_static_drought(
            input_path=args.input,
            output_path=str(output_path),
            temp_dir=temp_dir,
            template_raster=args.template,
            percentile_min=args.percentile_min,
            percentile_max=args.percentile_max
        )
        print(f"\n✓ Successfully created static drought hazard layer: {output_path}")
        return 0
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
