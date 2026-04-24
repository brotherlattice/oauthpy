"""Codex provider: drives the official ``codex`` CLI via ``exec --json``."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .. import _subprocess
from ..auth import (
    ensure_private_dir,
    normalize_auth_source,
    provider_state_dir,
    resolve_oauthpy_home,
)
from ..errors import (
    AuthRequiredError,
    CommandExecutionError,
    ProtocolError,
    ProviderNotInstalledError,
)
from ..models import AuthSource, AuthStatus, Event, EventKind, Usage
from .base import Provider

_CODEX_AUTH_ENV_KEYS = ("OPENAI_API_KEY",)
_SUPPORTED_CREDENTIAL_STORES = {"file", "keyring", "auto"}
_CREDENTIAL_STORE_RE = re.compile(
    r"^\s*cli_auth_credentials_store\s*=\s*(?P<value>.+?)\s*(?:#.*)?$"
)


def _codex_binary() -> str:
    """Return the configured codex binary name."""

    return os.environ.get("OAUTHPY_CODEX_BINARY", "codex")


def classify_event(row: dict[str, Any]) -> tuple[EventKind, str | None]:
    """Map a raw Codex JSONL row to ``(EventKind, text)``."""

    raw_type = str(row.get("type") or row.get("event") or "").lower()
    item = row.get("item") if isinstance(row.get("item"), dict) else None
    item_type = str(item.get("type") if item else "").lower()

    if raw_type in {"turn.failed", "error"} or "error" in raw_type or row.get("error"):
        text = _first_str(row.get("message"), row.get("error"), row.get("text"))
        if text is None and isinstance(row.get("error"), dict):
            text = _first_str(row["error"].get("message"), row["error"].get("type"))
        return EventKind.ERROR, text

    if raw_type in {"turn.completed", "task_complete", "task.complete", "done", "stop"}:
        return EventKind.DONE, _first_str(row.get("message"), row.get("text"))

    if item is not None and raw_type.startswith("item."):
        return _classify_item(item_type, item)

    # Backward-compatible classifier for older fixtures and CLI versions.
    if raw_type in {"agent_message", "message", "assistant_message"} or "message" in raw_type:
        return EventKind.MESSAGE, _extract_message_text(row)
    if "reasoning" in raw_type or "thought" in raw_type:
        return EventKind.REASONING, _first_str(row.get("text"), row.get("content"))
    if "plan" in raw_type:
        return EventKind.PLAN, _first_str(row.get("text"), row.get("summary"))
    if "file" in raw_type and ("change" in raw_type or "write" in raw_type or "edit" in raw_type):
        path = row.get("path") or row.get("file") or ""
        return EventKind.FILE_CHANGE, str(path) if path else None
    if "command" in raw_type or "shell" in raw_type or "exec" in raw_type:
        return EventKind.COMMAND, _command_text(row)
    if "tool" in raw_type or "web_search" in raw_type:
        name = row.get("name") or row.get("tool") or row.get("tool_name") or ""
        return EventKind.TOOL, str(name) if name else None
    if "session" in raw_type or raw_type in {"thread.started", "turn.started", "item.started"}:
        return EventKind.MESSAGE, None

    text = _first_str(row.get("text"), row.get("content"))
    return EventKind.MESSAGE, text


def _classify_item(item_type: str, item: dict[str, Any]) -> tuple[EventKind, str | None]:
    if item.get("error") or "error" in item_type:
        return EventKind.ERROR, _first_str(item.get("message"), item.get("error"), item.get("text"))
    if item_type == "agent_message":
        return EventKind.MESSAGE, _extract_message_text(item)
    if item_type == "reasoning":
        return EventKind.REASONING, _first_str(item.get("text"), item.get("summary"))
    if item_type in {"plan_update", "update_plan"}:
        return EventKind.PLAN, _first_str(item.get("text"), item.get("summary"))
    if item_type in {"command_execution", "exec_command"}:
        return EventKind.COMMAND, _command_text(item)
    if item_type in {"file_change", "file_diff", "patch"}:
        return EventKind.FILE_CHANGE, _file_change_text(item)
    if item_type in {"mcp_tool_call", "tool_call", "web_search"}:
        return EventKind.TOOL, _tool_text(item)
    return EventKind.MESSAGE, _first_str(item.get("text"), item.get("content"))


def _first_str(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v:
            return v
    return None


def _extract_message_text(row: dict[str, Any]) -> str | None:
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


def _command_text(row: dict[str, Any]) -> str | None:
    cmd = row.get("command") or row.get("cmd") or row.get("argv")
    if isinstance(cmd, list):
        return " ".join(str(part) for part in cmd)
    return str(cmd) if cmd else None


def _file_change_text(row: dict[str, Any]) -> str | None:
    value = row.get("path") or row.get("file") or row.get("filename") or row.get("summary")
    return str(value) if value else None


def _tool_text(row: dict[str, Any]) -> str | None:
    value = row.get("name") or row.get("tool") or row.get("tool_name") or row.get("server")
    return str(value) if value else None


def parse_jsonl(lines: list[str]) -> list[Event]:
    """Parse JSONL rows into normalized :class:`Event` objects."""

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
    """Codex adapter using ``codex exec --json``."""

    name = "codex"
    transport = "codex-cli-jsonl"

    def __init__(
        self,
        binary: str | None = None,
        *,
        auth_source: AuthSource = "auto",
        oauthpy_home: str | os.PathLike[str] | None = None,
    ) -> None:
        self._binary = binary or _codex_binary()
        self._auth_source = normalize_auth_source(auth_source)
        self._oauthpy_home = resolve_oauthpy_home(oauthpy_home)
        self._codex_home = provider_state_dir("codex", self._oauthpy_home)

    def _build_exec_argv(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None,
        model: str | None,
        provider_options: Mapping[str, Any] | None,
    ) -> list[str]:
        argv: list[str] = [self._binary, "exec", "--json"]
        options = dict(provider_options or {})
        if options.pop("skip_git_repo_check", False):
            argv.append("--skip-git-repo-check")
        if model:
            argv.extend(["--model", model])
        if cwd is not None:
            argv.extend(["--cd", os.fspath(cwd)])
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
                details={
                    "binary": self._binary,
                    "found": False,
                    "requested_source": self._auth_source,
                    "source": "none",
                    "provider_home": str(self._codex_home),
                },
            )

        if self._auth_source == "auto":
            oauthpy_status: AuthStatus | None = None
            if self._oauthpy_state_plausible():
                oauthpy_status = await self._status_for_source(
                    "oauthpy", path, requested_source="auto", ensure_state=False
                )
                if oauthpy_status.authenticated:
                    return oauthpy_status
            external_status = await self._status_for_source(
                "external", path, requested_source="auto", ensure_state=False
            )
            if external_status.authenticated:
                return external_status
            details = dict(external_status.details)
            details.update(
                {
                    "source": "none",
                    "requested_source": "auto",
                    "provider_home": str(self._codex_home),
                    "oauthpy_checked": oauthpy_status is not None,
                    "external_exit_code": external_status.details.get("exit_code"),
                }
            )
            if oauthpy_status is not None:
                details["oauthpy_exit_code"] = oauthpy_status.details.get("exit_code")
            return AuthStatus(
                provider="codex",
                installed=True,
                authenticated=False,
                mode="unknown",
                details=details,
            )

        return await self._status_for_source(
            self._auth_source,
            path,
            requested_source=self._auth_source,
            ensure_state=self._auth_source == "oauthpy",
        )

    async def _status_for_source(
        self,
        source: AuthSource,
        binary_path: str,
        *,
        requested_source: AuthSource,
        ensure_state: bool,
    ) -> AuthStatus:
        if source == "oauthpy" and ensure_state:
            self._ensure_oauthpy_state()
        env = self._source_env(source)
        try:
            result = await _subprocess.run(
                [self._binary, "login", "status"],
                env=env,
                timeout=10.0,
            )
        except CommandExecutionError as exc:
            return AuthStatus(
                provider="codex",
                installed=True,
                authenticated=False,
                mode="unknown",
                details={
                    "binary": binary_path,
                    "source": source,
                    "requested_source": requested_source,
                    "provider_home": str(self._codex_home) if source == "oauthpy" else None,
                    "error": str(exc)[:200],
                },
            )

        mode, auth_method = self._classify_status(result.stdout, result.stderr, result.returncode)
        return AuthStatus(
            provider="codex",
            installed=True,
            authenticated=result.returncode == 0,
            mode=mode,
            details={
                "binary": binary_path,
                "source": source,
                "requested_source": requested_source,
                "provider_home": str(self._codex_home) if source == "oauthpy" else None,
                "exit_code": result.returncode,
                "auth_method": auth_method,
            },
        )

    def _classify_status(self, stdout: str, stderr: str, returncode: int) -> tuple[str, str]:
        if returncode != 0:
            return "unknown", "none"
        text = f"{stdout}\n{stderr}".lower()
        if "api key" in text or "apikey" in text:
            return "api-key", "api-key"
        if "chatgpt" in text or "oauth" in text:
            return "oauth", "chatgpt"
        if "logged in" in text:
            return "unknown", "unknown"
        return "unknown", "unknown"

    async def login(self) -> None:
        if _subprocess.which(self._binary) is None:
            raise ProviderNotInstalledError(
                f"codex CLI not found (looked for {self._binary!r}). Install with "
                f"`npm i -g @openai/codex` or set OAUTHPY_CODEX_BINARY."
            )
        source: AuthSource = "oauthpy" if self._auth_source == "auto" else self._auth_source
        if source == "oauthpy":
            self._ensure_oauthpy_state()
        result = await _subprocess.run_interactive(
            [self._binary, "login"],
            env=self._source_env(source),
            timeout=None,
        )
        if result.returncode != 0:
            raise CommandExecutionError(
                f"codex login exited with code {result.returncode}",
                returncode=result.returncode,
            )

    async def _resolve_run_source(self) -> AuthSource:
        if self._auth_source in {"oauthpy", "external"}:
            if self._auth_source == "oauthpy":
                self._ensure_oauthpy_state()
            return self._auth_source

        path = _subprocess.which(self._binary)
        if path is None:
            return "oauthpy"
        if self._oauthpy_state_plausible():
            oauthpy_status = await self._status_for_source(
                "oauthpy", path, requested_source="auto", ensure_state=False
            )
            if oauthpy_status.authenticated:
                return "oauthpy"
        external_status = await self._status_for_source(
            "external", path, requested_source="auto", ensure_state=False
        )
        if external_status.authenticated:
            return "external"
        self._ensure_oauthpy_state()
        return "oauthpy"

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

        source = await self._resolve_run_source()
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
                env=self._source_env(source, env),
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
                    "codex is not logged in; run `oauthpy auth login --provider codex`."
                ) from exc
            raise ProtocolError(f"codex exec failed: {exc}") from exc

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)

    def _usage(self, events: list[Event]) -> Usage | None:
        for event in reversed(events):
            if event.kind is not EventKind.DONE or not isinstance(event.raw, dict):
                continue
            usage = event.raw.get("usage")
            if isinstance(usage, dict):
                return _usage_from_mapping(usage)
        return None

    def _source_env(
        self,
        source: AuthSource,
        user_env: Mapping[str, str] | None = None,
    ) -> dict[str, str | None] | None:
        env: dict[str, str | None] = {}
        if source == "oauthpy":
            for key in _CODEX_AUTH_ENV_KEYS:
                env[key] = None
            if user_env:
                env.update(user_env)
            env["CODEX_HOME"] = str(self._codex_home)
        elif user_env:
            env.update(user_env)
        return env or None

    def _ensure_oauthpy_state(self) -> None:
        ensure_private_dir(self._oauthpy_home)
        ensure_private_dir(self._codex_home)
        self._ensure_codex_config()

    def _ensure_codex_config(self) -> None:
        config = self._codex_home / "config.toml"
        if not config.exists():
            config.write_text('cli_auth_credentials_store = "file"\n', encoding="utf-8")
            return

        text = config.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        in_table = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_table = True
            if in_table:
                continue
            match = _CREDENTIAL_STORE_RE.match(line)
            if match is None:
                continue
            value = _tomlish_string_value(match.group("value"))
            if value not in _SUPPORTED_CREDENTIAL_STORES:
                raise ProtocolError(
                    "unsupported CODEX_HOME/config.toml cli_auth_credentials_store "
                    f"value {value!r}; expected file, keyring, or auto"
                )
            return

        prefix = 'cli_auth_credentials_store = "file"\n'
        separator = "\n" if text and not text.startswith("\n") else ""
        config.write_text(prefix + separator + text, encoding="utf-8")

    def _oauthpy_state_plausible(self) -> bool:
        if not self._codex_home.exists():
            return False
        return any(
            (self._codex_home / name).exists()
            for name in ("auth.json", "config.toml", "credentials.json")
        )


def _tomlish_string_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _usage_from_mapping(raw: Mapping[str, Any]) -> Usage:
    input_tokens = _int_value(raw.get("input_tokens"))
    output_tokens = _int_value(raw.get("output_tokens"))
    total_tokens = _int_value(raw.get("total_tokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=_float_value(raw.get("cost_usd") or raw.get("total_cost_usd")),
    )


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_value(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


__all__ = ["CodexProvider", "classify_event", "parse_jsonl"]
