"""Focused offline tests for numerical FWI routing, parsing, and sampling."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import json

import numpy as np
import requests
import xarray as xr

import fire_weather_client as fwi


class FireWeatherTests(unittest.TestCase):
    def test_utc_date_routing_boundaries_and_future(self):
        # 01:30 at UTC+02 is still the previous UTC calendar day.
        now = datetime(2026, 10, 14, 1, 30, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(fwi.route_fwi_source(date(2026, 10, 13), now), "gwis")
        self.assertEqual(fwi.route_fwi_source(date(2026, 10, 7), now), "gwis")
        self.assertEqual(fwi.route_fwi_source(date(2026, 10, 6), now), "ewds")
        self.assertEqual(fwi.route_fwi_source(date(2026, 10, 14), now), "future")

    def test_gwis_parameters_project_point_and_use_center_pixel(self):
        params = fwi.gwis_parameters(date(2026, 10, 13), 14.4378, 50.0755)
        west, south, east, north = map(float, params["bbox"].split(","))
        self.assertAlmostEqual(east - west, 10_000, places=3)
        self.assertAlmostEqual(north - south, 10_000, places=3)
        self.assertEqual(params["layers"], "ecmwf.query")
        self.assertEqual(params["query_layers"], "ecmwf.query")
        self.assertEqual(params["srs"], "EPSG:3857")
        self.assertEqual((params["width"], params["height"]), ("101", "101"))
        self.assertEqual((params["x"], params["y"]), ("50", "50"))

    def test_gwis_html_parsing_and_missing_or_invalid_values(self):
        html = """<table><tr><td>Fire Weather Index (FWI)</td><td>3.2643847</td></tr></table>"""
        self.assertAlmostEqual(fwi.parse_gwis_fwi(html), 3.2643847)
        with self.assertRaises(fwi.FireWeatherUnavailable):
            fwi.parse_gwis_fwi("<table><tr><td>Other index</td><td>2</td></tr></table>")
        with self.assertRaises(fwi.FireWeatherUnavailable):
            fwi.parse_gwis_fwi("<tr><td>Fire Weather Index (FWI)</td><td>NaN</td></tr>")
        with self.assertRaises(fwi.FireWeatherError):
            fwi.parse_gwis_fwi("<tr><td>Fire Weather Index (FWI)</td><td>high</td></tr>")

    def test_gwis_provider_failure_is_not_rerouted(self):
        session = Mock()
        session.get.side_effect = requests.Timeout("provider timed out")
        with TemporaryDirectory() as directory, patch.object(fwi, "_session", return_value=session):
            with self.assertRaisesRegex(fwi.FireWeatherError, "GWIS"):
                fwi.get_gwis_fwi(date(2026, 10, 13), 14.4378, 50.0755, directory)

    def test_gwis_result_cache_includes_source_date_and_point(self):
        response = Mock()
        response.text = "<tr><td>Fire Weather Index (FWI)</td><td>7.5</td></tr>"
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        with TemporaryDirectory() as directory, patch.object(fwi, "_session", return_value=session):
            first = fwi.get_gwis_fwi(date(2026, 10, 13), 14.4378, 50.0755, directory)
            second = fwi.get_gwis_fwi(date(2026, 10, 13), 14.4378, 50.0755, directory)
        self.assertEqual(first, second)
        self.assertEqual(first["fwi"], 7.5)
        self.assertEqual(first["unit"], "dimensionless")
        self.assertEqual(first["status"], "available")
        self.assertEqual(session.get.call_count, 1)

    def test_gwis_area_tile_uses_the_official_fwi_wms_layer(self):
        response = Mock()
        response.content = b"\x89PNG\r\n\x1a\narea"
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        with TemporaryDirectory() as directory, patch.object(
            fwi, "_session", return_value=session
        ), patch.object(fwi, "route_fwi_source", return_value="gwis"):
            content = fwi.get_gwis_fwi_tile(
                date(2026, 10, 13), 17, 11, 5, directory
            )
        self.assertEqual(content, response.content)
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["request"], "GetMap")
        self.assertEqual(params["layers"], "ecmwf.fwi")
        self.assertEqual(params["time"], "2026-10-13")
        self.assertEqual(params["format"], "image/png")

    def test_gwis_area_raster_uses_one_cached_extent_request(self):
        response = Mock()
        response.content = b"\x89PNG\r\n\x1a\narea"
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        extent = (21.9, -18.1, 33.8, -8.2)
        with TemporaryDirectory() as directory, patch.object(
            fwi, "_session", return_value=session
        ), patch.object(fwi, "route_fwi_source", return_value="gwis"):
            first = fwi.get_gwis_fwi_raster(
                date(2026, 10, 13), extent, directory
            )
            second = fwi.get_gwis_fwi_raster(
                date(2026, 10, 13), extent, directory
            )
        self.assertEqual(first, second)
        self.assertEqual(session.get.call_count, 1)
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["layers"], "ecmwf.fwi")
        self.assertEqual(max(int(params["width"]), int(params["height"])), 1024)
        self.assertGreaterEqual(min(int(params["width"]), int(params["height"])), 256)

    def test_ewds_request_uses_current_form_fields(self):
        request = fwi.ewds_request(
            date(2020, 7, 1), 14.4378, 50.0755, "consolidated_dataset"
        )
        self.assertEqual(
            set(request),
            {
                "product_type", "variable", "dataset_type", "system_version",
                "year", "month", "day", "grid", "data_format", "area",
            },
        )
        self.assertEqual(request["variable"], ["fire_weather_index"])
        self.assertEqual(request["product_type"], "reanalysis")
        self.assertEqual(request["dataset_type"], "consolidated_dataset")
        self.assertEqual(request["system_version"], ["4_1"])
        self.assertEqual(request["grid"], "0.25/0.25")
        self.assertEqual(request["data_format"], "netcdf")
        self.assertTrue(all(round(value * 20) == value * 20 for value in request["area"]))

    def test_ewds_request_uses_the_selected_extent(self):
        extent = (21.91, -18.08, 33.71, -8.22)
        request = fwi.ewds_request(
            date(2020, 7, 1),
            27.81,
            -13.15,
            "consolidated_dataset",
            extent,
        )
        self.assertEqual(request["area"], [-8.2, 21.9, -18.1, 33.75])

    def test_constraints_prefer_consolidated_then_intermediate(self):
        common = {
            "year": ["2020"], "month": ["07"], "day": ["01"],
            "product_type": ["reanalysis"], "variable": ["fire_weather_index"],
            "grid": ["0.25/0.25"], "data_format": ["netcdf"],
            "system_version": ["4_1"],
        }
        constraints = [
            {**common, "dataset_type": ["intermediate_dataset"]},
            {**common, "dataset_type": ["consolidated_dataset"]},
        ]
        self.assertEqual(
            fwi.choose_ewds_dataset_type(date(2020, 7, 1), constraints),
            "consolidated_dataset",
        )
        self.assertEqual(
            fwi.choose_ewds_dataset_type(date(2020, 7, 1), constraints[:1]),
            "intermediate_dataset",
        )
        with self.assertRaises(fwi.FireWeatherUnavailable):
            fwi.choose_ewds_dataset_type(date(2020, 7, 2), constraints)

    def test_netcdf_sampling_inspects_variable_coordinates_and_fill_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fwi.nc"
            data = xr.Dataset(
                {
                    "fwi": (
                        ("time", "latitude", "longitude"),
                        np.array([[[4.0, 8.0], [12.0, np.nan]]]),
                        {"long_name": "Fire Weather Index", "units": "1"},
                    )
                },
                coords={
                    "time": np.array(["2020-07-01"], dtype="datetime64[D]"),
                    "latitude": [50.0, 50.25],
                    "longitude": [14.25, 14.5],
                },
            )
            data.to_netcdf(path)
            sample = fwi.sample_ewds_netcdf(path, date(2020, 7, 1), 14.4378, 50.0755)
            self.assertEqual(sample["fwi"], 8.0)
            self.assertEqual(sample["longitude"], 14.5)
            self.assertEqual(sample["latitude"], 50.0)
            with self.assertRaises(fwi.FireWeatherUnavailable):
                fwi.sample_ewds_netcdf(path, date(2020, 7, 1), 14.5, 50.25)

    def test_netcdf_sampling_handles_zero_to_360_longitudes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fwi-360.nc"
            xr.Dataset(
                {"fire_weather_index": (("latitude", "longitude"), [[9.0]])},
                coords={"latitude": [10.0], "longitude": [350.0]},
            ).to_netcdf(path)
            sample = fwi.sample_ewds_netcdf(path, date(2020, 7, 1), -10.0, 10.0)
            self.assertEqual(sample["fwi"], 9.0)
            self.assertEqual(sample["longitude"], 350.0)

    def test_extent_statistics_handle_zero_to_360_longitudes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fwi-360-extent.nc"
            xr.Dataset(
                {"fwi": (("latitude", "longitude"), [[2.0, 3.0, 1.0]])},
                coords={"latitude": [10.0], "longitude": [0.0, 1.0, 359.0]},
            ).to_netcdf(path)
            statistics = fwi.summarize_ewds_netcdf(
                path, date(2020, 7, 1), (-1.1, 9.5, 1.1, 10.5)
            )
            self.assertEqual(statistics["cell_count"], 3)
            self.assertEqual(statistics["min"], 1.0)
            self.assertEqual(statistics["max"], 3.0)

    def test_cached_ewds_extent_renders_a_numerical_grid_png(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            raster_id = "a" * 64
            job = cache / "ewds" / raster_id
            job.mkdir(parents=True)
            xr.Dataset(
                {
                    "fwi": (
                        ("latitude", "longitude"),
                        [[1.0, 2.0], [3.0, np.nan]],
                        {"long_name": "Fire Weather Index"},
                    )
                },
                coords={"latitude": [50.25, 50.0], "longitude": [14.25, 14.5]},
            ).to_netcdf(job / "download.nc")
            result = {
                "date": "2020-07-01",
                "status": "available",
                "extent": [14.2, 49.95, 14.55, 50.3],
                "statistics": {"min": 1.0, "max": 3.0},
            }
            (cache / "ewds" / f"{raster_id}.json").write_text(json.dumps(result))
            content = fwi.get_ewds_fwi_raster(raster_id, cache)
            self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertTrue((job / "raster.png").exists())

    def test_future_result_is_explicitly_unavailable(self):
        result = fwi.get_fire_weather_index(
            date(2026, 10, 14),
            14.4378,
            50.0755,
            now=datetime(2026, 10, 13, 23, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["source"], "No provider selected")
        self.assertIn("Future", result["message"])

    def test_missing_ewds_credentials_returns_configuration_boundary(self):
        with (
            TemporaryDirectory() as directory,
            patch.dict(os.environ, {}, clear=True),
            patch("cdsapi.api.get_url_key_verify", side_effect=Exception("missing")),
        ):
            result = fwi.get_ewds_fwi(date(2020, 7, 1), 14.4378, 50.0755, directory)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["fwi"])
        self.assertEqual(result["sampling"], "nearest grid cell")
        self.assertIn("EWDS credentials", result["message"])


if __name__ == "__main__":
    unittest.main()
