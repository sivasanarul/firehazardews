"""Tests for Sentinel-2 search, rendering and local image caching."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import requests

from cdse_imagery import (
    CDSEImageryError,
    render_satellite_image,
    render_sentinel2_image,
    search_satellite_acquisitions,
    search_sentinel2_acquisitions,
)


def json_response(payload: dict) -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode()
    result.headers["Content-Type"] = "application/json"
    return result


def png_response() -> requests.Response:
    result = requests.Response()
    result.status_code = 200
    result._content = b"\x89PNG\r\n\x1a\nrendered-image"
    result.headers["Content-Type"] = "image/png"
    return result


class CDSEImageryTests(unittest.TestCase):
    def test_search_returns_recent_acquisitions_and_render_bbox(self) -> None:
        client = Mock()
        client.request.return_value = json_response(
            {
                "features": [
                    {
                        "id": "older",
                        "properties": {
                            "datetime": "2026-10-25T09:00:00Z",
                            "eo:cloud_cover": 12.4,
                            "platform": "sentinel-2b",
                        },
                    },
                    {
                        "id": "newer",
                        "properties": {
                            "datetime": "2026-10-30T09:00:00Z",
                            "eo:cloud_cover": 4,
                            "platform": "sentinel-2c",
                        },
                    },
                    {
                        "id": "cloudy",
                        "properties": {
                            "datetime": "2026-11-01T09:00:00Z",
                            "eo:cloud_cover": 94,
                        },
                    },
                ]
            }
        )
        result = search_sentinel2_acquisitions(
            27.8,
            -13.2,
            days=30,
            limit=2,
            radius_km=5,
            max_cloud_cover=80,
            now=datetime(2026, 11, 2, tzinfo=timezone.utc),
            client=client,
        )
        self.assertEqual([item["id"] for item in result["acquisitions"]], ["newer", "older"])
        self.assertEqual(len(result["bbox"]), 4)
        call = client.request.call_args
        self.assertEqual(call.args[:2], ("POST", "/catalog/v1/search"))
        self.assertEqual(call.kwargs["json"]["collections"], ["sentinel-2-l2a"])
        self.assertIn("2026-10-03", call.kwargs["json"]["datetime"])

    def test_search_window_is_relative_to_clicked_firms_observation(self) -> None:
        client = Mock()
        client.request.return_value = json_response({"features": []})
        result = search_sentinel2_acquisitions(
            27.8,
            -13.2,
            reference_datetime="2026-10-20T10:30:00Z",
            before_days=10,
            after_days=15,
            client=client,
        )
        requested = client.request.call_args.kwargs["json"]["datetime"]
        self.assertEqual(
            requested,
            "2026-10-10T10:30:00Z/2026-11-04T10:30:00Z",
        )
        self.assertEqual(result["reference_datetime"], "2026-10-20T10:30:00Z")
        self.assertEqual(result["window_start"], "2026-10-10T10:30:00Z")
        self.assertEqual(result["window_end"], "2026-11-04T10:30:00Z")

    def test_mixed_search_requests_and_identifies_both_collections(self) -> None:
        client = Mock()
        client.request.side_effect = [
            json_response({
                "features": [
                    {
                        "id": "s2",
                        "collection": "sentinel-2-l2a",
                        "properties": {
                            "datetime": "2026-10-29T09:00:00Z",
                            "platform": "sentinel-2c",
                        },
                    }
                ]
            }),
            json_response({
                "features": [
                    {
                        "id": "l9",
                        "collection": "landsat-ot-l1",
                        "properties": {
                            "datetime": "2026-10-30T08:00:00Z",
                            "platform": "landsat-9",
                        },
                    },
                ]
            }),
        ]
        result = search_satellite_acquisitions(
            27.8, -13.2, source="all", client=client
        )
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(
            [call.kwargs["json"]["collections"] for call in client.request.call_args_list],
            [["sentinel-2-l2a"], ["landsat-ot-l1"]],
        )
        self.assertEqual(
            [item["source"] for item in result["acquisitions"]],
            ["landsat-8-9", "sentinel-2"],
        )

    def test_render_uses_requested_bands_and_cache(self) -> None:
        client = Mock()
        client.request.return_value = png_response()
        with tempfile.TemporaryDirectory() as directory:
            first = render_sentinel2_image(
                "2026-10-30T09:00:00Z",
                [27.7, -13.3, 27.9, -13.1],
                style="swir",
                width=512,
                height=512,
                cache_dir=directory,
                client=client,
            )
            second = render_sentinel2_image(
                "2026-10-30T09:00:00Z",
                [27.7, -13.3, 27.9, -13.1],
                style="swir",
                width=512,
                height=512,
                cache_dir=directory,
                client=client,
            )
            self.assertEqual(first, second)
            self.assertEqual(client.request.call_count, 1)
            request_body = client.request.call_args.kwargs["json"]
            self.assertIn("B12", request_body["evalscript"])
            self.assertIn("B11", request_body["evalscript"])
            self.assertEqual(request_body["output"]["width"], 512)
            self.assertEqual(len(list(Path(directory).glob("*.png"))), 1)

    def test_invalid_render_options_fail_before_request(self) -> None:
        client = Mock()
        with self.assertRaises(CDSEImageryError):
            render_sentinel2_image(
                "2026-10-30T09:00:00Z",
                [28, -13, 27, -12],
                client=client,
            )
        with self.assertRaises(CDSEImageryError):
            render_sentinel2_image(
                "2026-10-30T09:00:00Z",
                [27, -13, 28, -12],
                style="infrared",
                client=client,
            )
        client.request.assert_not_called()

    def test_landsat_render_uses_oli_swir_bands_and_collection(self) -> None:
        client = Mock()
        client.request.return_value = png_response()
        with tempfile.TemporaryDirectory() as directory:
            render_satellite_image(
                "2026-10-30T09:00:00Z",
                [27.7, -13.3, 27.9, -13.1],
                source="landsat-8-9",
                style="swir",
                cache_dir=directory,
                client=client,
            )
        request_body = client.request.call_args.kwargs["json"]
        self.assertEqual(request_body["input"]["data"][0]["type"], "landsat-ot-l1")
        self.assertIn("B07", request_body["evalscript"])
        self.assertIn("B06", request_body["evalscript"])
        self.assertNotIn("B12", request_body["evalscript"])

    def test_all_is_search_only_and_cannot_be_rendered(self) -> None:
        client = Mock()
        with self.assertRaises(CDSEImageryError):
            render_satellite_image(
                "2026-10-30T09:00:00Z",
                [27.7, -13.3, 27.9, -13.1],
                source="all",
                client=client,
            )
        client.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
