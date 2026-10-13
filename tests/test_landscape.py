"""Offline checks for raster rendering, reprojection, and API errors."""
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from fastapi.testclient import TestClient

import landscape_client as landscape
from fire_api import app


class LandscapeTests(unittest.TestCase):
    def test_fixed_palette_zero_and_nodata(self):
        values = np.array([[0, .5, 1, -9999, float("nan")]])
        pixels = np.array(Image.open(BytesIO(landscape.colorize_hazard(values, np.ones_like(values)))))
        self.assertEqual(pixels[0, 0].tolist(), [255, 255, 204, 255])
        self.assertEqual(pixels[0, 2].tolist(), [128, 0, 38, 255])
        self.assertEqual(pixels[0, 3:, 3].tolist(), [0, 0])
        masked = np.array(Image.open(BytesIO(landscape.colorize_hazard(np.array([[.5]]), np.zeros((1, 1))))))
        self.assertEqual(masked[0, 0, 3], 0)

    def test_projected_districts_return_lonlat_and_names(self):
        data = {"type": "FeatureCollection", "crs": {"properties": {"name": "EPSG:3857"}}, "features": [
            {"type": "Feature", "properties": {"DISTRICT": "Example", "PROVINCE": "Eastern", "DIST_CODE": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[3000000, -1500000], [3010000, -1500000], [3010000, -1510000], [3000000, -1500000]]]}}
        ]}
        result = landscape.prepare_districts(data)
        feature = result["features"][0]
        lon, lat = feature["geometry"]["coordinates"][0][0]
        self.assertAlmostEqual(lon, 26.949, places=3)
        self.assertAlmostEqual(lat, -13.352208, places=6)
        self.assertEqual(feature["properties"]["DISTRICT"], "Example")
        self.assertNotIn("crs", result)

    def test_district_summary_lists_sorted_names_and_bounds(self):
        data = {"type": "FeatureCollection", "crs": {"properties": {"name": "EPSG:4326"}}, "features": [
            {"type": "Feature", "properties": {"DISTRICT": "Beta", "PROVINCE": "Central", "DIST_CODE": 2},
             "geometry": {"type": "Polygon", "coordinates": [[[27.0, -14.0], [28.0, -14.0], [28.0, -13.0], [27.0, -14.0]]]}},
            {"type": "Feature", "properties": {"DISTRICT": "Alpha", "PROVINCE": "Eastern", "DIST_CODE": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[30.0, -15.0], [31.0, -15.0], [31.0, -14.0], [30.0, -15.0]]]}},
        ]}
        landscape.get_district_summary.cache_clear()
        with patch.object(landscape, "get_districts", return_value=landscape.prepare_districts(data)):
            summary = landscape.get_district_summary()
        self.assertEqual([item["name"] for item in summary], ["Alpha", "Beta"])
        self.assertEqual(summary[0]["province"], "Eastern")
        self.assertEqual(summary[1]["bbox"], [27.0, -14.0, 28.0, -13.0])

    def test_burn_mask_periods_and_urls(self):
        name = lambda url: url.rsplit("/", 1)[-1]
        self.assertEqual(name(landscape.burn_mask_url("yearly")),
                         "SLIM_BURN_MASK_20250101_20251231_WGS84_cog.tif")
        self.assertEqual(name(landscape.burn_mask_url("2025-07")),
                         "SLIM_BURN_MASK_20250701_20250731_WGS84_cog.tif")
        self.assertEqual(name(landscape.burn_mask_url("2025-02")),
                         "SLIM_BURN_MASK_20250201_20250228_WGS84_cog.tif")
        self.assertEqual(name(landscape.burn_mask_url("2026-01")),
                         "SLIM_BURN_MASK_20260101_20260131_WGS84_cog.tif")
        self.assertIn("/burnscar/2025/", landscape.burn_mask_url("2025-07"))
        self.assertIn("/burnscar/2026/", landscape.burn_mask_url("2026-03"))
        for bad in ("2024-07", "nonsense", "2025-13", "2026-13"):
            with self.assertRaises(ValueError):
                landscape.burn_mask_url(bad)
        periods = landscape.burn_periods()
        self.assertEqual(periods["year"], 2025)
        self.assertEqual(len(periods["months"]), 12)
        self.assertEqual(periods["months"][0], "2025-01")
        self.assertEqual(periods["recent_year"], 2026)
        self.assertEqual(periods["recent"][0], "2026-01")
        self.assertTrue(all(item.startswith("2026-") for item in periods["recent"]))
        self.assertTrue(1 <= len(periods["recent"]) <= 12)

    def test_burn_mask_colorization_masks_non_burn_cells(self):
        values = np.array([[1, 0, 255, 1]])
        mask = np.array([[1, 1, 0, 0]])
        pixels = np.array(Image.open(BytesIO(landscape.colorize_burn_mask(values, mask))))
        self.assertEqual(pixels[0, 0].tolist(), [0xC2, 0x41, 0x0C, 255])
        self.assertEqual(pixels[0, 1, 3], 0)
        self.assertEqual(pixels[0, 3, 3], 0)

    def test_recovery_style_and_colorization(self):
        style = landscape.recovery_style()
        self.assertEqual(style["year"], 2025)
        self.assertEqual(style["color"], landscape.RECOVERY_COLOR)
        self.assertTrue(landscape.RECOVERY_URL.endswith(
            "SLIM_RECOVERY_STATUS_20250101_20251231_WGS84_cog.tif"))
        self.assertIn("/slim/fire/recovery", landscape.RECOVERY_URL)
        values = np.array([[1, 0, 255, 1]])
        mask = np.array([[1, 1, 0, 0]])
        pixels = np.array(Image.open(BytesIO(landscape.colorize_recovery(values, mask))))
        self.assertEqual(pixels[0, 0].tolist(), [0x2F, 0x8F, 0x5B, 255])
        self.assertEqual(pixels[0, 1, 3], 0)
        self.assertEqual(pixels[0, 3, 3], 0)

    def test_reader_tiles_cache_and_outside_extent(self):
        with TemporaryDirectory(dir=".cache") as directory:
            source = f"{directory}/hazard.tif"
            with rasterio.open(source, "w", driver="GTiff", height=64, width=64, count=1,
                               dtype="float32", crs="EPSG:4326", nodata=-9999,
                               transform=from_bounds(25, -18, 33, -10, 64, 64)) as ds:
                ds.write(np.full((1, 64, 64), .5, dtype="float32"))
            with patch.object(landscape, "HAZARD_URL", source):
                content = landscape.get_hazard_tile(36, 34, 6, directory)
                pixels = np.array(Image.open(BytesIO(content)))
                self.assertTrue((pixels[..., 3] > 0).any())
                with patch.object(landscape, "Reader", side_effect=AssertionError("must use cache")):
                    self.assertEqual(content, landscape.get_hazard_tile(36, 34, 6, directory))
                outside = landscape.get_hazard_tile(0, 0, 6, directory)
                self.assertFalse(np.array(Image.open(BytesIO(outside)))[..., 3].any())

    def test_api_validation_and_source_failures(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/hazard/tiles/2/99/0.png").status_code, 422)
        self.assertEqual(client.get("/api/hazard/style").json()["year"], 2025)
        with patch("fire_api.get_districts", side_effect=landscape.LandscapeError("Source unavailable")):
            self.assertEqual(client.get("/api/districts").status_code, 502)
        with patch("fire_api.get_district_summary", side_effect=landscape.LandscapeError("Source unavailable")):
            self.assertEqual(client.get("/api/districts/summary").status_code, 502)
        self.assertEqual(client.get("/api/burn/style").json()["year"], 2025)
        self.assertEqual(client.get("/api/burn/tiles/6/36/34.png?period=2024-01").status_code, 422)
        with patch("fire_api.get_burn_tile", side_effect=landscape.LandscapeError("Source unavailable")):
            self.assertEqual(client.get("/api/burn/tiles/6/36/34.png").status_code, 502)
        self.assertEqual(client.get("/api/recovery/style").json()["year"], 2025)
        self.assertEqual(client.get("/api/recovery/tiles/2/99/0.png").status_code, 422)
        with patch("fire_api.get_recovery_tile", side_effect=landscape.LandscapeError("Source unavailable")):
            self.assertEqual(client.get("/api/recovery/tiles/6/36/34.png").status_code, 502)
        with patch("fire_api.get_hazard_tile", side_effect=landscape.LandscapeError("Source unavailable")):
            self.assertEqual(client.get("/api/hazard/tiles/6/36/34.png").status_code, 502)


if __name__ == "__main__":
    unittest.main()
