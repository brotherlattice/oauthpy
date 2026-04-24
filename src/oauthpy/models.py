"""Normalized data models emitted by oauthpy providers.

These are deliberately small, frozen dataclasses. Providers preserve their raw
payload on ``Event.raw`` and ``RunResult.raw`` so advanced callers can drop
down a level when the normalized shape is not enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from ._redact import redact

ProviderName = Literal["codex", "claude"]
"""The set of providers oauthpy v0.1 supports."""

AuthSource = Literal["auto", "oauthpy", "external"]
"""Where oauthpy should look for provider auth/config."""

TransportName = Literal["codex-cli-jsonl", "claude-agent-sdk"]
"""How a provider is being driven under the hood."""

JsonScalar = str | int | float | bool | None
"""Flat JSON-compatible diagnostic value."""


class EventKind(str, Enum):
    """Normalized event kinds emitted during a run.

    Values are strings so they serialize cleanly and compare by identity against
    the literal names callers may type.
    """

    MESSAGE = "message"
    REASONING = "reasoning"
    PLAN = "plan"
    TOOL = "tool"
    COMMAND = "command"
    FILE_CHANGE = "file_change"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class Event:
    """A single normalized event from a provider run.

    ``raw`` always contains the underlying provider payload (a dict for Codex
    JSONL rows, or an object from the Claude SDK). ``text`` and ``timestamp``
    are best-effort extractions convenient for display.
    """

    kind: EventKind
    text: str | None = None
    timestamp: float | None = None
    raw: Any = None

    def __repr__(self) -> str:
        text_repr = redact(self.text) if self.text else None
        return (
            f"Event(kind={self.kind.value!r}, text={text_repr!r}, " f"timestamp={self.timestamp!r})"
        )


@dataclass(frozen=True)
class Usage:
    """Optional token/cost accounting for a single run.

    Providers that do not expose usage leave all fields ``None``.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class RunResult:
    """The result of a one-shot ``Client.run(...)`` call."""

    provider: ProviderName
    transport: TransportName
    model: str | None
    text: str
    events: tuple[Event, ...]
    elapsed_s: float
    cwd: str | None
    usage: Usage | None = None
    raw: Any = None

    def __repr__(self) -> str:
        return (
            f"RunResult(provider={self.provider!r}, transport={self.transport!r}, "
            f"model={self.model!r}, text_len={len(self.text)}, "
            f"events={len(self.events)}, elapsed_s={self.elapsed_s:.3f})"
        )


AuthMode = Literal["oauth", "env", "login-state", "api-key", "cloud", "unknown"]
"""How a provider is currently authenticated, from our point of view."""


@dataclass(frozen=True)
class AuthStatus:
    """Best-effort, read-only snapshot of a provider's auth state.

    ``details`` is a free-form mapping that providers populate with whatever
    non-secret diagnostic information they can surface (which binary was found
    on PATH, which env var was set, etc.). Never contains tokens.
    """

    provider: ProviderName
    installed: bool
    authenticated: bool
    mode: AuthMode
    details: dict[str, JsonScalar] = field(default_factory=dict)


__all__ = [
    "AuthMode",
    "AuthSource",
    "AuthStatus",
    "Event",
    "EventKind",
    "JsonScalar",
    "ProviderName",
    "RunResult",
    "TransportName",
    "Usage",
]
