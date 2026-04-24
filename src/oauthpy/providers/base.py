"""Provider interface.

``Provider`` is an abstract base class rather than a ``Protocol`` so we can
share default behavior (``available()`` deriving from ``auth_status()``,
``run()`` as a drain of ``stream()``) and so ``isinstance`` checks work for
tests.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any

from ..models import AuthStatus, Event, EventKind, ProviderName, RunResult, TransportName, Usage


class Provider(ABC):
    """Abstract base class for oauthpy provider adapters."""

    #: Provider identifier, set on each concrete subclass.
    name: ProviderName
    #: Transport identifier — how we're driving this provider under the hood.
    transport: TransportName

    @abstractmethod
    async def auth_status(self) -> AuthStatus:
        """Return a best-effort, read-only snapshot of auth state."""

    @abstractmethod
    async def login(self) -> None:
        """Shell out to the provider's official login flow."""

    @abstractmethod
    def stream(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        """Execute a one-shot prompt and stream normalized events as they arrive."""

    async def run(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> RunResult:
        """Default one-shot: drain :meth:`stream` and build a :class:`RunResult`.

        Subclasses only need to override :meth:`_final_text` if their text
        aggregation differs from "join all MESSAGE.text with newlines."
        """

        start = time.monotonic()
        events: list[Event] = []
        async for event in self.stream(
            prompt,
            cwd=cwd,
            model=model,
            timeout=timeout,
            env=env,
            provider_options=provider_options,
        ):
            events.append(event)
        elapsed = time.monotonic() - start
        return RunResult(
            provider=self.name,
            transport=self.transport,
            model=model,
            text=self._final_text(events),
            events=tuple(events),
            elapsed_s=elapsed,
            cwd=os.fspath(cwd) if cwd is not None else None,
            usage=self._usage(events),
        )

    def _final_text(self, events: list[Event]) -> str:
        """Aggregate event text into the single ``RunResult.text`` string.

        Default: join every ``MESSAGE`` event's text with newlines. Subclasses
        may override — e.g. Claude prefers the final ``DONE`` payload.
        """

        return "\n".join(e.text for e in events if e.kind is EventKind.MESSAGE and e.text)

    def _usage(self, events: list[Event]) -> Usage | None:
        """Extract optional provider usage from a drained event list."""

        return None

    async def available(self) -> bool:
        """Whether this provider is installed and authenticated.

        Defaults to ``auth_status().installed and authenticated``. Subclasses
        may override if they have a cheaper check.
        """

        status = await self.auth_status()
        return status.installed and status.authenticated


__all__ = ["Provider"]
