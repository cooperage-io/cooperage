"""
Cooperage Authentication & Authorization — Plugin Interface

The open-source core ships with single-tenant, no-auth defaults.
Enterprise auth (API keys, JWT, OIDC/SSO, per-tenant RBAC, audit)
is provided by the cooperage-enterprise package, which registers
providers via the hooks below.

When no enterprise provider is registered, every request gets the
"default" tenant with unrestricted access.
"""

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthContext:
    """Attached to every request after authentication."""
    tenant_id: str
    # Which servers this tenant is allowed to use (None = all)
    allowed_servers: frozenset[str] | None = None
    # Resource quota overrides (None = use defaults from settings)
    max_sessions: int | None = None


# ── Plugin protocol ──────────────────────────────────────────────────────────

@runtime_checkable
class AuthProvider(Protocol):
    """Interface that enterprise auth packages implement."""

    def authenticate_request(self, headers: dict[str, str]) -> AuthContext:
        """Authenticate a request from its headers.
        Returns AuthContext on success, raises PermissionError on failure."""
        ...

    def check_server_access(self, auth: AuthContext, server_name: str) -> None:
        """Raise PermissionError if the tenant can't use this server."""
        ...

    def on_startup(self) -> None:
        """Called once when the gateway starts (load keys, warm caches, etc.)."""
        ...

    def get_oidc_config(self) -> dict | None:
        """Return OIDC settings for the UI login flow, or None."""
        ...


# ── Default (no-op) provider ─────────────────────────────────────────────────

_DEFAULT_AUTH = AuthContext(tenant_id="default")


class _NoOpProvider:
    """Default provider — no auth, single tenant, unrestricted access."""

    def authenticate_request(self, headers: dict[str, str]) -> AuthContext:
        return _DEFAULT_AUTH

    def check_server_access(self, auth: AuthContext, server_name: str) -> None:
        pass  # all access allowed

    def on_startup(self) -> None:
        pass

    def get_oidc_config(self) -> dict | None:
        return None


# ── Provider registry ────────────────────────────────────────────────────────

_provider: AuthProvider | _NoOpProvider = _NoOpProvider()


def register_auth_provider(provider: AuthProvider) -> None:
    """Register an enterprise auth provider. Called by cooperage-enterprise on import."""
    global _provider
    _provider = provider
    logger.info("Enterprise auth provider registered: %s", type(provider).__name__)


def get_auth_provider() -> AuthProvider | _NoOpProvider:
    """Return the current auth provider."""
    return _provider


# ── Convenience wrappers (used by gateway code) ─────────────────────────────

def authenticate_request(headers: dict[str, str]) -> AuthContext:
    return _provider.authenticate_request(headers)


def check_server_access(auth: AuthContext, server_name: str) -> None:
    _provider.check_server_access(auth, server_name)


def get_oidc_config() -> dict | None:
    return _provider.get_oidc_config()
