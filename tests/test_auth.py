"""
Auth tests — covers API key, HS256 JWT, OIDC/RS256, request auth, and RBAC.
External network calls (JWKS) are mocked.
"""
import hashlib
import json
import time
from unittest.mock import MagicMock

import pytest

import cooperage.core.auth as auth_module
from cooperage.core.auth import (
    AuthContext,
    TenantConfig,
    authenticate_api_key,
    authenticate_jwt,
    authenticate_request,
    check_server_access,
    get_oidc_config,
    load_api_keys,
)


@pytest.fixture(autouse=True)
def reset_auth_state():
    """Clear in-memory API key / tenant state and JWK client between tests."""
    auth_module._api_keys.clear()
    auth_module._tenants.clear()
    auth_module._jwk_client = None
    yield
    auth_module._api_keys.clear()
    auth_module._tenants.clear()
    auth_module._jwk_client = None


# ── load_api_keys ─────────────────────────────────────────────────────────────

def test_load_api_keys_populates_store(tmp_path, monkeypatch):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps([
        {"key": "sk-test-1", "tenant_id": "acme", "allowed_servers": ["sim"], "max_sessions": 5},
    ]))
    monkeypatch.setattr("cooperage.core.auth.settings.api_keys_path", keys_file)
    load_api_keys()
    assert len(auth_module._api_keys) == 1
    assert "acme" in auth_module._tenants


def test_load_api_keys_missing_file_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr("cooperage.core.auth.settings.api_keys_path", tmp_path / "missing.json")
    load_api_keys()
    assert len(auth_module._api_keys) == 0


