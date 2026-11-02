"""Unit tests for long-lived CDSE OAuth authentication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from cdse_client import (
    CDSEAuthenticationError,
    CDSEClient,
    CDSEConfigurationError,
)


def response(status: int, payload: dict | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload or {}).encode()
    result.headers["Content-Type"] = "application/json"
    return result


class CDSEClientTests(unittest.TestCase):
    def test_missing_credentials_fail_without_network_request(self) -> None:
        session = Mock(spec=requests.Session)
        client = CDSEClient("", "", session=session)
        with self.assertRaises(CDSEConfigurationError):
            client.get_access_token()
        session.post.assert_not_called()

    def test_token_is_reused_until_refresh_margin(self) -> None:
        now = [100.0]
        session = Mock(spec=requests.Session)
        session.post.return_value = response(
            200, {"access_token": "secret-token", "expires_in": 3600}
        )
        client = CDSEClient(
            "client-id",
            "client-secret",
            session=session,
            clock=lambda: now[0],
            utcnow=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(client.get_access_token(), "secret-token")
        now[0] += 3000
        self.assertEqual(client.get_access_token(), "secret-token")
        self.assertEqual(session.post.call_count, 1)
        safe_status = client.status()
        self.assertNotIn("access_token", safe_status)
        self.assertNotIn("client_id", safe_status)

    def test_token_refreshes_before_expiration(self) -> None:
        now = [100.0]
        session = Mock(spec=requests.Session)
        session.post.side_effect = [
            response(200, {"access_token": "first", "expires_in": 3600}),
            response(200, {"access_token": "second", "expires_in": 3600}),
        ]
        client = CDSEClient(
            "client-id",
            "client-secret",
            session=session,
            clock=lambda: now[0],
        )
        self.assertEqual(client.get_access_token(), "first")
        now[0] += 3301
        self.assertEqual(client.get_access_token(), "second")
        self.assertEqual(session.post.call_count, 2)

    def test_concurrent_requests_share_one_token_exchange(self) -> None:
        session = Mock(spec=requests.Session)
        entered = threading.Event()

        def issue_token(*args, **kwargs):
            entered.set()
            time.sleep(0.03)
            return response(200, {"access_token": "shared", "expires_in": 3600})

        session.post.side_effect = issue_token
        client = CDSEClient("client-id", "client-secret", session=session)
        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(pool.map(lambda _: client.get_access_token(), range(8)))
        self.assertTrue(entered.is_set())
        self.assertEqual(tokens, ["shared"] * 8)
        self.assertEqual(session.post.call_count, 1)

    def test_authenticated_request_retries_once_after_401(self) -> None:
        session = Mock(spec=requests.Session)
        session.post.side_effect = [
            response(200, {"access_token": "first", "expires_in": 3600}),
            response(200, {"access_token": "second", "expires_in": 3600}),
        ]
        session.request.side_effect = [
            response(401),
            response(200, {"features": []}),
        ]
        client = CDSEClient("client-id", "client-secret", session=session)
        result = client.request("POST", "/catalog/v1/search", json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(session.request.call_count, 2)
        first_headers = session.request.call_args_list[0].kwargs["headers"]
        second_headers = session.request.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer first")
        self.assertEqual(second_headers["Authorization"], "Bearer second")

    def test_identity_error_does_not_include_response_body(self) -> None:
        session = Mock(spec=requests.Session)
        session.post.return_value = response(
            401, {"error": "invalid_client", "client_secret": "leaked"}
        )
        client = CDSEClient("client-id", "client-secret", session=session)
        with self.assertRaises(CDSEAuthenticationError) as caught:
            client.get_access_token()
        self.assertNotIn("leaked", str(caught.exception))

    def test_from_env_reports_unconfigured_state(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = CDSEClient.from_env()
        self.assertFalse(client.status()["configured"])


if __name__ == "__main__":
    unittest.main()
