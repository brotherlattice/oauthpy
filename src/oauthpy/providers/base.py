"""Provider interface.

``Provider`` is an abstract base class rather than a ``Protocol`` so we can
share default behavior (``available()`` deriving from ``auth_status()``,
``run()`` as a drain of ``stream()``) and so ``isinstance`` checks work for
tests.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from .._redact import redact
from ..errors import CommandExecutionError, ProtocolError, TimeoutExceededError
from ..models import AuthStatus, Event, EventKind, ProviderName, RunResult, TransportName, Usage


@dataclass(frozen=True)
class RetryPolicy:
    """Common retry policy parsed from ``provider_options``."""

    max_retries: int = 0
    backoff_s: float = 1.0
    backoff_max_s: float = 8.0
    jitter_s: float = 0.25
    retry_on_timeout: bool = False

    @property
    def enabled(self) -> bool:
        return self.max_retries > 0

    def delay_for_retry(self, retry_index: int) -> float:
        base = min(self.backoff_max_s, self.backoff_s * (2 ** max(retry_index - 1, 0)))
        jitter = random.uniform(0.0, self.jitter_s) if self.jitter_s > 0 else 0.0
        return min(self.backoff_max_s, base + jitter)


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    reason: str


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

    async def stream(
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

        policy, call_options = _split_retry_options(provider_options)
        attempts: list[dict[str, Any]] = []

        for attempt in range(1, policy.max_retries + 2):
            events_yielded = 0
            attempt_start = time.monotonic()
            try:
                async for event in self._stream_once(
                    prompt,
                    cwd=cwd,
                    model=model,
                    timeout=timeout,
                    env=env,
                    provider_options=call_options,
                ):
                    events_yielded += 1
                    yield event
                return
            except Exception as exc:
                if not policy.enabled:
                    raise
                decision = self._retry_decision(
                    exc,
                    policy=policy,
                    events_yielded=events_yielded,
                )
                attempt_info = self._retry_attempt_details(
                    exc,
                    attempt=attempt,
                    events_received=events_yielded,
                    elapsed_s=time.monotonic() - attempt_start,
                    decision=decision,
                    cwd=cwd,
                    model=model,
                    provider_options=call_options,
                )
                attempts.append(attempt_info)
                if not policy.enabled or attempt > policy.max_retries or not decision.retryable:
                    raise self._retry_exhausted_error(exc, attempts) from exc
                delay = policy.delay_for_retry(attempt)
                attempt_info["backoff_s"] = delay
                await asyncio.sleep(delay)

        # ``for`` always returns or raises, but keep mypy/pyright satisfied.
        raise RuntimeError("unreachable retry loop state")  # pragma: no cover

    @abstractmethod
    def _stream_once(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        """Execute one provider attempt with retry options already stripped."""

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
        policy, call_options = _split_retry_options(provider_options)
        attempts: list[dict[str, Any]] = []
        total_backoff_s = 0.0

        for attempt in range(1, policy.max_retries + 2):
            attempt_start = time.monotonic()
            events: list[Event] = []
            try:
                async for event in self._stream_once(
                    prompt,
                    cwd=cwd,
                    model=model,
                    timeout=timeout,
                    env=env,
                    provider_options=call_options,
                ):
                    events.append(event)
                elapsed = time.monotonic() - start
                return RunResult(
                    provider=self.name,
                    transport=self._transport_for_events(events),
                    model=model,
                    text=self._final_text(events),
                    events=tuple(events),
                    elapsed_s=elapsed,
                    cwd=os.fspath(cwd) if cwd is not None else None,
                    usage=self._usage(events),
                    raw=_merge_run_raw(
                        self._raw_for_events(events),
                        _retry_success_raw(
                            policy,
                            attempts=attempts,
                            attempt=attempt,
                            total_backoff_s=total_backoff_s,
                        ),
                    ),
                )
            except Exception as exc:
                if not policy.enabled:
                    raise
                decision = self._retry_decision(
                    exc,
                    policy=policy,
                    events_yielded=len(events),
                )
                attempt_info = self._retry_attempt_details(
                    exc,
                    attempt=attempt,
                    events_received=len(events),
                    elapsed_s=time.monotonic() - attempt_start,
                    decision=decision,
                    cwd=cwd,
                    model=model,
                    provider_options=call_options,
                )
                attempts.append(attempt_info)
                if not policy.enabled or attempt > policy.max_retries or not decision.retryable:
                    raise self._retry_exhausted_error(exc, attempts) from exc
                delay = policy.delay_for_retry(attempt)
                attempt_info["backoff_s"] = delay
                total_backoff_s += delay
                await asyncio.sleep(delay)

        raise RuntimeError("unreachable retry loop state")  # pragma: no cover

    def _final_text(self, events: list[Event]) -> str:
        """Aggregate event text into the single ``RunResult.text`` string.

        Default: join every ``MESSAGE`` event's text with newlines. Subclasses
        may override — e.g. Claude prefers the final ``DONE`` payload.
        """

        return "\n".join(e.text for e in events if e.kind is EventKind.MESSAGE and e.text)

    def _usage(self, events: list[Event]) -> Usage | None:
        """Extract optional provider usage from a drained event list."""

        return None

    def _transport_for_events(self, events: list[Event]) -> TransportName:
        """Return the transport used by the completed event sequence."""

        return self.transport

    def _raw_for_events(self, events: list[Event]) -> Any:
        """Return optional provider-level raw payload for a completed run."""

        return None

    def _retry_decision(
        self,
        exc: Exception,
        *,
        policy: RetryPolicy,
        events_yielded: int,
    ) -> RetryDecision:
        """Return whether ``exc`` is safe to retry for this provider."""

        if events_yielded:
            return RetryDecision(False, "events_already_yielded")
        if isinstance(exc, TimeoutExceededError):
            return RetryDecision(policy.retry_on_timeout, "timeout")
        return RetryDecision(False, "not_retryable")

    def _retry_context(
        self,
        *,
        cwd: str | os.PathLike[str] | None,
        model: str | None,
        provider_options: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "provider": self.name,
            "transport": self.transport,
            "model": model,
            "cwd": os.fspath(cwd) if cwd is not None else None,
        }

    def _retry_attempt_details(
        self,
        exc: Exception,
        *,
        attempt: int,
        events_received: int,
        elapsed_s: float,
        decision: RetryDecision,
        cwd: str | os.PathLike[str] | None,
        model: str | None,
        provider_options: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        details = getattr(exc, "details", None)
        return {
            "attempt": attempt,
            "retryable": decision.retryable,
            "reason": decision.reason,
            "exception_type": type(exc).__name__,
            "message": redact(str(exc))[:2000],
            "returncode": getattr(exc, "returncode", None),
            "stderr": redact(getattr(exc, "stderr", None) or "")[-2000:] or None,
            "events_received": events_received,
            "elapsed_s": elapsed_s,
            "context": self._retry_context(
                cwd=cwd,
                model=model,
                provider_options=provider_options,
            ),
            "details": details if isinstance(details, Mapping) else None,
        }

    def _retry_exhausted_error(
        self,
        exc: Exception,
        attempts: list[dict[str, Any]],
    ) -> Exception:
        if len(attempts) <= 1:
            return exc
        message = (
            f"{self.name} run failed after {len(attempts)} attempts: {exc}\n"
            f"retry attempts:\n{_format_attempts(attempts)}"
        )
        return CommandExecutionError(
            message,
            returncode=getattr(exc, "returncode", None),
            stderr=getattr(exc, "stderr", None),
            details={
                "provider": self.name,
                "transport": self.transport,
                "attempts": attempts,
            },
        )

    async def available(self) -> bool:
        """Whether this provider is installed and authenticated.

        Defaults to ``auth_status().installed and authenticated``. Subclasses
        may override if they have a cheaper check.
        """

        status = await self.auth_status()
        return status.installed and status.authenticated


def _split_retry_options(
    provider_options: Mapping[str, Any] | None,
) -> tuple[RetryPolicy, dict[str, Any]]:
    options = dict(provider_options or {})
    max_retries = _non_negative_int(options.pop("max_retries", 0), "max_retries")
    backoff_s = _non_negative_float(options.pop("retry_backoff_s", 1.0), "retry_backoff_s")
    backoff_max_s = _non_negative_float(
        options.pop("retry_backoff_max_s", 8.0),
        "retry_backoff_max_s",
    )
    jitter_s = _non_negative_float(options.pop("retry_jitter_s", 0.25), "retry_jitter_s")
    retry_on_timeout = bool(options.pop("retry_on_timeout", False))
    return (
        RetryPolicy(
            max_retries=max_retries,
            backoff_s=backoff_s,
            backoff_max_s=backoff_max_s,
            jitter_s=jitter_s,
            retry_on_timeout=retry_on_timeout,
        ),
        options,
    )


def _non_negative_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise ProtocolError(f"{key} must be a non-negative integer, not bool")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{key} must be a non-negative integer") from exc
    if parsed < 0:
        raise ProtocolError(f"{key} must be non-negative")
    return parsed


def _non_negative_float(value: Any, key: str) -> float:
    if isinstance(value, bool):
        raise ProtocolError(f"{key} must be a non-negative number, not bool")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{key} must be a non-negative number") from exc
    if parsed < 0:
        raise ProtocolError(f"{key} must be non-negative")
    return parsed


def _retry_success_raw(
    policy: RetryPolicy,
    *,
    attempts: list[dict[str, Any]],
    attempt: int,
    total_backoff_s: float,
) -> dict[str, Any] | None:
    if not policy.enabled:
        return None
    return {
        "retry": {
            "enabled": True,
            "attempts": attempt,
            "retry_count": attempt - 1,
            "max_retries": policy.max_retries,
            "failed_attempts": attempts,
            "total_backoff_s": total_backoff_s,
        }
    }


def _merge_run_raw(provider_raw: Any, retry_raw: dict[str, Any] | None) -> Any:
    if provider_raw is None:
        return retry_raw
    if retry_raw is None:
        return provider_raw
    if isinstance(provider_raw, Mapping):
        merged = dict(provider_raw)
        merged.update(retry_raw)
        return merged
    return {"provider": provider_raw, **retry_raw}


def _format_attempts(attempts: list[dict[str, Any]]) -> str:
    lines = []
    for attempt in attempts:
        lines.append(
            "- attempt {attempt}: {exception_type}, retryable={retryable}, "
            "reason={reason}, events={events_received}, returncode={returncode}, "
            "message={message}".format(**attempt)
        )
    return "\n".join(lines)


__all__ = ["Provider", "RetryDecision", "RetryPolicy"]
