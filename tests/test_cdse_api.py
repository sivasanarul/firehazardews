"""API boundary checks for safe CDSE authentication status."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import fire_api
from cdse_client import CDSEAuthenticationError


class CDSEAuthStatusAPITests(unittest.TestCase):
    def test_status_does_not_fetch_or_expose_token(self) -> None:
        cdse = Mock()
        cdse.status.return_value = {
            "configured": True,
            "authenticated": False,
            "token_expires_at": None,
            "api_url": "https://sh.dataspace.copernicus.eu/",
        }
        with patch.object(fire_api, "get_cdse_client", return_value=cdse):
            result = fire_api.cdse_auth_status(verify=False)
        cdse.get_access_token.assert_not_called()
        self.assertNotIn("access_token", result)
        self.assertNotIn("client_secret", result)

    def test_verify_performs_exchange_and_returns_safe_status(self) -> None:
        cdse = Mock()
        cdse.get_access_token.return_value = "must-not-be-returned"
        cdse.status.return_value = {
            "configured": True,
            "authenticated": True,
            "token_expires_at": "2026-11-02T01:00:00+00:00",
            "api_url": "https://sh.dataspace.copernicus.eu/",
        }
        with patch.object(fire_api, "get_cdse_client", return_value=cdse):
            result = fire_api.cdse_auth_status(verify=True)
        cdse.get_access_token.assert_called_once_with()
        self.assertNotIn("must-not-be-returned", str(result))

    def test_authentication_failure_is_sanitized(self) -> None:
        cdse = Mock()
        cdse.get_access_token.side_effect = CDSEAuthenticationError(
            "CDSE identity service returned HTTP 401"
        )
        with patch.object(fire_api, "get_cdse_client", return_value=cdse):
            with self.assertRaises(HTTPException) as caught:
                fire_api.cdse_auth_status(verify=True)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("HTTP 401", caught.exception.detail)

    def test_acquisition_endpoint_passes_detection_location(self) -> None:
        expected = {"bbox": [27, -14, 28, -13], "acquisitions": []}
        with patch.object(
            fire_api, "search_satellite_acquisitions", return_value=expected
        ) as search:
            result = fire_api.cdse_acquisitions(
                longitude=27.5,
                latitude=-13.5,
                days=45,
                reference_datetime="2026-10-20T10:00:00Z",
                before_days=10,
                after_days=15,
                limit=20,
                radius_km=5,
                max_cloud_cover=100,
                source="landsat-8-9",
            )
        self.assertEqual(result, expected)
        search.assert_called_once_with(
            27.5,
            -13.5,
            days=45,
            reference_datetime="2026-10-20T10:00:00Z",
            before_days=10,
            after_days=15,
            limit=20,
            radius_km=5,
            max_cloud_cover=100,
            source="landsat-8-9",
        )

    def test_image_endpoint_returns_png(self) -> None:
        with patch.object(
            fire_api,
            "render_satellite_image",
            return_value=b"\x89PNG\r\n\x1a\nimage",
        ) as render:
            result = fire_api.cdse_image(
                datetime_utc="2026-10-30T09:00:00Z",
                bbox="27,-14,28,-13",
                style="true-color",
                width=768,
                height=768,
                source="landsat-8-9",
            )
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(result.body[:8], b"\x89PNG\r\n\x1a\n")
        render.assert_called_once()
        self.assertEqual(render.call_args.kwargs["source"], "landsat-8-9")


if __name__ == "__main__":
    unittest.main()
