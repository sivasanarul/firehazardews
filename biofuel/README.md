# Dynamic biofuel load model

`slim_fire.biofuel` estimates total surface/fire-carrying dry fuel load from
Copernicus Land Monitoring Service (CLMS) Dry Matter Productivity (DMP), the
SLIM 100 m land-cover map, and the previous timestep's fuel load.

The output is a prototype fuel-pool estimate. It is **not** total above-ground
biomass, tree biomass, carbon stock, fuel moisture, or a field measurement.

## Model overview

For each pixel and DMP timestep, the model calculates:

```text
production_MgHa = DMP_kgHaDay * delta_days / 1000
fuel_input       = A * production_MgHa
capacity         = max(0, 1 - previous_fuel / Fmax)

new_fuel = previous_fuel * (1 - loss)
         + fuel_input * capacity

new_fuel = clip(new_fuel, 0, Fmax)
```

`A`, loss, and `Fmax` depend on land-cover class. Loss values are calibrated
for a ten-day period and are proportionally adjusted for shorter or longer
dekads. Built-up, bare/sparse land, and water always produce zero fuel.

The implementation is NumPy-vectorized and processes GeoTIFF blocks rather
than loading a country-scale raster into memory.

## Inputs

### CLMS DMP

Use the official
[CLMS DMP Global 300 m, 10-daily, version 2](https://land.copernicus.eu/en/products/vegetation/dry-matter-productivity-v2-0-300m)
collection:

```text
CLMS_DMP_GLOBAL_300M_10DAILY_V2
```

The model expects DMP standardized to:

```text
kg dry matter / ha / day
```

It reads raster scale, offset, and nodata metadata. Supported unit metadata or
`--dmp-units` overrides include `kg/ha/day`, `g/m2/day`, `kg/m2/day`, and
`Mg/ha/day`. DMP is bilinearly aligned to the output grid. This interpolation
does not create true 100 m DMP observations.

DMP filenames must contain their nominal end date as `YYYYMMDD`, for example:

```text
DMP_20251010.tif
DMP_20251020.tif
DMP_20251031.tif
```

### Land cover

The production land-cover raster used for Zambia is:

```text
/mnt/hddarchive.nfs/slim/hazardmap/data/SLIM_LC_LandCover_2024_100m_cog.tif
```

Land cover is categorical and is always aligned with nearest-neighbour
resampling. Numeric IDs and model parameters are centralized in `config.py`.

| ID | Land-cover class | A | 10-day loss | Fmax (Mg/ha) |
|---:|---|---:|---:|---:|
| 11 | Tree cover (closed) | 0.35 | 0.015 | 8.0 |
| 12 | Tree cover (open) | 0.50 | 0.018 | 7.0 |
| 21 | Shrubland (closed) | 0.60 | 0.020 | 7.0 |
| 22 | Shrubland (open) | 0.70 | 0.025 | 6.0 |
| 30 | Permanent herbaceous | 0.75 | 0.025 | 7.0 |
| 40 | Periodically herbaceous | 0.75 | 0.030 | 7.0 |
| 50 | Settlement areas | 0.00 | 0.000 | 0.0 |
| 60 | Bare land and sparse vegetation | 0.00 | 0.000 | 0.0 |
| 70 | Water bodies | 0.00 | 0.000 | 0.0 |
| 80 | Wetland | 0.75 | 0.025 | 7.0 |

The wetland parameters are a provisional assumption that mapped Zambian
wetlands are predominantly fire-carrying herbaceous wetlands. They should be
calibrated when field or regional evidence is available.

Unknown land-cover classes produce nodata rather than silently being treated as
zero fuel.

### Previous fuel

Previous fuel must be in `Mg dry matter / ha`. If `--initial-fuel` is omitted,
the first timestep starts at zero. Each output becomes the previous state for
the following timestep.

## Output grid

The default national grid is constructed from `ZAMBIA_EXTENT`,
`PROCESSING_CRS`, and `WORKING_RESOLUTION` in
`slim_fire/hazardmap/config.py`:

```text
CRS:        EPSG:32735
resolution: 100 m
nodata:     -9999
dtype:      Float32
compression: DEFLATE
```

Output files are named:

```text
fuel_load_YYYYMMDD.tif
```

Use `--write-kgm2` to additionally write
`fuel_load_kgm2_YYYYMMDD.tif`, using `1 Mg/ha = 0.1 kg/m2`.

## Running one timestep

Activate the SLIM environment and run:

```bash
conda activate slimfire

python -m slim_fire.biofuel.cli \
  --landcover /mnt/hddarchive.nfs/slim/hazardmap/data/SLIM_LC_LandCover_2024_100m_cog.tif \
  --dmp /mnt/hddarchive.nfs/slim/ews/fuel/.inputs/DMP_20260910.tif \
  --dmp-units kg/ha/day \
  --output-dir /mnt/hddarchive.nfs/slim/ews/fuel
```

Without `--initial-fuel`, this estimates only the first timestep's added fuel
from a zero state; it is not a mature accumulated fuel stock.

## Accumulating a time series

Pass all DMP rasters to one CLI call in any order. The model sorts them by the
date in each filename and carries state forward:

```bash
python -m slim_fire.biofuel.cli \
  --landcover /mnt/hddarchive.nfs/slim/hazardmap/data/SLIM_LC_LandCover_2024_100m_cog.tif \
  --dmp /mnt/hddarchive.nfs/slim/ews/fuel/.inputs/DMP_*.tif \
  --dmp-units kg/ha/day \
  --output-dir /mnt/hddarchive.nfs/slim/ews/fuel
```

The CLI's `--delta-days` value applies to every supplied raster. CLMS calendar
dekads ending on the last day of a month can contain 8, 9, or 11 days. For an
exact calendar integration, invoke the CLI sequentially with the appropriate
`--delta-days` and the preceding output supplied through `--initial-fuel`.

The completed Zambia run from 1 October 2025 through 10 September 2026 used 34
dekads and actual calendar-dekad lengths. Its final output is:

```text
/mnt/hddarchive.nfs/slim/ews/fuel/fuel_load_20260910.tif
```

### Spin-up

`--spinup --spinup-cycles N` repeats the available DMP sequence before writing
the requested series. Spin-up is optional and is not equivalent to supplying a
long, representative historical record.

## Nodata behavior

Output is nodata when land cover, DMP, or the recursive previous state is
nodata. Valid DMP equal to zero is not nodata: it causes existing fuel to decay.

## Extension points

- `apply_disturbance_reset()` currently returns fuel unchanged. It is reserved
  for future harvest, bare-soil, curing, or seasonal reset logic.
- `apply_fire_reset()` optionally applies a consumption fraction to burned
  pixels without forcing all burned fuel to zero.
- `resample_dmp_to_target_grid()` isolates the prototype bilinear interpolation
  so it can later be replaced by a more advanced downscaling method.

No fuel moisture, Nelson/NFDRS model, DEM, terrain, precipitation, biomass map,
live/dead pool separation, grazing, harvest detection, or machine-learning
model is used here.

## Diagnostics

For every timestep the CLI logs:

- mean DMP;
- mean, approximate median, and maximum fuel load;
- mean fuel load for every land-cover class.

GeoTIFF tags record the date, timestep length, units, resampling methods, and
scientific interpretation. Production runs may additionally record the source
collection, accumulation start, initialization, and timestep count.

## Tests

Run the biofuel and Nelson compatibility tests with:

```bash
/home/eouser/miniconda3/envs/slimfire/bin/python -m unittest -v \
  slim_fire.biofuel.test_fuel_model \
  slim_fire.ews.nelsonmodel.test_nfdrs4
```

The tests cover non-combustible classes, wetland behavior, positive
accumulation, zero-DMP decay, capacity and non-negative constraints, class
differentiation, nodata, fire consumption, large vectorized arrays, scale and
offset handling, temporal state carry-over, output metadata, and compatibility
with the Nelson-model tests.
