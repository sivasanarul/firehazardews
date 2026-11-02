"""Checks for the optional GeoPackage-backed FIRMS boundary."""

from __future__ import annotations

from datetime import date
import os
import unittest
from unittest.mock import Mock, patch

import geopandas as gpd
from fastapi import HTTPException
from shapely.geometry import Point, box

import fire_api


class FirmsBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "FIRMS_BOUNDARY_ENABLED": "true",
                "FIRMS_BOUNDARY_GPKG": "greennexus_ews.gpkg",
                "FIRMS_BOUNDARY_LAYER": "landscape_slim_updated",
                "USE_LOCAL_FIRMS": "false",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    @staticmethod
    def boundary() -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            geometry=[box(25, -18, 30, -10)],
            crs="EPSG:4326",
        )

    def test_boundary_limits_download_bbox_and_filters_points(self) -> None:
        detections = gpd.GeoDataFrame(
            {"frp": [10.0, 20.0]},
            geometry=[Point(27, -13), Point(32, -13)],
            crs="EPSG:4326",
        )
        client = Mock()
        client.download.return_value = detections

        with (
            patch.object(fire_api.gpd, "read_file", return_value=self.boundary()),
            patch.object(fire_api, "get_client", return_value=client),
        ):
            result = fire_api.get_fire_data(
                "21,-19,34,-8",
                date(2026, 10, 1),
                date(2026, 10, 2),
                "all",
            )

        client.download.assert_called_once_with(
            (25.0, -18.0, 30.0, -10.0),
            date(2026, 10, 1),
            date(2026, 10, 2),
            "all",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result.geometry.iloc[0], Point(27, -13))

    def test_request_outside_boundary_returns_empty_without_download(self) -> None:
        client = Mock()
        with (
            patch.object(fire_api.gpd, "read_file", return_value=self.boundary()),
            patch.object(fire_api, "get_client", return_value=client),
        ):
            result = fire_api.get_fire_data(
                "20,-8,22,-6",
                date(2026, 10, 1),
                date(2026, 10, 2),
                "all",
            )

        self.assertTrue(result.empty)
        client.download.assert_not_called()

    def test_enabled_missing_boundary_fails_closed(self) -> None:
        with patch.object(fire_api.Path, "is_file", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                fire_api.get_fire_data(
                    "21,-19,34,-8",
                    date(2026, 10, 1),
                    date(2026, 10, 2),
                    "all",
                )

        self.assertEqual(caught.exception.status_code, 500)
        self.assertIn("boundary GeoPackage not found", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