def test_load_api_keys_none_path_is_noop(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.api_keys_path", None)
    load_api_keys()
    assert len(auth_module._api_keys) == 0


def test_load_api_keys_invalid_json_logs_and_continues(tmp_path, monkeypatch):
    keys_file = tmp_path / "bad.json"
    keys_file.write_text("not json")
    monkeypatch.setattr("cooperage.core.auth.settings.api_keys_path", keys_file)
    load_api_keys()  # should not raise
    assert len(auth_module._api_keys) == 0


# ── authenticate_api_key ──────────────────────────────────────────────────────

def test_authenticate_api_key_valid():
    key = "sk-cooperage-abc123"
    tenant = TenantConfig(tenant_id="acme", allowed_servers=["sim"], max_sessions=3)
    auth_module._api_keys[hashlib.sha256(key.encode()).hexdigest()] = tenant
    ctx = authenticate_api_key(key)
    assert ctx is not None
    assert ctx.tenant_id == "acme"
    assert ctx.allowed_servers == frozenset(["sim"])
    assert ctx.max_sessions == 3


def test_authenticate_api_key_invalid_returns_none():
    assert authenticate_api_key("sk-wrong") is None


def test_authenticate_api_key_no_allowed_servers_means_all():
    key = "sk-open"
    tenant = TenantConfig(tenant_id="open-tenant")
    auth_module._api_keys[hashlib.sha256(key.encode()).hexdigest()] = tenant
    ctx = authenticate_api_key(key)
    assert ctx.allowed_servers is None


# ── authenticate_jwt (HS256) ──────────────────────────────────────────────────

def test_authenticate_jwt_no_secret_returns_none(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", None)
    assert authenticate_jwt("any.token.here") is None


def test_authenticate_jwt_valid(monkeypatch):
    import jwt
    secret = "test-secret-padded-to-32-bytes!!"
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", secret)
    token = jwt.encode({"tenant_id": "acme"}, secret, algorithm="HS256")
    ctx = authenticate_jwt(token)
    assert ctx is not None
    assert ctx.tenant_id == "acme"


def test_authenticate_jwt_with_tenant_config(monkeypatch):
    import jwt
    secret = "test-secret-padded-to-32-bytes!!"
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", secret)
    auth_module._tenants["acme"] = TenantConfig(tenant_id="acme", max_sessions=10)
    token = jwt.encode({"tenant_id": "acme"}, secret, algorithm="HS256")
    ctx = authenticate_jwt(token)
    assert ctx.max_sessions == 10


def test_authenticate_jwt_missing_tenant_id_returns_none(monkeypatch):
    import jwt
    secret = "test-secret-padded-to-32-bytes!!"
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", secret)
    token = jwt.encode({"sub": "user1"}, secret, algorithm="HS256")
    assert authenticate_jwt(token) is None


def test_authenticate_jwt_wrong_secret_returns_none(monkeypatch):
    import jwt
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", "correct-secret-padded-to-32-bytes!")
    token = jwt.encode({"tenant_id": "acme"}, "wrong-secret-padded-to-32-bytes!!", algorithm="HS256")
    assert authenticate_jwt(token) is None


def test_authenticate_jwt_expired_returns_none(monkeypatch):
    import jwt
    secret = "test-secret-padded-to-32-bytes!!"
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", secret)
    token = jwt.encode({"tenant_id": "acme", "exp": int(time.time()) - 60}, secret, algorithm="HS256")
    assert authenticate_jwt(token) is None


# ── authenticate_oidc ─────────────────────────────────────────────────────────

def test_authenticate_oidc_no_issuer_returns_none(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", None)
    from cooperage.core.auth import authenticate_oidc
    assert authenticate_oidc("any.token") is None


def _make_rs256_token(payload: dict) -> tuple:
    """Return (private_key, public_key, token) for RS256 tests."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    token = pyjwt.encode(payload, private_key, algorithm="RS256")
    return private_key, private_key.public_key(), token


def test_authenticate_oidc_valid_rs256(monkeypatch):
    """Mock the JWK client to return a valid RS256-decoded payload."""
    issuer = "https://login.example.com"
    _, pub_key, token = _make_rs256_token({
        "tid": "azure-tenant-1", "sub": "user1", "iss": issuer,
    })

    mock_signing_key = MagicMock()
    mock_signing_key.key = pub_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", issuer)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_audience", None)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_tenant_claim", "tid")
    auth_module._jwk_client = mock_client

    from cooperage.core.auth import authenticate_oidc
    ctx = authenticate_oidc(token)
    assert ctx is not None
    assert ctx.tenant_id == "azure-tenant-1"


def test_authenticate_oidc_falls_back_to_sub(monkeypatch):
    issuer = "https://login.example.com"
    _, pub_key, token = _make_rs256_token({"sub": "user-sub-id", "iss": issuer})

    mock_signing_key = MagicMock()
    mock_signing_key.key = pub_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", issuer)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_audience", None)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_tenant_claim", "tid")
    auth_module._jwk_client = mock_client

    from cooperage.core.auth import authenticate_oidc
    ctx = authenticate_oidc(token)
    assert ctx is not None
    assert ctx.tenant_id == "user-sub-id"


def test_authenticate_oidc_bad_token_returns_none(monkeypatch):
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.side_effect = Exception("bad token")
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", "https://login.example.com")
    auth_module._jwk_client = mock_client

    from cooperage.core.auth import authenticate_oidc
    assert authenticate_oidc("garbage") is None


# ── get_oidc_config ───────────────────────────────────────────────────────────

def test_get_oidc_config_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", None)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_client_id", None)
    assert get_oidc_config() is None


def test_get_oidc_config_returns_dict(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", "https://login.example.com")
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_client_id", "my-client-id")
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_scopes", "openid profile")
    cfg = get_oidc_config()
    assert cfg["client_id"] == "my-client-id"
    assert cfg["issuer_url"] == "https://login.example.com"
    assert "authorization_endpoint" in cfg


# ── authenticate_request ──────────────────────────────────────────────────────

def test_authenticate_request_disabled_returns_default(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.auth_enabled", False)
    ctx = authenticate_request({})
    assert ctx.tenant_id == "default"


def test_authenticate_request_missing_header_raises(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.auth_enabled", True)
    with pytest.raises(PermissionError, match="Missing"):
        authenticate_request({})


def test_authenticate_request_non_bearer_raises(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.auth_enabled", True)
    with pytest.raises(PermissionError, match="Missing"):
        authenticate_request({"authorization": "Basic abc123"})


def test_authenticate_request_valid_api_key(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.auth_enabled", True)
    key = "sk-valid"
    auth_module._api_keys[hashlib.sha256(key.encode()).hexdigest()] = TenantConfig(tenant_id="t1")
    ctx = authenticate_request({"authorization": f"Bearer {key}"})
    assert ctx.tenant_id == "t1"


def test_authenticate_request_invalid_all_methods_raises(monkeypatch):
    monkeypatch.setattr("cooperage.core.auth.settings.auth_enabled", True)
    monkeypatch.setattr("cooperage.core.auth.settings.jwt_secret", None)
    monkeypatch.setattr("cooperage.core.auth.settings.oidc_issuer_url", None)
    with pytest.raises(PermissionError, match="Invalid credentials"):
        authenticate_request({"authorization": "Bearer invalid-token"})


# ── check_server_access ───────────────────────────────────────────────────────

def test_check_server_access_allowed():
    ctx = AuthContext(tenant_id="t1", allowed_servers=frozenset(["sim", "cfd"]))
    check_server_access(ctx, "sim")  # should not raise


def test_check_server_access_denied():
    ctx = AuthContext(tenant_id="t1", allowed_servers=frozenset(["sim"]))
    with pytest.raises(PermissionError, match="not authorized"):
        check_server_access(ctx, "cfd")


def test_check_server_access_none_means_all():
    ctx = AuthContext(tenant_id="t1", allowed_servers=None)
    check_server_access(ctx, "anything")  # should not raise
