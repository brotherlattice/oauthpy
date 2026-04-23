"""Codex provider — drives the ``codex`` CLI via ``codex exec --json``.

Codex does not have a stable Python SDK in v0.1. The CLI's JSONL stream is the
supported, forward-compatible integration surface. We parse each line into a
normalized :class:`Event`, always preserving the raw dict on ``Event.raw`` so
callers can drop down a level when the classifier is wrong or incomplete.

The JSONL wire format is not formally spec'd. The classifier below handles the
event ``type`` values we have seen in practice (``session.created``,
``agent_message``, ``agent_reasoning``, ``plan_update``, ``tool_call``,
``tool_call_output``, ``file_change``, ``task_complete``, ``error``) and falls
back to a structural classifier (by key presence) for anything unknown.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .. import _subprocess
from ..errors import (
    AuthRequiredError,
    CommandExecutionError,
    ProtocolError,
    ProviderNotInstalledError,
)
from ..models import AuthStatus, Event, EventKind
from .base import Provider


def _codex_binary() -> str:
    """Return the configured codex binary name.

    Looks at the ``OAUTHPY_CODEX_BINARY`` env var first so tests can point at
    a mock script.
    """

    return os.environ.get("OAUTHPY_CODEX_BINARY", "codex")


def classify_event(row: dict[str, Any]) -> tuple[EventKind, str | None]:
    """Map a raw Codex JSONL row to ``(EventKind, text)``.

    Returns ``None`` text when there is no obvious display string for the kind
    (e.g. a ``session.created`` row). Callers should still receive these as
    events so they can inspect ``raw``.
    """

    raw_type = row.get("type") or row.get("event") or ""
    lowered = str(raw_type).lower()

    if "error" in lowered or row.get("error"):
        text = _first_str(row.get("message"), row.get("error"), row.get("text"))
        return EventKind.ERROR, text

    if lowered in {"task_complete", "task.complete", "done", "stop", "turn_complete"}:
        return EventKind.DONE, _first_str(row.get("message"), row.get("text"))

    if lowered in {"agent_message", "message", "assistant_message"} or "message" in lowered:
        return EventKind.MESSAGE, _extract_message_text(row)

    if "reasoning" in lowered or "thought" in lowered:
        return EventKind.REASONING, _first_str(row.get("text"), row.get("content"))

    if "plan" in lowered:
        return EventKind.PLAN, _first_str(row.get("text"), row.get("summary"))

    if "file" in lowered and ("change" in lowered or "write" in lowered or "edit" in lowered):
        path = row.get("path") or row.get("file") or ""
        return EventKind.FILE_CHANGE, str(path) if path else None

    if "command" in lowered or "shell" in lowered or "exec" in lowered:
        cmd = row.get("command") or row.get("cmd") or row.get("argv")
        text = " ".join(cmd) if isinstance(cmd, list) else (str(cmd) if cmd else None)
        return EventKind.COMMAND, text

    if "tool" in lowered:
        name = row.get("name") or row.get("tool") or ""
        return EventKind.TOOL, str(name) if name else None

    if "session" in lowered:
        return EventKind.MESSAGE, None

    text = _first_str(row.get("text"), row.get("content"))
    return EventKind.MESSAGE, text


def _first_str(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v:
            return v
    return None


def _extract_message_text(row: dict[str, Any]) -> str | None:
    """Pull assistant text out of a message row.

    Handles three common shapes:
    * ``{"text": "..."}``
    * ``{"content": "..."}``
    * ``{"content": [{"type": "text", "text": "..."}, ...]}``
    """

    text = _first_str(row.get("text"))
    if text:
        return text
    content = row.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = _first_str(item.get("text"), item.get("content"))
                if t:
                    parts.append(t)
        if parts:
            return "".join(parts)
    return None


def parse_jsonl(lines: list[str]) -> list[Event]:
    """Parse a list of JSONL rows into normalized :class:`Event` objects.

    Malformed rows are surfaced as an ``ERROR`` event whose ``raw`` is the
    original string — we would rather emit noise than silently drop a line.
    """

    events: list[Event] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            events.append(
                Event(
                    kind=EventKind.ERROR,
                    text=f"invalid JSONL line: {line[:120]!r}",
                    raw=line,
                )
            )
            continue
        if not isinstance(row, dict):
            events.append(
                Event(
                    kind=EventKind.ERROR,
                    text=f"non-object JSONL row: {row!r}",
                    raw=row,
                )
            )
            continue
        kind, text = classify_event(row)
        ts = row.get("timestamp") or row.get("ts")
        timestamp = float(ts) if isinstance(ts, int | float) else None
        events.append(Event(kind=kind, text=text, timestamp=timestamp, raw=row))
    return events


class CodexProvider(Provider):
    """Codex adapter.

    ``run`` shells out to ``codex exec --json`` and buffers the JSONL stream;
    ``stream`` yields events as they arrive.
    """

    name = "codex"
    transport = "codex-cli-jsonl"

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or _codex_binary()

    def _build_exec_argv(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None,
        model: str | None,
        provider_options: Mapping[str, Any] | None,
    ) -> list[str]:
        argv: list[str] = [self._binary, "exec", "--json", "--skip-git-repo-check"]
        if model:
            argv.extend(["--model", model])
        if cwd is not None:
            argv.extend(["--cd", os.fspath(cwd)])
        options = dict(provider_options or {})
        sandbox = options.pop("sandbox", None)
        if sandbox:
            argv.extend(["--sandbox", str(sandbox)])
        full_auto = options.pop("full_auto", False)
        if full_auto:
            argv.append("--full-auto")
        approval = options.pop("ask_for_approval", None)
        if approval:
            argv.extend(["--ask-for-approval", str(approval)])
        extra_config = options.pop("config", None)
        if isinstance(extra_config, Mapping):
            for key, value in extra_config.items():
                argv.extend(["--config", f"{key}={value}"])
        extra_argv = options.pop("extra_argv", None)
        if isinstance(extra_argv, list | tuple):
            argv.extend(str(a) for a in extra_argv)
        for key, value in options.items():
            argv.extend(["--config", f"{key}={value}"])
        argv.append(prompt)
        return argv

    async def auth_status(self) -> AuthStatus:
        path = _subprocess.which(self._binary)
        if path is None:
            return AuthStatus(
                provider="codex",
                installed=False,
                authenticated=False,
                mode="unknown",
                details={"binary": self._binary, "found": "false"},
            )
        try:
            result = await _subprocess.run(
                [self._binary, "login", "status"],
                timeout=10.0,
            )
        except CommandExecutionError as exc:
            return AuthStatus(
                provider="codex",
                installed=True,
                authenticated=False,
                mode="unknown",
                details={"binary": path, "error": str(exc)[:200]},
            )
        authenticated = result.returncode == 0
        return AuthStatus(
            provider="codex",
            installed=True,
            authenticated=authenticated,
            mode="oauth" if authenticated else "unknown",
            details={"binary": path, "exit_code": str(result.returncode)},
        )

    async def login(self) -> None:
        if _subprocess.which(self._binary) is None:
            raise ProviderNotInstalledError(
                f"codex CLI not found (looked for {self._binary!r}). Install with "
                f"`npm i -g @openai/codex` or set OAUTHPY_CODEX_BINARY."
            )
        result = await _subprocess.run([self._binary, "login"], timeout=None)
        if result.returncode != 0:
            raise CommandExecutionError(
                f"codex login exited with code {result.returncode}",
                returncode=result.returncode,
                stderr=result.stderr,
            )

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
        if _subprocess.which(self._binary) is None:
            raise ProviderNotInstalledError(
                f"codex CLI not found (looked for {self._binary!r}). Install with "
                f"`npm i -g @openai/codex` or set OAUTHPY_CODEX_BINARY."
            )

        argv = self._build_exec_argv(
            prompt,
            cwd=cwd,
            model=model,
            provider_options=provider_options,
        )

        emitted_done = False
        try:
            async for line in _subprocess.stream_lines(
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
            ):
                for event in parse_jsonl([line]):
                    if event.kind is EventKind.DONE:
                        emitted_done = True
                    yield event
        except CommandExecutionError as exc:
            stderr = (exc.stderr or "").lower()
            if "not logged in" in stderr or "authentication" in stderr or "unauthorized" in stderr:
                raise AuthRequiredError(
                    "codex is not logged in — run `oauthpy auth login --provider codex`."
                ) from exc
            raise ProtocolError(f"codex exec failed: {exc}") from exc

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)


__all__ = ["CodexProvider", "classify_event", "parse_jsonl"]
