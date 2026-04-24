"""Auth helpers and pluggable auth backend.

The default backend delegates everything to the provider adapters, which in
turn shell out to the upstream CLI / SDK. This module exists so a future
direct-PKCE backend can slot in without breaking the public ``Client`` API:
implement :class:`AuthBackend` and pass it into :class:`oauthpy.client.Client`.

We intentionally do NOT:

* read ``~/.codex/auth.json``;
* parse Claude credential files;
* store tokens ourselves;
* print tokens anywhere.

If state cannot be determined safely, we return ``mode="unknown"``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import AuthSource, AuthStatus, ProviderName

_AUTH_SOURCES: tuple[AuthSource, ...] = ("auto", "oauthpy", "external")


def normalize_auth_source(auth_source: str) -> AuthSource:
    """Validate and normalize an auth source string."""

    if auth_source not in _AUTH_SOURCES:
        expected = ", ".join(_AUTH_SOURCES)
        raise ValueError(f"unknown auth_source {auth_source!r}; expected one of {expected}")
    return auth_source  # type: ignore[return-value]


def resolve_oauthpy_home(oauthpy_home: str | os.PathLike[str] | None = None) -> Path:
    """Resolve oauthpy's private state directory."""

    raw = oauthpy_home
    if raw is None:
        raw = os.environ.get("OAUTHPY_HOME")
    if raw is None:
        return Path.home() / ".oauthpy"
    return Path(raw).expanduser()


def provider_state_dir(
    provider: ProviderName,
    oauthpy_home: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the oauthpy-owned state directory for one provider."""

    return resolve_oauthpy_home(oauthpy_home) / provider


def ensure_private_dir(path: Path) -> None:
    """Create a user-private directory, best-effort on platforms without chmod."""

    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        # Windows ACLs are inherited from the user profile; chmod is best-effort.
        pass


@runtime_checkable
class AuthBackend(Protocol):
    """Strategy for resolving a provider's auth state.

    ``status`` must be read-only — never mutate disk state.
    ``login`` may shell out to an interactive flow but must never read or print
    tokens directly.
    """

    async def status(self, provider: ProviderName) -> AuthStatus:  # pragma: no cover - Protocol
        ...

    async def login(self, provider: ProviderName) -> None:  # pragma: no cover - Protocol
        ...


class SubprocessAuthBackend:
    """Default backend — delegates to each provider's ``auth_status``/``login``."""

    def __init__(self, providers: dict[ProviderName, object]) -> None:
        self._providers = providers

    async def status(self, provider: ProviderName) -> AuthStatus:
        adapter = self._providers[provider]
        return await adapter.auth_status()  # type: ignore[attr-defined]

    async def login(self, provider: ProviderName) -> None:
        adapter = self._providers[provider]
        await adapter.login()  # type: ignore[attr-defined]


__all__ = [
    "AuthBackend",
    "SubprocessAuthBackend",
    "ensure_private_dir",
    "normalize_auth_source",
    "provider_state_dir",
    "resolve_oauthpy_home",
]
