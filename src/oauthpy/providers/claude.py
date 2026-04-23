"""Claude provider — drives the official ``claude-agent-sdk``.

The SDK's ``query(prompt, options=ClaudeAgentOptions(...))`` is an async
iterator of message objects. We map each message to a normalized
:class:`Event`, preserving the raw message on ``Event.raw``. ``run`` buffers
``stream`` into a :class:`RunResult`.

Auth is owned by the SDK / Claude Code CLI: ``ANTHROPIC_API_KEY``,
``CLAUDE_CODE_OAUTH_TOKEN``, or the existing ``~/.claude.json`` login state.
We never touch those files.

A ``claude-agent-sdk`` import failure at module top-level is intentionally
caught: ``Client("claude").available()`` still returns ``False`` cleanly, and
only ``run``/``stream``/``login`` raise :class:`ProviderNotInstalledError`.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from .. import _subprocess
from ..errors import CommandExecutionError, ProviderNotInstalledError, TimeoutExceededError
from ..models import AuthStatus, Event, EventKind
from .base import Provider


def _sdk() -> tuple[Any, Any] | None:
    """Return ``(query, ClaudeAgentOptions)`` from the SDK, or ``None``."""

    try:
        module = importlib.import_module("claude_agent_sdk")
    except ImportError:
        return None
    query = getattr(module, "query", None)
    options_cls = getattr(module, "ClaudeAgentOptions", None)
    if query is None or options_cls is None:
        return None
    return query, options_cls


def _claude_json_path() -> Path:
    """Return the path to the Claude Code login-state file.

    We only check for its *presence* — never parse it — so we never need to
    know the exact schema. ``CLAUDE_CONFIG_HOME`` overrides the home directory
    for tests.
    """

    override = os.environ.get("CLAUDE_CONFIG_HOME")
    base = Path(override) if override else Path.home()
    return base / ".claude.json"


def _classify_sdk_message(msg: Any) -> tuple[EventKind, str | None]:
    """Map a Claude SDK message to ``(EventKind, text)``.

    Conservative: unknown classes become ``MESSAGE``. The raw object is always
    preserved on the emitted :class:`Event`.
    """

    cls_name = type(msg).__name__

    if cls_name == "ResultMessage":
        text = getattr(msg, "result", None) or getattr(msg, "text", None)
        return EventKind.DONE, text if isinstance(text, str) else None

    if cls_name == "SystemMessage":
        subtype = getattr(msg, "subtype", None) or ""
        data = getattr(msg, "data", None)
        text = str(data)[:200] if data is not None else str(subtype) or None
        return EventKind.MESSAGE, text

    if cls_name == "AssistantMessage":
        return EventKind.MESSAGE, _assistant_text(msg)

    if cls_name == "UserMessage":
        return EventKind.MESSAGE, _assistant_text(msg)

    # ToolUseBlock-style messages, if the SDK ever surfaces them directly.
    if "tool" in cls_name.lower():
        name = getattr(msg, "name", None)
        return EventKind.TOOL, str(name) if name else None

    if "error" in cls_name.lower():
        err = getattr(msg, "message", None) or getattr(msg, "error", None)
        return EventKind.ERROR, str(err) if err else None

    # Fall through: check for common attribute shapes.
    if hasattr(msg, "result"):
        return EventKind.DONE, str(getattr(msg, "result", "") or "") or None
    if hasattr(msg, "content"):
        return EventKind.MESSAGE, _assistant_text(msg)
    if hasattr(msg, "text"):
        return EventKind.MESSAGE, str(getattr(msg, "text", "") or "") or None

    return EventKind.MESSAGE, None


def _assistant_text(msg: Any) -> str | None:
    """Extract display text from an AssistantMessage-shaped object."""

    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
            elif isinstance(block, str) and block:
                parts.append(block)
        if parts:
            return "".join(parts)
    text = getattr(msg, "text", None)
    return text if isinstance(text, str) and text else None


def _build_options(
    options_cls: Any,
    *,
    cwd: str | os.PathLike[str] | None,
    model: str | None,
    provider_options: Mapping[str, Any] | None,
) -> Any:
    """Construct a ``ClaudeAgentOptions`` from oauthpy's run() args.

    Callers can pass any SDK-native option through ``provider_options`` —
    unknown keys are forwarded verbatim and rejected by the SDK if invalid.
    """

    kwargs: dict[str, Any] = {}
    if cwd is not None:
        kwargs["cwd"] = os.fspath(cwd)
    if model is not None:
        kwargs["model"] = model
    if provider_options:
        kwargs.update(provider_options)
    try:
        return options_cls(**kwargs)
    except TypeError as exc:
        raise ProviderNotInstalledError(
            f"claude-agent-sdk ClaudeAgentOptions rejected kwargs {list(kwargs)}: {exc}"
        ) from exc


class ClaudeProvider(Provider):
    """Claude adapter via ``claude-agent-sdk.query``."""

    name = "claude"
    transport = "claude-agent-sdk"

    async def auth_status(self) -> AuthStatus:
        sdk = _sdk()
        installed = sdk is not None
        details: dict[str, str] = {}
        details["sdk"] = "present" if installed else "missing"

        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            details["env"] = "CLAUDE_CODE_OAUTH_TOKEN"
            return AuthStatus(
                provider="claude",
                installed=installed,
                authenticated=True,
                mode="env",
                details=details,
            )
        if os.environ.get("ANTHROPIC_API_KEY"):
            details["env"] = "ANTHROPIC_API_KEY"
            return AuthStatus(
                provider="claude",
                installed=installed,
                authenticated=True,
                mode="api-key",
                details=details,
            )

        claude_json = _claude_json_path()
        if claude_json.exists():
            details["login_state"] = str(claude_json)
            return AuthStatus(
                provider="claude",
                installed=installed,
                authenticated=True,
                mode="login-state",
                details=details,
            )

        claude_bin = _subprocess.which("claude")
        if claude_bin:
            details["binary"] = claude_bin
        return AuthStatus(
            provider="claude",
            installed=installed,
            authenticated=False,
            mode="unknown",
            details=details,
        )

    async def login(self) -> None:
        claude_bin = _subprocess.which("claude")
        if claude_bin is None:
            raise ProviderNotInstalledError(
                "claude CLI not found. Install Claude Code from "
                "https://code.claude.com/ and run `claude setup-token`, "
                "or set CLAUDE_CODE_OAUTH_TOKEN in your environment."
            )
        result = await _subprocess.run([claude_bin, "setup-token"], timeout=None)
        if result.returncode != 0:
            raise CommandExecutionError(
                f"claude setup-token exited with code {result.returncode}",
                returncode=result.returncode,
                stderr=result.stderr,
            )

    def _final_text(self, events: list[Event]) -> str:
        # Claude's ResultMessage carries the canonical final text, so prefer it
        # over aggregating MESSAGE events.
        final_done = next(
            (e for e in reversed(events) if e.kind is EventKind.DONE and e.text),
            None,
        )
        if final_done is not None and final_done.text:
            return final_done.text
        return super()._final_text(events)

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
        sdk = _sdk()
        if sdk is None:
            raise ProviderNotInstalledError(
                "claude-agent-sdk is not installed. Run `pip install claude-agent-sdk` "
                "or `pip install oauthpy[claude]`."
            )
        query, options_cls = sdk
        options = _build_options(
            options_cls,
            cwd=cwd,
            model=model,
            provider_options=provider_options,
        )

        # The SDK's query() returns an async iterator in most versions, but
        # some versions return a coroutine that resolves to one. Handle both.
        stream_obj = query(prompt=prompt, options=options)
        if inspect.iscoroutine(stream_obj):
            stream_obj = await stream_obj

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)
        iterator = stream_obj.__aiter__()

        async def _next_msg() -> Any:
            if deadline is None:
                return await iterator.__anext__()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutExceededError(f"claude stream timed out after {timeout}s")
            try:
                return await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutExceededError(f"claude stream timed out after {timeout}s") from exc

        emitted_done = False
        try:
            while True:
                try:
                    msg = await _next_msg()
                except StopAsyncIteration:
                    break
                kind, text = _classify_sdk_message(msg)
                if kind is EventKind.DONE:
                    emitted_done = True
                yield Event(kind=kind, text=text, timestamp=None, raw=msg)
        finally:
            aclose = getattr(stream_obj, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)


__all__ = ["ClaudeProvider"]
