# SLIM Fire API

This starter backend downloads NASA FIRMS active-fire detections in five-day
chunks, prefers Standard Processing where it is available, caches upstream CSV
responses, and returns normalized GeoJSON.

```bash
conda env create -f slim_fire/environment.yaml
conda activate slimfire
uvicorn slim_fire.fire_api:app --reload
```

Before starting the API, replace `YOUR_FIRMS_MAP_KEY` in `slim_fire/.env` with
your FIRMS key. Both the API and the original download script load that file
automatically.

`FIRMS_DOWNLOAD_WORKERS` optionally controls concurrent upstream downloads and
defaults to four. Responses are cached, so an identical repeated request does
not download the same FIRMS chunks again.

Example request:

```text
http://localhost:8000/api/fires?bbox=21.9,-18.1,33.8,-8.2&start=2025-07-01&end=2025-07-15&sensor=all
```

Open `http://localhost:8000/map` for an interactive Leaflet map. The root URL opens the scenario landing page. Choose `15-day event polygons` to cluster nearby
detections into buffered event hulls. These polygons summarize detection
clusters and must not be interpreted as measured burned-area perimeters.

Follow the Map settings guide to choose an area, observation period, and data
layers, then select **Explore map** (or **Update map** after the first load).
Layer checkboxes select the overlays for the next
update; opacity sliders apply immediately. Fire activity and burned area can
be selected independently to overlay FIRMS detections (or 15-day event polygons)
with the time-filtered GWIS MODIS & VIIRS NRT burned-area WMS.
The browser requests same-origin tile URLs; FastAPI proxies and
caches the upstream PNG tiles under `.cache/gwis_burned_area`. Set
`GWIS_CACHE_DIR` to override that location.

Enable `Land cover` to select the SLIM COG whose year is nearest the midpoint
of the requested start/end interval. Available configured years are 2000,
2005, 2010, 2015, 2020, and 2024; an exact tie selects the newer year. The
published objects are 30 m through 2015 and 10 m for 2020/2024. FastAPI reads
and colorizes the remote categorical COG with rio-tiler, then caches PNG tiles
under `.cache/landcover`. Set `LANDCOVER_CACHE_DIR` to override it.
The land-cover opacity slider changes the displayed opacity from 0–100%
without requesting new tiles.

Enable `Vegetation fuel` to overlay the GWIS `fuel_map` WMS layer. The API
validates the layer name and caches valid PNG responses under
`.cache/gwis_fuel`. Set `FUEL_CACHE_DIR` to override that location.

Land-cover class values, labels, and RGBA colors are read from
`SLIM_LC_LandCover_legend.qml`. The QML uses nearest-neighbour categorical
rendering; rio-tiler likewise reads categorical tiles without interpolating
class values. `/api/land-cover/style` exposes the parsed palette as JSON.

Valid sensor values are `all`, `modis`, `viirs_snpp`, `viirs_noaa20`, and
`viirs_noaa21`. Set `FIRMS_CACHE_DIR` and `FIRE_DATA_DIR` to override the
default `.cache/firms` and `data/fires` directories.

## Monitoring workspace

The `/map` workspace uses a full-viewport map with controls docked to the left
and a full-width top header containing context, scenario links, and actions.
The menu toggle lives in the control card's heading, which stays visible when the
card is collapsed; settings and results are retained. On small
screens, controls open as a bottom sheet. **Use current map extent** selects a
custom area without entering coordinates; geographic coordinates remain
available under the area settings. Recent-date shortcuts use the browser's
local calendar. The default historical period remains July 1–15, 2025.

The first visit opens a guided setup: **Area → Dates → Layers**, with one step
visible at a time. Continue validates the current step; Back and completed step
links retain the chosen settings. The default fire layer can be loaded with
**Explore map**; environmental layers sit under an optional section. Pressing
Enter on an early step advances the guide without downloading observations.
On mobile, the collapsed **Map workspace** menu opens the guide and resumes the current step.

