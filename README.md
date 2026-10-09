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

Open `http://localhost:8000/map` for an interactive Leaflet map. The root URL
redirects to the same viewer. Choose `15-day event polygons` to cluster nearby
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

The `/map` workspace uses a responsive sidebar and a full-height map. On small
screens, open the menu to adjust filters. **Use current map extent** selects a
custom area without entering coordinates; geographic coordinates remain
available under the area settings. Recent-date shortcuts use the browser's
local calendar. The default historical period remains July 1–15, 2025.

The first visit opens a guided setup: **Area → Dates → Layers**, with one step
visible at a time. Continue validates the current step; Back and completed step
links retain the chosen settings. The default fire layer can be loaded with
**Explore map**; environmental layers sit under an optional section. Pressing
Enter on an early step advances the guide without downloading observations.
On mobile, **Get started** opens the guide and resumes the current step.

The forest-green panel and orange accents preserve the original design.
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
