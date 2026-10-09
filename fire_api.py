"""FastAPI application serving NASA FIRMS detections as GeoJSON."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    from .burned_area_client import BurnedAreaError, get_burned_area_tile
    from .consolidate_15days import consolidate_fire_events
    from .firms_client import FirmsClient, FirmsError, parse_bbox
    from .fuel_client import FuelError, get_fuel_tile, GWIS_FUEL_LAYER
    from .landcover_client import (
        LandCoverError,
        closest_landcover_year,
        get_landcover_tile,
        LAND_COVER_RESOLUTION_M,
        landcover_url,
    )
    from .precipitation_client import PrecipitationError, get_precipitation, get_precipitation_for_tile
    from .map_view import MAP_HTML
    from .qml_style import load_qml_style
except ImportError:  # Allow: python slim_fire/fire_api.py
    from burned_area_client import BurnedAreaError, get_burned_area_tile
    from consolidate_15days import consolidate_fire_events
    from firms_client import FirmsClient, FirmsError, parse_bbox
    from fuel_client import FuelError, get_fuel_tile, GWIS_FUEL_LAYER
    from landcover_client import (
        LandCoverError,
        closest_landcover_year,
        get_landcover_tile,
        LAND_COVER_RESOLUTION_M,
        landcover_url,
    )
    from precipitation_client import PrecipitationError, get_precipitation, get_precipitation_for_tile
    from map_view import MAP_HTML
    from qml_style import load_qml_style


app = FastAPI(title="SLIM Fire API", version="0.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1_000)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/map")


@app.get("/map", response_class=HTMLResponse, include_in_schema=False)
def fire_map() -> str:
    return MAP_HTML


@app.get("/test/precipitation", response_class=HTMLResponse, include_in_schema=False)
def precipitation_test() -> str:
    """Test page for precipitation API debugging."""
    test_file = Path(__file__).parent / "test_precipitation.html"
    if test_file.exists():
        return test_file.read_text()
    return "<h1>Test page not found</h1>"


def get_client() -> FirmsClient:
    """Build the client at request time so environment changes are respected."""
    return FirmsClient(
        map_key=os.getenv("FIRMS_MAP_KEY", ""),
        cache_dir=os.getenv("FIRMS_CACHE_DIR", ".cache/firms"),
    )


def load_firms_from_gpkg(bbox: str, start: date, end: date) -> gpd.GeoDataFrame | None:
    """
    Try to load FIRMS data from a preloaded GeoPackage file.
    Returns None if no local data found or USE_LOCAL_FIRMS not enabled.
    """
    use_local = os.getenv("USE_LOCAL_FIRMS", "").lower() in ("true", "1", "yes")
    if not use_local:
        print(f"[FIRMS] USE_LOCAL_FIRMS={use_local} - skipping local load")
        return None
    
    try:
        coordinates = parse_bbox(bbox)
        west, south, east, north = coordinates
        
        # Try to load from GeoPackage
        fire_data_dir = Path(os.getenv("FIRE_DATA_DIR", "data/fires"))
        gpkg_file = fire_data_dir / "fires.gpkg"
        
        if not gpkg_file.exists():
            print(f"[FIRMS] GeoPackage not found at {gpkg_file} - will use API")
            return None
        
        print(f"[FIRMS] Loading from GeoPackage: {gpkg_file}")
        # Read entire layer and filter by bbox and date
        gdf = gpd.read_file(gpkg_file, layer="fires")
        print(f"[FIRMS] Loaded {len(gdf)} total records from GPKG")
        
        # Filter by bounding box
        gdf = gdf.cx[west:east, south:north]
        print(f"[FIRMS] After bbox filter: {len(gdf)} records")
        
        # Filter by date if acq_date column exists
        if "acq_date" in gdf.columns and not gdf.empty:
            gdf["acq_date"] = pd.to_datetime(gdf["acq_date"], errors="coerce")
            start_dt = pd.Timestamp(start)
            end_dt = pd.Timestamp(end)
            gdf = gdf[(gdf["acq_date"] >= start_dt) & (gdf["acq_date"] <= end_dt)]
            print(f"[FIRMS] After date filter ({start} to {end}): {len(gdf)} records")
        
        # Convert datetime columns to string for JSON serialization
        for col in gdf.columns:
            if pd.api.types.is_datetime64_any_dtype(gdf[col]):
                gdf[col] = gdf[col].astype(str)
        
        if gdf.empty:
            print(f"[FIRMS] No data found after filtering - will use API")
            return None
        
        print(f"[FIRMS] ✓ Returning {len(gdf)} records from GeoPackage")
        return gdf
    except Exception as e:
        # Log error but don't fail - will fall back to API
        print(f"[FIRMS] Error loading from GPKG: {e}")
        return None


def load_precipitation_from_gpkg(
    bbox: str,
    time_window: str = "24h",
    observation_date: date | None = None,
) -> list[dict] | None:
    """
    Try to load precipitation data from a preloaded GeoPackage file.
    Returns None if no local data found or USE_LOCAL_PRECIPITATION not enabled.
    """
    use_local = os.getenv("USE_LOCAL_PRECIPITATION", "").lower() in ("true", "1", "yes")
    if not use_local:
        print(f"[PRECIP] USE_LOCAL_PRECIPITATION={use_local} - skipping local load")
        return None
    
    try:
        coordinates = parse_bbox(bbox)
        west, south, east, north = coordinates
        
        # Try to load from GeoPackage
        precip_data_dir = Path(os.getenv("PRECIPITATION_DATA_DIR", "data/precipitation"))
        gpkg_file = precip_data_dir / "precipitation.gpkg"
        
        if not gpkg_file.exists():
            print(f"[PRECIP] GeoPackage not found at {gpkg_file} - will use API")
            return None
        
        print(f"[PRECIP] Loading from GeoPackage: {gpkg_file}")
        
        # Read entire layer
        gdf = gpd.read_file(gpkg_file, layer="precipitation")
        print(f"[PRECIP] Loaded {len(gdf)} total records from GPKG")
        
        # Filter by bounding box
        gdf = gdf.cx[west:east, south:north]
        print(f"[PRECIP] After bbox filter: {len(gdf)} records")
        
        # Filter by time_window if column exists
        if "time_window" in gdf.columns:
            gdf = gdf[gdf["time_window"] == time_window]
            print(f"[PRECIP] After time_window filter ({time_window}): {len(gdf)} records")
        
        # Filter by observation_date if provided and column exists
        if observation_date and "observation_date" in gdf.columns:
            gdf["observation_date"] = pd.to_datetime(gdf["observation_date"], errors="coerce")
            target_date = pd.Timestamp(observation_date)
            gdf = gdf[gdf["observation_date"] == target_date]
            print(f"[PRECIP] After date filter ({observation_date}): {len(gdf)} records")
        
        if gdf.empty:
            print(f"[PRECIP] No data found after filtering - will use API")
            return None
        
        print(f"[PRECIP] ✓ Returning {len(gdf)} records from GeoPackage")
        
        # Convert to GeoJSON-like feature list
        features = []
        for idx, row in gdf.iterrows():
            lon, lat = row.geometry.x, row.geometry.y
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "precipitation_mm": float(row.get("precipitation_mm", 0)),
                    "intensity": min(1.0, max(0.0, float(row.get("precipitation_mm", 0)) / 200.0)),
                    "time_window": row.get("time_window", time_window),
                }
            })
        
        return features
    except Exception as e:
        # Log error but don't fail - will fall back to API
        print(f"[PRECIP] Error loading from GPKG: {e}")
        return None


def get_fire_data(bbox: str, start: date, end: date, sensor: str):
    """
    Fetch FIRMS data constrained to the allowed region (21.9,-18.1,33.8,-8.2).
    
    Tries to load from preloaded GeoPackage first if USE_LOCAL_FIRMS=true,
    otherwise fetches from NASA FIRMS API.
    """
    if start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    try:
        coordinates = parse_bbox(bbox)
        # Constrain to allowed region: west, south, east, north
        ALLOWED_REGION = (21.9, -18.1, 33.8, -8.2)
        west, south, east, north = coordinates
        allowed_west, allowed_south, allowed_east, allowed_north = ALLOWED_REGION
        
        # Calculate intersection
        constrained_west = max(west, allowed_west)
        constrained_south = max(south, allowed_south)
        constrained_east = min(east, allowed_east)
        constrained_north = min(north, allowed_north)
        
        # Check if intersection is valid
        if constrained_west > constrained_east or constrained_south > constrained_north:
            # No intersection with allowed region
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        
        constrained_bbox_str = f"{constrained_west},{constrained_south},{constrained_east},{constrained_north}"
        
        # Try local GeoPackage first if enabled
        local_data = load_firms_from_gpkg(constrained_bbox_str, start, end)
        if local_data is not None:
            print(f"[FIRMS] Using data from GeoPackage")
            gdf = local_data
        else:
            # Fall back to API
            print(f"[FIRMS] Falling back to NASA FIRMS API")
            constrained_coordinates = (constrained_west, constrained_south, constrained_east, constrained_north)
            gdf = get_client().download(constrained_coordinates, start, end, sensor)
            print(f"[FIRMS] Downloaded {len(gdf)} records from API")
        
        # Convert all datetime columns to strings for JSON serialization
        for col in gdf.columns:
            if pd.api.types.is_datetime64_any_dtype(gdf[col]):
                gdf[col] = gdf[col].astype(str)
        
        return gdf
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FirmsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/fires")
def fires(
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    start: date = Query(...),
    end: date = Query(...),
    sensor: str = Query("all"),
) -> JSONResponse:
    """Return active-fire detections as a GeoJSON FeatureCollection."""
    gdf = get_fire_data(bbox, start, end, sensor)
    return JSONResponse(content=json.loads(gdf.to_json(drop_id=True, na="null")))


@app.get("/api/fire-events")
def fire_events(
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    start: date = Query(...),
    end: date = Query(...),
    sensor: str = Query("all"),
    cluster_distance_m: float = Query(2_000, gt=0, le=25_000),
    min_detections: int = Query(3, ge=1, le=100),
) -> JSONResponse:
    """Return approximate 15-day clustered event polygons as GeoJSON."""
    fires_gdf = get_fire_data(bbox, start, end, sensor)
    try:
        events = consolidate_fire_events(
            fires_gdf,
            interval_days=15,
            cluster_distance_m=cluster_distance_m,
            min_detections=min_detections,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(content=json.loads(events.to_json(drop_id=True, na="null")))


@app.get("/api/burned-area/tiles/{z}/{x}/{y}.png", include_in_schema=False)
def burned_area_tile(
    z: int,
    x: int,
    y: int,
    start: date = Query(...),
    end: date = Query(...),
) -> Response:
    """Proxy a time-filtered GWIS MODIS & VIIRS NRT burned-area tile."""
    try:
        content = get_burned_area_tile(
            x=x,
            y=y,
            z=z,
            start=start,
            end=end,
            cache_dir=os.getenv("GWIS_CACHE_DIR", ".cache/gwis_burned_area"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BurnedAreaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/land-cover/selection")
def land_cover_selection(start: date = Query(...), end: date = Query(...)) -> dict:
    """Return the land-cover product selected for a requested date interval."""
    try:
        year = closest_landcover_year(start, end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "year": year,
        "resolution_m": LAND_COVER_RESOLUTION_M[year],
        "source": landcover_url(year),
    }


@app.get("/api/land-cover/style")
def land_cover_style() -> dict:
    """Expose the QML palette for legends and other clients."""
    style = load_qml_style()
    return {
        "opacity": style.opacity,
        "entries": [
            {
                "value": entry.value,
                "label": entry.label,
                "color": "#{:02x}{:02x}{:02x}".format(*entry.rgba[:3]),
                "alpha": entry.rgba[3],
            }
            for entry in style.entries
        ],
    }


@app.get("/api/land-cover/tiles/{year}/{z}/{x}/{y}.png", include_in_schema=False)
def land_cover_tile(year: int, z: int, x: int, y: int) -> Response:
    """Render a categorical SLIM land-cover COG as a Web Mercator PNG tile."""
    try:
        content = get_landcover_tile(
            x=x,
            y=y,
            z=z,
            year=year,
            cache_dir=os.getenv("LANDCOVER_CACHE_DIR", ".cache/landcover"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LandCoverError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/fuel/tiles/{z}/{x}/{y}.png", include_in_schema=False)
def fuel_tile(
    z: int,
    x: int,
    y: int,
    fuel_layer: str = Query(GWIS_FUEL_LAYER),
) -> Response:
    """Proxy the GWIS global fuel-map WMS layer."""
    try:
        content = get_fuel_tile(
            x=x,
            y=y,
            z=z,
            fuel_layer=fuel_layer,
            cache_dir=os.getenv("FUEL_CACHE_DIR", ".cache/gwis_fuel"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FuelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/precipitation")
def precipitation_data(
    latitude: float = Query(...),
    longitude: float = Query(...),
    time_window: str = Query("24h"),
    observation_date: date = Query(None),
) -> JSONResponse:
    """
    Retrieve accumulated precipitation for a location.
    
    Returns hourly precipitation data in mm from Open-Meteo.
    Suitable for fire spread modeling.
    """
    try:
        data = get_precipitation(
            latitude=latitude,
            longitude=longitude,
            time_window=time_window,
            observation_date=observation_date,
            cache_dir=os.getenv("PRECIPITATION_CACHE_DIR", ".cache/precipitation"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrecipitationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/precipitation/tile/{z}/{x}/{y}")
def precipitation_tile_data(
    z: int,
    x: int,
    y: int,
    time_window: str = Query("24h"),
    observation_date: date = Query(None),
) -> JSONResponse:
    """
    Retrieve precipitation for the center of an XYZ tile.
    
    Returns hourly precipitation data in mm from Open-Meteo.
    Suitable for fire spread modeling.
    """
    try:
        data = get_precipitation_for_tile(
            x=x,
            y=y,
            z=z,
            time_window=time_window,
            observation_date=observation_date,
            cache_dir=os.getenv("PRECIPITATION_CACHE_DIR", ".cache/precipitation"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PrecipitationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/precipitation/heatmap")
def precipitation_heatmap(
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    time_window: str = Query("24h"),
    observation_date: date = Query(None),
) -> JSONResponse:
    """
    Retrieve precipitation data as GeoJSON heatmap points for a bounding box.
    
    Returns points with precipitation intensity for visualization.
    Tries to load from preloaded GeoPackage first if USE_LOCAL_PRECIPITATION=true,
    otherwise fetches from Open-Meteo API via grid.
    """
    try:
        coordinates = parse_bbox(bbox)
        west, south, east, north = coordinates
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    
    # Try to load from GeoPackage first if enabled
    cached_features = load_precipitation_from_gpkg(bbox, time_window, observation_date)
    if cached_features is not None:
        print(f"[PRECIP] Using {len(cached_features)} points from GeoPackage")
        return JSONResponse(
            content={
                "type": "FeatureCollection",
                "features": cached_features
            },
            headers={"Cache-Control": "public, max-age=86400"},
        )
    
    # Fall back to API grid fetch
    print(f"[PRECIP] Falling back to API grid fetch")
    
    # Create a grid of points across the bounding box
    # Use 0.2 degree resolution for better heatmap continuity (~25km spacing)
    # This provides smooth visualization without excessive API calls
    features = []
    step = 0.2
    lat = south
    
    while lat <= north:
        lon = west
        while lon <= east:
            try:
                data = get_precipitation(
                    latitude=lat,
                    longitude=lon,
                    time_window=time_window,
                    observation_date=observation_date,
                    cache_dir=os.getenv("PRECIPITATION_CACHE_DIR", ".cache/precipitation"),
                )
                
                precip_mm = data.get("precipitation_mm", 0)
                
                # Create GeoJSON feature with intensity normalized 0-1
                # Assume max 200mm for normalization
                intensity = min(1.0, max(0.0, precip_mm / 200.0))
                
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat]
                    },
                    "properties": {
                        "precipitation_mm": precip_mm,
                        "intensity": intensity,
                        "time_window": time_window
                    }
                })
            except PrecipitationError:
                # Skip points that fail
                pass
            
            lon += step
        lat += step
    
    return JSONResponse(
        content={
            "type": "FeatureCollection",
            "features": features
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/fires/save")
def save_fire_data(
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    start: date = Query(...),
    end: date = Query(...),
    sensor: str = Query("all"),
    filename: str = Query("fires.gpkg"),
) -> dict[str, str | int]:
    """Save a request to a server-side GeoPackage."""
    safe_name = Path(filename).name
    if safe_name != filename or Path(safe_name).suffix.lower() != ".gpkg":
        raise HTTPException(status_code=422, detail="filename must be a .gpkg basename")

    gdf = get_fire_data(bbox, start, end, sensor)
    if gdf.empty:
        return {"status": "empty", "features": 0}

    output_dir = Path(os.getenv("FIRE_DATA_DIR", "data/fires"))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / safe_name
    gdf.to_file(path, layer="fires", driver="GPKG")
    return {"status": "saved", "path": str(path), "features": len(gdf)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("fire_api:app", host="0.0.0.0", port=8005, reload=True)
