"""
Cooperage Authentication & Authorization

Supports three modes (tried in order):
  1. API key: static keys mapped to tenants (good for service-to-service)
  2. HS256 JWT: signed with a shared secret (simple deployments)
  3. OIDC / RS256 JWT: validated via JWKS from your SSO provider (enterprise SSO)

When auth is disabled (default for local dev), all requests get the "default" tenant.
"""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass

from cooperage.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthContext:
    """Attached to every request after authentication."""
    tenant_id: str
    # Which servers this tenant is allowed to use (None = all)
    allowed_servers: frozenset[str] | None = None
    # Resource quota overrides (None = use defaults from settings)
    max_sessions: int | None = None


# ── Tenant / API key store ───────────────────────────────────────────────────

@dataclass
class TenantConfig:
    tenant_id: str
    allowed_servers: list[str] | None = None  # None = all
    max_sessions: int | None = None


# api_key_hash → TenantConfig
_api_keys: dict[str, TenantConfig] = {}

# tenant_id → TenantConfig (for JWT-based lookup)
_tenants: dict[str, TenantConfig] = {}


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_api_keys() -> None:
    """Load API keys from the configured keys file.

    File format (JSON):
    [
      {
        "key": "sk-cooperage-abc123...",
        "tenant_id": "acme-corp",
        "allowed_servers": ["image-analyzer", "sim-runner"],
        "max_sessions": 10
      }
    ]
    """
    path = settings.api_keys_path
    if path is None or not path.exists():
        return

    try:
        data = json.loads(path.read_text())
        for entry in data:
            tenant = TenantConfig(
                tenant_id=entry["tenant_id"],
                allowed_servers=entry.get("allowed_servers"),
                max_sessions=entry.get("max_sessions"),
            )
            _api_keys[_hash_key(entry["key"])] = tenant
            _tenants[tenant.tenant_id] = tenant
        logger.info("Loaded %d API key(s) from %s", len(data), path)
    except Exception as e:
        logger.error("Failed to load API keys from %s: %s", path, e)


# ── API key auth ──────────────────────────────────────────────────────────────

def authenticate_api_key(key: str) -> AuthContext | None:
    """Validate an API key and return an AuthContext, or None if invalid."""
    tenant = _api_keys.get(_hash_key(key))
    if tenant is None:
        return None
    return AuthContext(
        tenant_id=tenant.tenant_id,
        allowed_servers=frozenset(tenant.allowed_servers) if tenant.allowed_servers else None,
        max_sessions=tenant.max_sessions,
    )


# ── HS256 JWT auth ────────────────────────────────────────────────────────────

def authenticate_jwt(token: str) -> AuthContext | None:
    """Validate an HS256 JWT and return an AuthContext, or None if invalid.

    Expects the JWT to contain a 'tenant_id' claim. Uses HS256 with
    settings.jwt_secret. Returns None if secret is not configured.
    """
    if not settings.jwt_secret:
        return None

    try:
        import jwt
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            return None

        # Look up tenant config for RBAC, fall back to bare tenant_id
        tenant = _tenants.get(tenant_id)
        return AuthContext(
            tenant_id=tenant_id,
            allowed_servers=(
                frozenset(tenant.allowed_servers) if tenant and tenant.allowed_servers else None
            ),
            max_sessions=tenant.max_sessions if tenant else None,
        )
    except Exception as e:
        logger.warning("HS256 JWT validation failed: %s", e)
        return None


# ── OIDC / JWKS (RS256) auth ─────────────────────────────────────────────────

_jwk_client = None
_jwk_client_lock = threading.Lock()