Warm light panels, forest-green text, and orange actions match the landing page.
Sensor options and geographic coordinates are collapsed by default; extra
layer controls appear when their layer is selected. After fire data loads,
the **Results** tab, export action, and map legend become available. Results
holds the observation
summary and lists up to 100 observations; selecting one opens its map
popup. **Export GeoJSON** downloads the complete loaded fire collection, even
if filters have subsequently changed. No upstream observation requests run
until the user updates the map. Individual source failures are reported without
discarding successful layers. Precipitation uses the selected window ending
on the observation period's end date.

UI files live in `static/map.html`, `static/map.css`, and `static/map.js` and are
served by FastAPI. No frontend build is required. Leaflet and its heatmap plugin
load from their existing CDN; OpenStreetMap supplies basemap tiles.

`static/slim-logo.webp` is an unchanged copy of the supplied official SLIM logo.
It is displayed at its original aspect ratio, including the complete lower
ribbon, with no cropping or recreation.

DOM integration tests mock Leaflet and the data endpoints and do not download
upstream observations. Run them with Node.js 18 or newer:

```bash
npm install --prefix tests/ui
npm test --prefix tests/ui
```

These checks cover loading, validation, partial failures, result navigation,
opacity, empty states, guided navigation, and mobile control visibility. They do not replace a
visual browser review.

## Scenario landing page

The root URL (`/`) introduces four workflows from the EWS brief. Each opens the
existing map design with scenario-specific guidance and layer defaults:

- `/map?scenario=prevention`: Prevention & preparedness; 2025 hazard index and districts.
- `/map?scenario=response`: Detection; FIRMS detections and districts, preserving the original workflow.
- `/map?scenario=prediction`: Prediction; burned area and landscape context, with propagation and risk prediction clearly marked as planned.
- `/map?scenario=restoration`: Restoration & adaptation; burned area, land cover and districts.

`/map` and unknown scenario values default to detection. The header's
**All scenarios** link returns to the landing page. No observation data loads until
**Explore map** is selected. FIRMS detections and event polygons are available only
in scenario 2. Hazard is available in scenario 1; districts and environmental
context layers are shared across scenarios.

FIRMS detection points use proportional symbols: point area scales with FRP in
MW (with minimum and maximum display sizes), while colour uses a Turbo gradient
to show observation date within the selected period. Detection provides quick
UTC date windows for the last 24 hours, 48 hours, 7, 10, or 30 days, plus this
month, the previous month, a 2026 month picker, and a custom range. The current
month ends today and future dates are disabled. The legend derives up to six
date intervals from the active range, with cooler colours for earlier
observations and warmer colours for later observations.
Popups retain the exact FRP and observation timestamp. After individual
detections load, the Results panel provides a logarithmic minimum-FRP slider
spanning the lowest through highest observed MW value, with the median shown
for context. The logarithmic mapping gives the common low-intensity detections
useful slider space even when a few extreme FRP values are present. It filters
map points, the result list, counts, and legend immediately without another FIRMS request. Event
polygons are not affected, and GeoJSON export retains the complete originally
loaded collection. A second minimum-date slider filters the same loaded points
from the earliest through latest observation date. FRP and date thresholds are
combined, so adjusting one retains the other.

FIRMS requests can be restricted to a polygon or multipolygon stored in a
GeoPackage. Configure `.env` with:

```dotenv
FIRMS_BOUNDARY_ENABLED=true
FIRMS_BOUNDARY_GPKG=greennexus_ews.gpkg
FIRMS_BOUNDARY_LAYER=landscape_slim_updated
```

Relative paths are resolved from the application directory. When enabled, the
backend intersects the requested extent with the boundary bounds before calling
FIRMS, then removes detections outside the actual geometry. Missing or invalid
boundary configuration fails the request instead of returning unfiltered data.
Set `FIRMS_BOUNDARY_ENABLED=false` to retain the default rectangular region.

### Copernicus Data Space authentication

CDSE access uses a backend-only OAuth2 client-credentials flow. Create an OAuth
client in the Sentinel Hub dashboard and configure `.env`:

```dotenv
CDSE_CLIENT_ID=your-client-id
CDSE_CLIENT_SECRET=your-client-secret
CDSE_TOKEN_URL=https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
CDSE_API_URL=https://sh.dataspace.copernicus.eu
CDSE_HTTP_TIMEOUT=30
CDSE_TOKEN_REFRESH_MARGIN=300
CDSE_PROCESS_TIMEOUT=90
CDSE_IMAGE_CACHE_DIR=.cache/cdse
```

