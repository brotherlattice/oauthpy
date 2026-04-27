from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._redact import redact


class OauthPyError(Exception):
    """Base class for all oauthpy errors."""

    def __init__(self, message: str = "", *args: object) -> None:
        super().__init__(redact(message), *args)


class UnsupportedProviderError(OauthPyError):
    """Raised when an unknown provider name is passed to Client(...)."""


class ProviderNotInstalledError(OauthPyError):
    """Raised when the underlying CLI or SDK for a provider is not available."""


class AuthRequiredError(OauthPyError):
    """Raised when a provider is installed but not authenticated."""


class ProtocolError(OauthPyError):
    """Raised when the provider returns malformed or unexpected output."""


class CommandExecutionError(OauthPyError):
    """Raised when a subprocess exits non-zero or cannot be started."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = redact(stderr) if stderr else None
        self.details = _redact_details(details or {})


class TimeoutExceededError(OauthPyError):
    """Raised when a run or stream exceeds its timeout."""


def _redact_details(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {str(key): _redact_details(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_details(item) for item in value]
    return value


__all__ = [
    "AuthRequiredError",
    "CommandExecutionError",
    "OauthPyError",
    "ProtocolError",
    "ProviderNotInstalledError",
    "TimeoutExceededError",
    "UnsupportedProviderError",
]
