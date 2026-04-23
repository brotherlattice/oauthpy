"""Pluggable auth backend.

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

from typing import Protocol, runtime_checkable

from .models import AuthStatus, ProviderName


@runtime_checkable
class AuthBackend(Protocol):
    """Strategy for resolving a provider's auth state.

    ``status`` must be read-only — never mutate disk state.
    ``login`` may shell out to an interactive flow but must never write tokens
    to a location under oauthpy's control.
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


__all__ = ["AuthBackend", "SubprocessAuthBackend"]