The server reuses access tokens, refreshes them five minutes before expiry,
serializes concurrent refreshes, and retries an API request once after HTTP 401.
Credentials and tokens are never returned by the status endpoint. Test a real
credential pair without printing the token:

```bash
/home/eouser/miniconda3/envs/slimfire/bin/python -m cdse_client
```

`GET /api/cdse/auth-status` reports only whether credentials are configured,
whether this process currently holds a fresh token, the safe API base URL, and
the token expiry time. `GET /api/cdse/auth-status?verify=true` performs a live,
cached token exchange and still returns no credentials or token. Restart
Uvicorn after changing credentials.

In Detection, selecting an individual FIRMS point searches satellite
acquisitions within 5 km, from 10 days before through 15 days after that FIRMS
observation. The viewer searches Sentinel-2 L2A and Landsat 8/9 Collection 2
Level-1 together. A bottom map timeline marks every date with available RGB
imagery using separate S2, L8, and L9 markers; more than one mission can appear
on the same date. Selecting a marker renders that collection and highlights its
Sentinel-2 or Landsat source control. The source controls are shortcuts to the
closest available acquisition from that mission. The closest acquisition
overall is selected initially. The results panel provides platform and scene-cloud metadata,
True-colour/SWIR controls, and image opacity. Sentinel-2 SWIR uses B12/B11/B04;
Landsat OLI SWIR uses B07/B06/B04. The default image uses a 5 km radius
(approximately 10 × 10 km) and a Results-panel coverage slider can expand it
to a 50 km radius (approximately 100 × 100 km) without changing the selected
acquisition or display style. Every extent is rendered at 768 × 768 pixels, so
larger areas trade spatial detail for coverage. Images are rendered on demand
by the CDSE Process API beneath the FIRMS points and cached as PNGs; a
full Landsat scene is not downloaded. The backend routes are
`GET /api/cdse/acquisitions` and `GET /api/cdse/image`; OAuth credentials remain
server-side for both routes. Their optional `source` query parameter accepts
`sentinel-2` (the default), `landsat-8-9`, or `all` for acquisition searches;
image requests accept one concrete collection.

Both pages load `static/design.css` for shared colours, typography, scenario accents,
and responsive refinements. `static/floating-map.css` adds the floating layout and
light control surfaces while retaining the original map styles in `map.css`.
Prepare / Respond / Restore links open another scenario with
its default settings; the current scenario is marked for screen readers. Future
modules appear after the map setup, and SLIM branding sits at the bottom of each page.

The shared catalogue in `static/scenarios.js` supplies landing cards and map
roadmaps. Greyed roadmap entries are informational and cannot load data. **Planned**
includes the brief's future prototype modules and integrations still needed by this
viewer (risk assessment, fire propagation).
**Concept** covers ground sensors and pseudo emission. Risk assessment is **Planned**.
The GWIS near-real-time burned-area layer is not currently exposed in the UI;
the SLIM monthly/yearly burn masks replace it. Pseudo emission retains the brief's
uncertainty about its scope.

### Environmental context and future scope

Environmental context follows the scenario brief: prevention provides land
cover; detection provides numerical FWI, land cover, burned area, vegetation
fuel, and precipitation; prediction provides burned area and environmental context;
restoration provides burned area and land cover. District
boundaries remain the common reporting reference. Ward-level units, controls,
endpoints, and summaries are intentionally out of scope at this stage.

The hero cycle links directly to each scenario. Hovering or keyboard-focusing a
cycle link or scenario card highlights its counterpart and previews the stage in
the cycle's centre. The cycle is also available on mobile. `static/interactions.css`
adds brief entrance and control transitions, with reduced-motion support.
The map header reports actual loading, ready, unapplied changes, and error states;
its activity line runs only while data requests or map tiles are pending.

Run `node tests/ui/workspace.test.cjs` if the environment blocks the test runner's
child process. Tests mock data and Leaflet; a connected browser is still needed for
visual layout and real tile review.

### Hazard and district layers

