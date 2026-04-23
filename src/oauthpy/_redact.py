"""Best-effort secret redaction for logs, reprs, and exception messages.

This module is intentionally conservative. It does not attempt to detect every
possible secret shape — that's impossible — but it catches the common ones we
know show up when talking to Codex and Claude: bearer tokens, OpenAI/Anthropic
API keys, and explicit environment values keyed on ``*_TOKEN`` / ``*_KEY`` /
``*_SECRET``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_MASK = "***REDACTED***"

# Secret-shape patterns combined into a single alternation — one regex pass per
# string beats six on the hot path (every exception message and every Event
# __repr__ runs through here).
_SECRET_RE = re.compile(
    "|".join(
        (
            r"sk-ant-[A-Za-z0-9_\-]{10,}",
            r"sk-[A-Za-z0-9_\-]{10,}",
            r"gh[pous]_[A-Za-z0-9]{20,}",
            r"(?i:bearer)\s+[A-Za-z0-9_\-\.=]+",
            r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
            r"\b[a-f0-9]{32,}\b",
        )
    )
)

# Env-var keys whose values should always be redacted.
_SENSITIVE_KEY_RE = re.compile(r"(?i)(token|secret|api[_\-]?key|password|passphrase)")


def redact(value: str | None) -> str:
    """Return ``value`` with any recognized secret substrings masked.

    Passing ``None`` returns the empty string. Any non-string ``value`` is
    stringified via ``str()`` first — this keeps :func:`redact` safe to call
    from ``__repr__``.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return value
    return _SECRET_RE.sub(_MASK, value)


def redact_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """Return a copy of ``env`` with values of sensitive keys masked.

    Used for diagnostics when a subprocess call fails — we want to log which
    keys were set without spilling their values.
    """

    if env is None:
        return {}
    masked: dict[str, str] = {}
    for key, val in env.items():
        if _SENSITIVE_KEY_RE.search(key):
            masked[key] = _MASK
        else:
            masked[key] = redact(val)
    return masked


def redact_argv(argv: list[str]) -> list[str]:
    """Return a copy of ``argv`` with any recognized secret substrings masked.

    Secrets should not appear on a command line — but if they do (e.g. because
    a user passed a token via ``--with-api-key`` or ``env`` forwarding), this
    stops them leaking into error messages.
    """

    return [redact(arg) for arg in argv]


def safe_repr(obj: Any) -> str:
    """Return ``repr(obj)`` with secret substrings redacted."""

    return redact(repr(obj))


__all__ = ["redact", "redact_argv", "redact_env", "safe_repr"]
