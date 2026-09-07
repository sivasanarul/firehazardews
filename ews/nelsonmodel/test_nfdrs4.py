"""Focused offline tests for NFDRS4 dead-fuel-moisture raster generation."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from slim_fire.ews.nelsonmodel import test as nelson  # noqa: E402


def synthetic_weather(
    hours: int,
    temperature_c: float,
    humidity_pct: float,
    rainfall_mm: float = 0.0,
    start: datetime = datetime(2025, 8, 1, tzinfo=timezone.utc),
) -> list[nelson.WeatherHour]:
    return [
        nelson.WeatherHour(
            timestamp=start + timedelta(hours=index),
            temperature_c=temperature_c,
            relative_humidity_pct=humidity_pct,
            precipitation_mm=rainfall_mm if index == hours - 1 else 0.0,
            wind_kmh=12.0,
            wind_azimuth_deg=180.0,
            shortwave_wm2=650.0 if 7 <= (index % 24) <= 17 else 0.0,
            snowfall_cm=0.0,
        )
        for index in range(hours)
    ]


class UnitConversionTests(unittest.TestCase):
    def test_open_meteo_metric_values_are_converted_for_nfdrs4(self) -> None:
        source = [nelson.WeatherHour(datetime(2025, 8, 1, tzinfo=timezone.utc), 0.0, 50.0, 25.4, 1.609344, 90.0, 100.0, 0.0)]
        frame = nelson.prepare_nfdrs_weather(source)

        self.assertEqual(frame.loc[0, "Temperature(F)"], 32.0)
        self.assertEqual(frame.loc[0, "RelativeHumidity(%)"], 50.0)
        self.assertEqual(frame.loc[0, "Precipitation(in)"], 1.0)
        self.assertEqual(frame.loc[0, "WindSpeed(mph)"], 1.0)
        self.assertEqual(frame.loc[0, "SolarRadiation(W/m2)"], 100.0)


class NfdrsTemporalResponseTests(unittest.TestCase):
    def _result(self, series: list[nelson.WeatherHour]) -> tuple[dict[str, float], object]:
        return nelson.run_nfdrs4_point(series, -15.0, series[-1].timestamp, "W", 30.0)

    def test_dry_weather_changes_fast_fuel_more_than_slow_fuel(self) -> None:
        series = synthetic_weather(24, 20.0, 90.0)
        series += synthetic_weather(8, 38.0, 10.0, start=series[-1].timestamp + timedelta(hours=1))
        transition = series[24].timestamp
        _, result = self._result(series)
        before = result.loc[result["DateTime"] == transition.replace(tzinfo=None) - timedelta(hours=1)].iloc[0]
        after = result.loc[result["DateTime"] == transition.replace(tzinfo=None) + timedelta(hours=6)].iloc[0]
        changes = {name: abs(float(after[column] - before[column])) for name, column in nelson.OUTPUT_COLUMNS.items()}

        self.assertGreater(changes["1h"], changes["10h"])
        self.assertGreater(changes["10h"], changes["100h"])

    def test_rain_increases_dead_fuel_moisture(self) -> None:
        dry, _ = self._result(synthetic_weather(72, 36.0, 15.0))
        rain, _ = self._result(synthetic_weather(72, 36.0, 15.0, rainfall_mm=12.7))

        self.assertGreater(rain["1h"], dry["1h"])
        self.assertGreater(rain["10h"], dry["10h"])

    def test_prolonged_warm_dry_weather_reduces_moisture(self) -> None:
        humid, _ = self._result(synthetic_weather(72, 20.0, 90.0))
        dry, _ = self._result(synthetic_weather(72, 38.0, 10.0))

        self.assertLess(dry["1h"], humid["1h"])
        self.assertLess(dry["10h"], humid["10h"])
        self.assertLess(dry["100h"], humid["100h"])


class RasterTests(unittest.TestCase):
    def test_writes_three_float32_moisture_rasters(self) -> None:
        bbox = nelson.BBox(28.0, -15.6, 28.2, -15.4)
        grid = nelson.Grid(bbox, 0.1)
        rasters = {name: np.full((grid.height, grid.width), 12.5, dtype=np.float32) for name in nelson.OUTPUT_COLUMNS}
        snapshot = datetime(2025, 8, 1, 23, tzinfo=timezone.utc)

        with TemporaryDirectory() as directory:
            paths = nelson.write_rasters(grid, rasters, snapshot, Path(directory))
            self.assertEqual(len(paths), 3)
            for path in paths:
                import rasterio
                with rasterio.open(path) as source:
                    self.assertEqual(source.crs.to_string(), "EPSG:4326")
                    self.assertEqual(source.read(1).shape, (2, 2))
                    self.assertEqual(source.dtypes[0], "float32")
                    self.assertFalse(np.isnan(source.read(1)).any())


if __name__ == "__main__":
    unittest.main()