def _get_jwk_client():
    """Lazily create and cache a PyJWKClient for the configured OIDC issuer."""
    global _jwk_client
    if _jwk_client is not None:
        return _jwk_client

    with _jwk_client_lock:
        if _jwk_client is not None:
            return _jwk_client

        issuer = settings.oidc_issuer_url
        if not issuer:
            return None

        import httpx
        # Fetch OIDC discovery document to find the JWKS URI
        discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            resp = httpx.get(discovery_url, timeout=10)
            resp.raise_for_status()
            jwks_uri = resp.json()["jwks_uri"]
        except Exception as e:
            logger.error("Failed to fetch OIDC discovery from %s: %s", discovery_url, e)
            return None

        from jwt import PyJWKClient
        _jwk_client = PyJWKClient(jwks_uri, cache_jwk_set=True, lifespan=300)
        logger.info("OIDC JWKS client initialized from %s", jwks_uri)
        return _jwk_client


def authenticate_oidc(token: str) -> AuthContext | None:
    """Validate an OIDC/RS256 JWT via JWKS and return an AuthContext.

    The tenant_id is extracted from the claim specified by settings.oidc_tenant_claim
    (default: "tid" for Azure AD). Falls back to "sub" if the tenant claim is missing.
    """
    client = _get_jwk_client()
    if client is None:
        return None

    try:
        import jwt
        signing_key = client.get_signing_key_from_jwt(token)

        decode_options = {
            "algorithms": ["RS256", "RS384", "RS512"],
            "key": signing_key.key,
        }
        if settings.oidc_audience:
            decode_options["audience"] = settings.oidc_audience
        if settings.oidc_issuer_url:
            decode_options["issuer"] = settings.oidc_issuer_url

        payload = jwt.decode(token, **decode_options)

        # Extract tenant_id from the configured claim
        tenant_claim = settings.oidc_tenant_claim
        tenant_id = payload.get(tenant_claim) or payload.get("sub")
        if not tenant_id:
            logger.warning("OIDC token missing both %r and 'sub' claims", tenant_claim)
            return None

        # Look up tenant config for RBAC if one exists
        tenant = _tenants.get(tenant_id)
        return AuthContext(
            tenant_id=tenant_id,
            allowed_servers=(
                frozenset(tenant.allowed_servers) if tenant and tenant.allowed_servers else None
            ),
            max_sessions=tenant.max_sessions if tenant else None,
        )
    except Exception as e:
        logger.warning("OIDC JWT validation failed: %s", e)
        return None


# ── OIDC discovery (exposed for the UI) ──────────────────────────────────────

def get_oidc_config() -> dict | None:
    """Return OIDC settings needed by the UI for the login flow, or None if not configured."""
    if not settings.oidc_issuer_url or not settings.oidc_client_id:
        return None
    return {
        "issuer_url": settings.oidc_issuer_url,
        "client_id": settings.oidc_client_id,
        "scopes": settings.oidc_scopes,
        "authorization_endpoint": f"{settings.oidc_issuer_url.rstrip('/')}/authorize",
    }


# ── Request authentication (main entry point) ────────────────────────────────

def authenticate_request(headers: dict[str, str]) -> AuthContext:
    """Authenticate a request from its headers.

    Checks Authorization header for (in order):
      1. API key match
      2. HS256 JWT (if jwt_secret is configured)
      3. OIDC/RS256 JWT (if oidc_issuer_url is configured)

    Returns default tenant if auth is disabled.
    Raises PermissionError if auth is enabled but credentials are invalid.
    """
    if not settings.auth_enabled:
        return AuthContext(tenant_id="default")

    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PermissionError("Missing or invalid Authorization header")

    token = auth_header[7:]

    # Try API key first
    ctx = authenticate_api_key(token)
    if ctx is not None:
        return ctx

    # Try HS256 JWT
    ctx = authenticate_jwt(token)
    if ctx is not None:
        return ctx

    # Try OIDC/RS256 JWT
    ctx = authenticate_oidc(token)
    if ctx is not None:
        return ctx

    raise PermissionError("Invalid credentials")


def check_server_access(auth: AuthContext, server_name: str) -> None:
    """Raise PermissionError if the tenant can't use this server."""
    if auth.allowed_servers is not None and server_name not in auth.allowed_servers:
        raise PermissionError(
            f"Tenant {auth.tenant_id!r} is not authorized to use server {server_name!r}"
        )
