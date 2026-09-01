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

Use the fire-layer selector and burned-area checkbox independently to overlay
FIRMS detections (or 15-day event polygons) with the time-filtered GWIS MODIS &
VIIRS NRT burned-area WMS. The Leaflet layer control can then show or hide each
loaded overlay. The browser requests same-origin tile URLs; FastAPI proxies and
caches the upstream PNG tiles under `.cache/gwis_burned_area`. Set
`GWIS_CACHE_DIR` to override that location.

Enable `Land cover` to select the SLIM COG whose year is nearest the midpoint
of the requested start/end interval. Available configured years are 2000,
2005, 2010, 2015, 2020, and 2024; an exact tie selects the newer year. The
published objects are 30 m through 2015 and 10 m for 2020/2024. FastAPI reads
and colorizes the remote categorical COG with rio-tiler, then caches PNG tiles
under `.cache/landcover`. Set `LANDCOVER_CACHE_DIR` to override it.
The `LC opacity` slider changes the displayed land-cover opacity from 0–100%
without requesting new tiles.

Enable `Fuel data (GWIS)` to overlay the GWIS `fuel_map` WMS layer. The API
validates the layer name and caches valid PNG responses under
`.cache/gwis_fuel`. Set `FUEL_CACHE_DIR` to override that location.

Land-cover class values, labels, and RGBA colors are read from
`SLIM_LC_LandCover_legend.qml`. The QML uses nearest-neighbour categorical
rendering; rio-tiler likewise reads categorical tiles without interpolating
class values. `/api/land-cover/style` exposes the parsed palette as JSON.

Valid sensor values are `all`, `modis`, `viirs_snpp`, `viirs_noaa20`, and
`viirs_noaa21`. Set `FIRMS_CACHE_DIR` and `FIRE_DATA_DIR` to override the
default `.cache/firms` and `data/fires` directories.
# firehazardews
