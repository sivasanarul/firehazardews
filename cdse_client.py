"""Long-lived, server-side authentication for Copernicus Data Space APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
DEFAULT_API_URL = "https://sh.dataspace.copernicus.eu/"


class CDSEError(RuntimeError):
    """Base exception for CDSE authentication and API failures."""


class CDSEConfigurationError(CDSEError):
    """Raised when required server-side credentials are missing."""


class CDSEAuthenticationError(CDSEError):
    """Raised when CDSE rejects or cannot issue an access token."""


class CDSERequestError(CDSEError):
    """Raised when an authenticated CDSE request fails."""


class CDSEClient:
    """OAuth2 client-credentials session with expiry-aware token reuse.

    The token is kept in memory, refreshed shortly before expiry, and protected
    by a lock so concurrent FastAPI requests do not create a token stampede.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        token_url: str = DEFAULT_TOKEN_URL,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 30,
        refresh_margin: float = 300,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.token_url = token_url.strip()
        self.api_url = api_url.rstrip("/") + "/"
        self.timeout = timeout
        self.refresh_margin = refresh_margin
        self.session = session or requests.Session()
        self._clock = clock
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._expires_at_utc: datetime | None = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> "CDSEClient":
        try:
            timeout = float(os.getenv("CDSE_HTTP_TIMEOUT", "30"))
            refresh_margin = float(os.getenv("CDSE_TOKEN_REFRESH_MARGIN", "300"))
        except ValueError as exc:
            raise CDSEConfigurationError(
                "CDSE_HTTP_TIMEOUT and CDSE_TOKEN_REFRESH_MARGIN must be numbers"
            ) from exc
        return cls(
            os.getenv("CDSE_CLIENT_ID", ""),
            os.getenv("CDSE_CLIENT_SECRET", ""),
            token_url=os.getenv("CDSE_TOKEN_URL", DEFAULT_TOKEN_URL),
            api_url=os.getenv("CDSE_API_URL", DEFAULT_API_URL),
            timeout=timeout,
            refresh_margin=refresh_margin,
            **kwargs,
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _require_configuration(self) -> None:
        if not self.configured:
            raise CDSEConfigurationError(
                "CDSE_CLIENT_ID and CDSE_CLIENT_SECRET are not configured"
            )

    def _token_is_fresh(self) -> bool:
        return bool(
            self._access_token
            and self._clock() < self._expires_at - self.refresh_margin
        )

    def invalidate_token(self) -> None:
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0
            self._expires_at_utc = None

    def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a reusable token, refreshing it once near expiration."""
        self._require_configuration()
        with self._lock:
            if not force_refresh and self._token_is_fresh():
                return self._access_token  # type: ignore[return-value]
            try:
                response = self.session.post(
                    self.token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise CDSEAuthenticationError(
                    f"Could not reach the CDSE identity service: {exc}"
                ) from exc
            if response.status_code >= 400:
                raise CDSEAuthenticationError(
                    f"CDSE identity service returned HTTP {response.status_code}"
                )
            try:
                payload = response.json()
                access_token = str(payload["access_token"])
                expires_in = float(payload.get("expires_in", 3600))
            except (KeyError, TypeError, ValueError, requests.JSONDecodeError) as exc:
                raise CDSEAuthenticationError(
                    "CDSE identity service returned an invalid token response"
                ) from exc
            if not access_token or expires_in <= 0:
                raise CDSEAuthenticationError(
                    "CDSE identity service returned an unusable token"
                )
            self._access_token = access_token
            self._expires_at = self._clock() + expires_in
            self._expires_at_utc = self._utcnow() + timedelta(seconds=expires_in)
            return access_token

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Make an authenticated request, refreshing once after HTTP 401."""
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = urljoin(self.api_url, path.lstrip("/"))
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.get_access_token()}"
        timeout = kwargs.pop("timeout", self.timeout)
        try:
            response = self.session.request(
                method,
                url,
                headers=dict(headers),
                timeout=timeout,
                **kwargs,
            )
            if response.status_code == 401:
                headers["Authorization"] = (
                    f"Bearer {self.get_access_token(force_refresh=True)}"
                )
                response = self.session.request(
                    method,
                    url,
                    headers=dict(headers),
                    timeout=timeout,
                    **kwargs,
                )
        except requests.RequestException as exc:
            raise CDSERequestError(f"Could not reach the CDSE API: {exc}") from exc
        if response.status_code >= 400:
            raise CDSERequestError(
                f"CDSE API returned HTTP {response.status_code}"
            )
        return response

    def status(self) -> dict[str, Any]:
        """Return non-sensitive state suitable for health reporting."""
        return {
            "configured": self.configured,
            "authenticated": self._token_is_fresh(),
            "token_expires_at": (
                self._expires_at_utc.isoformat()
                if self._expires_at_utc is not None
                else None
            ),
            "api_url": self.api_url,
        }


_default_client: CDSEClient | None = None
_default_client_lock = threading.Lock()


def get_cdse_client() -> CDSEClient:
    """Return the process-wide client used by API routes and token health."""
    global _default_client
    with _default_client_lock:
        if _default_client is None:
            _default_client = CDSEClient.from_env()
        return _default_client


def verify_cdse_authentication() -> dict[str, Any]:
    """Perform a live token exchange and return only safe status metadata."""
    client = CDSEClient.from_env()
    client.get_access_token()
    return client.status()


def main() -> int:
    try:
        status = verify_cdse_authentication()
    except CDSEConfigurationError as exc:
        print(f"CDSE authentication is not configured: {exc}")
        return 2
    except CDSEError as exc:
        print(f"CDSE authentication failed: {exc}")
        return 1
    print("CDSE authentication succeeded.")
    print(f"API: {status['api_url']}")
    print(f"Token expires: {status['token_expires_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
