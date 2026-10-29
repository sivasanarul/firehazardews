"""Offline tests for the prototype dynamic biofuel model."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from slim_fire.biofuel import fuel_model  # noqa: E402
from slim_fire.biofuel.config import LANDCOVER_CLASS_IDS, NODATA  # noqa: E402
from slim_fire.biofuel.raster_utils import TargetGrid, load_hazardmap_grid_config  # noqa: E402


class VectorModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.water = LANDCOVER_CLASS_IDS["water"]
        self.built = LANDCOVER_CLASS_IDS["built_up"]
        self.bare = LANDCOVER_CLASS_IDS["bare_land"]
        self.grass = LANDCOVER_CLASS_IDS["permanent_herbaceous"]
        self.forest = LANDCOVER_CLASS_IDS["closed_forest"]
        self.wetland = LANDCOVER_CLASS_IDS["wetland"]

    def update(self, previous, dmp, cover):
        return fuel_model.update_fuel_load(
            np.asarray(previous, dtype=np.float32),
            np.asarray(dmp, dtype=np.float32),
            np.asarray(cover, dtype=np.int16),
        )

    def test_water_and_built_up_always_zero(self) -> None:
        result = self.update([5.0, 5.0], [1000.0, 1000.0], [self.water, self.built])
        np.testing.assert_array_equal(result, [0.0, 0.0])

    def test_bare_land_always_produces_zero(self) -> None:
        result = self.update([5.0], [1000.0], [self.bare])
        np.testing.assert_array_equal(result, [0.0])

    def test_wetland_uses_configured_herbaceous_accumulation(self) -> None:
        result = self.update([0.0, 0.0], [100.0, 100.0], [self.wetland, self.grass])
        np.testing.assert_allclose(result[0], result[1])

    def test_positive_dmp_increases_herbaceous_fuel(self) -> None:
        result = self.update([1.0], [100.0], [self.grass])
        self.assertGreater(result[0], 1.0)

    def test_zero_dmp_decays_existing_fuel(self) -> None:
        result = self.update([4.0], [0.0], [self.grass])
        self.assertAlmostEqual(float(result[0]), 3.9, places=6)

    def test_result_is_clipped_to_class_capacity_and_zero(self) -> None:
        result = self.update([20.0, -4.0], [1e6, 0.0], [self.grass, self.grass])
        self.assertLessEqual(result[0], 7.0)
        self.assertGreaterEqual(result[1], 0.0)

    def test_cover_changes_accumulation_under_identical_dmp(self) -> None:
        result = self.update([0.0, 0.0], [100.0, 100.0], [self.forest, self.grass])
        self.assertNotEqual(float(result[0]), float(result[1]))

    def test_masked_or_sentinel_dmp_produces_nodata(self) -> None:
        masked = np.ma.array([100.0, 100.0], mask=[True, False])
        result = fuel_model.update_fuel_load(
            np.zeros(2, dtype=np.float32), masked, np.full(2, self.grass),
        )
        self.assertEqual(result[0], NODATA)
        self.assertNotEqual(result[1], NODATA)
        result = self.update([0.0], [NODATA], [self.grass])
        self.assertEqual(result[0], NODATA)

    def test_unknown_landcover_is_nodata(self) -> None:
        result = self.update([0.0], [100.0], [999])
        self.assertEqual(result[0], NODATA)

    def test_fire_reset_uses_consumption_fraction(self) -> None:
        result = fuel_model.apply_fire_reset(np.array([4.0, 4.0]), np.array([True, False]), 0.75)
        np.testing.assert_allclose(result, [1.0, 4.0])

    def test_large_array_retains_shape_and_dtype(self) -> None:
        shape = (1000, 1000)
        result = self.update(np.zeros(shape), np.full(shape, 25.0), np.full(shape, self.grass))
        self.assertEqual(result.shape, shape)
        self.assertEqual(result.dtype, np.float32)


class RasterPipelineTests(unittest.TestCase):
    @staticmethod
    def write(path: Path, data: np.ndarray, transform: Affine, nodata, scale=1.0, offset=0.0) -> None:
        with rasterio.open(
            path, "w", driver="GTiff", count=1, dtype=data.dtype,
            width=data.shape[1], height=data.shape[0], crs="EPSG:32735",
            transform=transform, nodata=nodata,
        ) as output:
            output.write(data, 1)
            output.scales = (scale,)
            output.offsets = (offset,)
            output.update_tags(1, units="kg/ha/day")

    def test_pipeline_preserves_grid_metadata_scale_and_temporal_state(self) -> None:
        transform = Affine(100.0, 0.0, 500000.0, 0.0, -100.0, 8500000.0)
        grid = TargetGrid(CRS.from_epsg(32735), transform, 2, 2)
        cover = np.array([[30, 70], [50, 11]], dtype=np.uint8)
        # Physical DMP is raw*2 + 10 = 110 kg/ha/day.
        dmp = np.full((2, 2), 50, dtype=np.int16)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lc_path = root / "landcover.tif"
            first = root / "DMP_20260101.tif"
            second = root / "DMP_20260111.tif"
            self.write(lc_path, cover, transform, 255)
            self.write(first, dmp, transform, -32768, scale=2.0, offset=10.0)
            self.write(second, np.zeros_like(dmp), transform, -32768)
            outputs = fuel_model.run_fuel_model(
                [second, first], lc_path, root / "output", target_grid=grid, write_kgm2=True,
            )

            self.assertEqual([path.name for path in outputs], ["fuel_load_20260101.tif", "fuel_load_20260111.tif"])
            with rasterio.open(outputs[0]) as source:
                first_fuel = source.read(1)
                self.assertEqual(source.transform, transform)
                self.assertEqual(source.crs, grid.crs)
                self.assertEqual(source.dtypes[0], "float32")
                self.assertEqual(source.tags()["units"], "Mg dry matter / ha")
                self.assertEqual(source.tags()["date"], "2026-01-01")
            self.assertAlmostEqual(float(first_fuel[0, 0]), 0.825, places=5)
            self.assertEqual(first_fuel[0, 1], 0.0)
            self.assertEqual(first_fuel[1, 0], 0.0)
            with rasterio.open(outputs[1]) as source:
                second_fuel = source.read(1)
            self.assertLess(second_fuel[0, 0], first_fuel[0, 0])
            with rasterio.open(root / "output" / "fuel_load_kgm2_20260101.tif") as source:
                np.testing.assert_allclose(source.read(1)[0, 0], first_fuel[0, 0] * 0.1)

    def test_shared_extent_matches_hazardmap_config(self) -> None:
        extent, crs, resolution = load_hazardmap_grid_config()
        self.assertEqual(extent["xmin"], 24.1643844080555539)
        self.assertEqual(crs, "EPSG:32735")
        self.assertEqual(resolution, 100.0)


if __name__ == "__main__":
    unittest.main()
