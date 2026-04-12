"""
REST adapter logic tests — auth headers, env var resolution, param routing.
Tests the server functions directly (no Docker needed).
"""

import base64


import importlib.util
from pathlib import Path

# Import the adapter server module without polluting sys.path
_adapter_path = Path(__file__).parent.parent / "servers" / "rest-adapter" / "server.py"
_spec = importlib.util.spec_from_file_location("rest_adapter_server", _adapter_path)
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)


# ── env var resolution ───────────────────────────────────────────────────────


def test_resolve_env_replaces_var(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret123")
    _resolve_env = _adapter._resolve_env
    assert _resolve_env("Bearer ${MY_KEY}") == "Bearer secret123"


def test_resolve_env_missing_var():
    _resolve_env = _adapter._resolve_env
    assert _resolve_env("${NONEXISTENT_VAR}") == ""


def test_resolve_env_none():
    _resolve_env = _adapter._resolve_env
    assert _resolve_env(None) is None


def test_resolve_env_no_vars():
    _resolve_env = _adapter._resolve_env
    assert _resolve_env("plain string") == "plain string"


def test_resolve_env_multiple_vars(monkeypatch):
    monkeypatch.setenv("A", "hello")
    monkeypatch.setenv("B", "world")
    _resolve_env = _adapter._resolve_env
    assert _resolve_env("${A} ${B}") == "hello world"


# ── auth header building ────────────────────────────────────────────────────


def test_build_auth_headers_none():
    _build_auth_headers = _adapter._build_auth_headers
    assert _build_auth_headers({"type": "none"}) == {}


def test_build_auth_headers_bearer(monkeypatch):
    monkeypatch.setenv("TOKEN", "abc123")
    _build_auth_headers = _adapter._build_auth_headers
    headers = _build_auth_headers({"type": "bearer", "token": "${TOKEN}"})
    assert headers == {"Authorization": "Bearer abc123"}


def test_build_auth_headers_api_key(monkeypatch):
    monkeypatch.setenv("KEY", "mykey")
    _build_auth_headers = _adapter._build_auth_headers
    headers = _build_auth_headers({
        "type": "api-key",
        "api_key": "${KEY}",
        "api_key_header": "X-Custom-Key",
    })
    assert headers == {"X-Custom-Key": "mykey"}


def test_build_auth_headers_basic(monkeypatch):
    monkeypatch.setenv("USER", "admin")
    monkeypatch.setenv("PASS", "secret")
    _build_auth_headers = _adapter._build_auth_headers
    headers = _build_auth_headers({
        "type": "basic",
        "username": "${USER}",
        "password": "${PASS}",
    })
    expected = base64.b64encode(b"admin:secret").decode()
    assert headers == {"Authorization": f"Basic {expected}"}
