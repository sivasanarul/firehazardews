"""Aggregate FIRMS detections into approximate 15-day fire-event polygons."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint
from sklearn.cluster import DBSCAN


def consolidate_fire_events(
    fires: gpd.GeoDataFrame,
    interval_days: int = 15,
    cluster_distance_m: float = 2_000,
    min_detections: int = 3,
    polygon_buffer_m: float = 187.5,
) -> gpd.GeoDataFrame:
    """Cluster detections by time window and distance into event polygons.

    The geometries are buffered detection hulls for visualization. They are not
    measured burned-area perimeters.
    """
    if fires.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    if interval_days < 1:
        raise ValueError("interval_days must be at least 1")
    if cluster_distance_m <= 0 or polygon_buffer_m <= 0:
        raise ValueError("cluster and buffer distances must be positive")
    if min_detections < 1:
        raise ValueError("min_detections must be at least 1")

    prepared = fires.copy()
    prepared["acq_datetime_utc"] = pd.to_datetime(
        prepared["acq_datetime_utc"], errors="coerce", utc=True
    )
    prepared = prepared.dropna(subset=["acq_datetime_utc", "geometry"])
    if prepared.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    acquired = prepared["acq_datetime_utc"]
    year_start = pd.to_datetime(acquired.dt.year.astype(str) + "-01-01", utc=True)
    prepared["interval_id"] = ((acquired - year_start).dt.days // interval_days).astype(int)
    prepared["interval_start"] = year_start + pd.to_timedelta(
        prepared["interval_id"] * interval_days, unit="D"
    )
    prepared["interval_end"] = prepared["interval_start"] + pd.Timedelta(
        days=interval_days - 1
    )
    prepared["event_year"] = acquired.dt.year

    metric_crs = prepared.estimate_utm_crs()
    if metric_crs is None:
        metric_crs = "EPSG:3857"
    metric = prepared.to_crs(metric_crs)
    if "frp" in metric.columns:
        metric["frp"] = pd.to_numeric(metric["frp"], errors="coerce")
    else:
        metric["frp"] = np.nan

    records: list[dict] = []
    grouped = metric.groupby(["event_year", "interval_id"], sort=True)
    for (year, interval_id), group in grouped:
        if len(group) < min_detections:
            continue
        coordinates = np.column_stack((group.geometry.x, group.geometry.y))
        labels = DBSCAN(
            eps=cluster_distance_m,
            min_samples=min_detections,
        ).fit_predict(coordinates)
        labeled = group.assign(cluster=labels)

        for cluster_id, cluster in labeled[labeled["cluster"] >= 0].groupby("cluster"):
            hull = MultiPoint(cluster.geometry.tolist()).convex_hull
            polygon = hull.buffer(polygon_buffer_m)
            start_time = cluster["acq_datetime_utc"].min()
            end_time = cluster["acq_datetime_utc"].max()
            frp = cluster["frp"].dropna()
            records.append(
                {
                    "fire_id": f"{int(year)}_{int(interval_id):02d}_{int(cluster_id):04d}",
                    "interval_start": cluster["interval_start"].iloc[0].isoformat(),
                    "interval_end": cluster["interval_end"].iloc[0].isoformat(),
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "duration_hr": (end_time - start_time).total_seconds() / 3_600,
                    "detections": len(cluster),
                    "max_frp": float(frp.max()) if not frp.empty else None,
                    "mean_frp": float(frp.mean()) if not frp.empty else None,
                    "total_frp": float(frp.sum()) if not frp.empty else None,
                    "cluster_area_ha": polygon.area / 10_000,
                    "geometry": polygon,
                }
            )

    if not records:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(records, geometry="geometry", crs=metric_crs).to_crs(
        "EPSG:4326"
    )
