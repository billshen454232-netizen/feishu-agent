"""Verify Cloudflare Access JWTs at the origin.

Cloudflare Access only protects traffic that goes through Cloudflare. A request
sent straight to the origin IP with the right Host header would skip it, so the
review page checks the ``Cf-Access-Jwt-Assertion`` token itself: RS256 signature
against the team's published keys, audience (the application's AUD tag),
issuer and expiry.

PyJWT is imported only when a verifier is built (Gateway with Access configured),
so Workers that never serve the review page do not need it installed
(``requirements-gateway.txt``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KEY_CACHE_SECONDS = 3600
CLOCK_LEEWAY_SECONDS = 30


class AccessDenied(Exception):
    """The request does not carry a valid Cloudflare Access token."""


class CloudflareAccessVerifier:
    def __init__(
        self,
        team_domain: str,
        audience: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        import jwt  # Gateway-only dependency, see module docstring

        self._jwt = jwt
        self._team_domain = team_domain.strip().rstrip("/").removeprefix("https://")
        self._audience = audience.strip()
        self._client = http_client or httpx.AsyncClient(timeout=10)
        self._keys: dict[str, Any] = {}
        self._keys_loaded_at = 0.0

    @property
    def issuer(self) -> str:
        return "https://{}".format(self._team_domain)

    async def verify(self, token: str) -> dict[str, Any]:
        """Return the token's claims, or raise :class:`AccessDenied`."""
        if not token:
            raise AccessDenied("missing Cloudflare Access token")
        try:
            kid = self._jwt.get_unverified_header(token).get("kid", "")
        except self._jwt.PyJWTError as exc:
            raise AccessDenied("malformed token") from exc
        key = await self._key_for(kid)
        try:
            return self._jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self.issuer,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except self._jwt.PyJWTError as exc:
            raise AccessDenied("invalid token: {}".format(type(exc).__name__)) from exc

    async def _key_for(self, kid: str) -> Any:
        stale = time.monotonic() - self._keys_loaded_at > KEY_CACHE_SECONDS
        if stale or kid not in self._keys:
            await self._load_keys()
        if kid not in self._keys:
            raise AccessDenied("unknown signing key")
        return self._keys[kid]

    async def _load_keys(self) -> None:
        try:
            response = await self._client.get("{}/cdn-cgi/access/certs".format(self.issuer))
            response.raise_for_status()
            jwks = response.json()
            keys = {item["kid"]: self._jwt.PyJWK(item).key for item in jwks.get("keys", []) if item.get("kid")}
        except Exception as exc:  # noqa: BLE001 - fail closed, keep any keys we already trust
            logger.warning("cloudflare access keys unavailable error=%s", type(exc).__name__)
            if not self._keys:
                raise AccessDenied("signing keys unavailable") from exc
            return
        self._keys = keys
        self._keys_loaded_at = time.monotonic()
