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

The root URL (`/`) introduces three workflows from the EWS brief. Each opens the
existing map design with scenario-specific guidance and layer defaults:

- `/map?scenario=prevention`: Prevention & preparedness; 2025 hazard index and districts.
- `/map?scenario=response`: Detection & response; FIRMS detections and districts, preserving the original workflow.
- `/map?scenario=restoration`: Restoration & adaptation; burned area, land cover and districts.

`/map` and unknown scenario values default to detection and response. The header's
**All scenarios** link returns to the landing page. No observation data loads until
**Explore map** is selected. FIRMS detections and event polygons are available only
in scenario 2. Hazard is available in scenario 1; districts and environmental
context layers are shared across scenarios.

Both pages load `static/design.css` for shared colours, typography, scenario accents,
and responsive refinements. `static/floating-map.css` adds the floating layout and
light control surfaces while retaining the original map styles in `map.css`.
Prepare / Respond / Restore links open another scenario with
its default settings; the current scenario is marked for screen readers. Future
modules appear after the map setup, and SLIM branding sits at the bottom of each page.

The shared catalogue in `static/scenarios.js` supplies landing cards and map
roadmaps. Greyed roadmap entries are informational and cannot load data. **Planned**
includes the brief's future prototype modules and integrations still needed by this
viewer (recovery rasters, risk assessment, monthly/yearly summaries).
**Concept** covers sensors, emissions, and carbon. Risk assessment is **Planned**.
Existing GWIS burned-area tiles are not presented as completed monthly/annual
analysis. Emissions retains the brief's uncertainty about its scope.

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

Routes: `/api/hazard/style`, `/api/hazard/tiles/{z}/{x}/{y}.png`, `/api/districts`.
District geometry is cached in process memory; restart the API after replacing
that source. Clear the hazard tile cache if replacing the raster at the same URL.
Recovery and the new monthly/yearly burned-area rasters remain planned; the
existing GWIS burned-area layer remains available.

Run offline GIS/API checks with
`python -m unittest discover -s tests -p test_landscape.py`.