`landscape_client.py` reads the public objects under
`https://s3.waw3-1.cloudferro.com/slim/fire/`:

- `zambia_hazard_2025_continuous_cog.tif`: primary layer in preparedness, with a
  fixed continuous 0–1 palette, legend and opacity control. The year is always
  2025, independent of the observation dates. Nodata and tiles outside its extent
  are transparent. Tiles are read through COG range requests and cached in
  `.cache/hazard` (override with `HAZARD_CACHE_DIR`).
- `zambia_districts.geojson`: reference boundaries enabled in every scenario.
  The supplied file contains 56 districts in ESRI:102022; the API reprojects it
  to WGS 84 and retains district/province names and codes. Its coverage is used
  as supplied. Hover or click for names; boundaries do not create fire results
  or district-level hazard statistics.

- `recovery/SLIM_RECOVERY_STATUS_20250101_20251231_WGS84_cog.tif`: the primary
  restoration layer. A binary 2025 raster (value `1` = recovered cell, `0` =
  nodata) rendered as a single green tone with opacity control, independent of
  any observation period. Cached in `.cache/recovery`
  (override with `RECOVERY_CACHE_DIR`).

- `burnscar/<year>/SLIM_BURN_MASK_<start>_<end>_WGS84_cog.tif`: monthly and
  full-year SLIM burned-area masks (value `1` = burned). In detection the layer
  is on by default and shows the burned-area mask for the month preceding the
  selected period end; in restoration it is fixed to the 2025 yearly composite
  with no period selector. Detection offers recent UTC windows, a 2026 month
  picker, and a custom range which drive the FIRMS fire query. Rendered as a single ember tone with opacity
  control. Cached in `.cache/burnscars` (override with `BURN_CACHE_DIR`).

Routes: `/api/hazard/style`, `/api/hazard/tiles/{z}/{x}/{y}.png`,
`/api/burn/style`, `/api/burn/tiles/{z}/{x}/{y}.png`, `/api/recovery/style`,
`/api/recovery/tiles/{z}/{x}/{y}.png`, `/api/districts`.
District geometry is cached in process memory; restart the API after replacing
that source. Clear the relevant tile cache if replacing a raster at the same
URL. Fire propagation and risk assessment remain planned.

Run offline GIS/API checks with
`python -m unittest discover -s tests -p test_landscape.py`.

### Numerical Fire Weather Index

Detection displays FWI across the selected WGS84 extent and reports
a dimensionless numerical value at its centre. `/api/fire-weather-index` routes
today and the previous six UTC calendar days to the GWIS `ecmwf.query` WMS
`GetFeatureInfo` endpoint for that centre value. The area layer uses the
official provider-rendered `ecmwf.fwi` WMS; its colours are not converted back
into numerical values. The backend requests and caches one aspect-preserving WMS
image for the complete selected extent, avoiding a burst of independent upstream
tile requests when the user zooms. Older dates use the numerical
`cems-fire-historical-v1` EWDS grid. Future dates return an explicit
`unavailable` result and are not sent to either historical source.

EWDS uses the current catalogue form fields (`reanalysis`,
`fire_weather_index`, system `4_1`, 0.25° grid and NetCDF). The client consults
the published constraints to prefer consolidated ERA5 and uses intermediate
ERA5T only when that is the listed product for the date. Downloads run in a
bounded background queue, are cached under `.cache/fire_weather`, and initially
return `pending`. The request covers the selected extent. After download, the
client inspects the NetCDF variable, coordinates, fill values and longitude
convention, renders the numerical grid over that extent, and samples the nearest
grid cell to the centre for the reported value. The UI labels extent cell
minimum, mean and maximum as grid statistics rather than a polygon average.
Results and GeoJSON exports retain the provider source and sampling label.

EWDS credentials stay on the backend. Accept the dataset's CC-BY terms and
configure either:

- `EWDS_API_KEY` (with optional `EWDS_API_URL`, defaulting to
  `https://ewds.climate.copernicus.eu/api`), or
- the service account's `.cdsapirc` exactly as generated by EWDS.

`FWI_CACHE_DIR` overrides the cache location. Run focused checks with
`python -m unittest tests.test_fire_weather`.
