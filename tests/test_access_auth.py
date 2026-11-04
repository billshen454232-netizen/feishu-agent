import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.access_auth import AccessDenied, CloudflareAccessVerifier
from src.admin_app import create_admin_app
from src.execution_report import ExecutionReportStore

TEAM = "team.cloudflareaccess.com"
AUD = "aud-tag-123"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return key, jwk


SIGNING_KEY, PUBLIC_JWK = _keypair()
OTHER_KEY, _ = _keypair()


def _token(*, key=SIGNING_KEY, kid="k1", aud=AUD, iss="https://" + TEAM, exp_delta=300, email="owner@example.com"):
    now = int(time.time())
    return jwt.encode(
        {"aud": [aud], "iss": iss, "iat": now, "exp": now + exp_delta, "email": email, "sub": "u1"},
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _verifier(fetches=None):
    def handler(request):
        if fetches is not None:
            fetches.append(str(request.url))
        assert str(request.url) == "https://{}/cdn-cgi/access/certs".format(TEAM)
        return httpx.Response(200, json={"keys": [dict(PUBLIC_JWK, kid="k1")]})

    return CloudflareAccessVerifier(TEAM, AUD, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_valid_token_returns_claims_and_keys_are_cached():
    fetches = []
    verifier = _verifier(fetches)

    assert (await verifier.verify(_token()))["email"] == "owner@example.com"
    await verifier.verify(_token())
    assert len(fetches) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    "",
    "not-a-jwt",
    _token(key=OTHER_KEY),                       # forged signature
    _token(aud="another-app"),                   # token for another Access app
    _token(iss="https://evil.cloudflareaccess.com"),
    _token(exp_delta=-3600),                     # expired
    _token(kid="unknown"),
])
async def test_invalid_tokens_are_denied(token):
    with pytest.raises(AccessDenied):
        await _verifier().verify(token)


def _store(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1:8901")


PROXIED = {"X-Admin-Proxy": "cloudflare-access"}


def test_proxied_requests_need_a_valid_access_token(tmp_path):
    client = _client(create_admin_app(_store(tmp_path), access_verifier=_verifier()))

    assert client.get("/api/summary", headers=PROXIED).status_code == 403
    assert client.get("/api/summary", headers=dict(PROXIED, **{"Cf-Access-Jwt-Assertion": _token(key=OTHER_KEY)})).status_code == 403
    assert client.get("/api/summary", headers=dict(PROXIED, **{"Cf-Access-Jwt-Assertion": _token()})).status_code == 200
    client.cookies.set("CF_Authorization", _token())
    assert client.get("/api/summary", headers=PROXIED).status_code == 200


def test_proxied_requests_are_refused_when_access_is_not_configured(tmp_path):
    client = _client(create_admin_app(_store(tmp_path)))

    response = client.get("/api/summary", headers=dict(PROXIED, **{"Cf-Access-Jwt-Assertion": _token()}))

    assert response.status_code == 403
    assert client.get("/api/summary", headers={"X-Admin-Proxy": "anything"}).status_code == 403


def test_ssh_tunnel_requests_still_work_without_token(tmp_path):
    client = _client(create_admin_app(_store(tmp_path), access_verifier=_verifier()))

    assert client.get("/api/summary").status_code == 200


def test_operator_email_is_logged_for_changes(tmp_path, caplog):
    import logging

    store = _store(tmp_path)
    client = _client(create_admin_app(store, access_verifier=_verifier()))
    headers = dict(PROXIED, **{"Cf-Access-Jwt-Assertion": _token(), "X-Admin-Request": "1"})

    with caplog.at_level(logging.INFO, logger="src.admin_app"):
        client.put("/api/settings/read-success-mode", headers=headers, json={"mode": "template"})

    assert "operator=owner@example.com" in caplog.text


def test_access_config_requires_both_fields(tmp_path):
    from src.config import ConfigError, _load_admin

    assert _load_admin({"access_team_domain": TEAM, "access_aud": AUD}).access_aud == AUD
    with pytest.raises(ConfigError, match="同时填写"):
        _load_admin({"access_team_domain": TEAM})
    with pytest.raises(ConfigError, match="cloudflareaccess.com"):
        _load_admin({"access_team_domain": "example.com", "access_aud": AUD})


def test_workers_can_import_the_app_without_pyjwt():
    """PyJWT is a Gateway-only dependency (requirements-gateway.txt); Workers must start without it."""
    import subprocess
    import sys
    from pathlib import Path

    code = "import sys; sys.modules['jwt'] = None; import src.access_auth, src.admin_app, src.app"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
