"""Gemini provider: drives the official ``gemini`` CLI in headless JSON mode."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from .. import _subprocess
from .._redact import redact
from ..auth import normalize_auth_source, provider_state_dir, resolve_oauthpy_home
from ..errors import (
    AuthRequiredError,
    CommandExecutionError,
    OauthPyError,
    ProtocolError,
    ProviderNotInstalledError,
)
from ..models import AuthSource, AuthStatus, Event, EventKind, Usage
from .base import Provider

_GEMINI_ENV_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_GCA",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


def _gemini_binary() -> str:
    return os.environ.get("OAUTHPY_GEMINI_BINARY", "gemini")


def classify_event(row: dict[str, Any]) -> tuple[EventKind, str | None]:
    """Map a raw Gemini JSON/JSONL row to ``(EventKind, text)``."""

    raw_type = str(row.get("type") or row.get("event") or "").lower()
    if row.get("error") or raw_type == "error":
        return EventKind.ERROR, _error_text(row.get("error")) or _first_str(
            row.get("message"), row.get("text")
        )
    if raw_type == "result" or ("response" in row and not raw_type):
        return EventKind.DONE, _first_str(row.get("response"), row.get("result"), row.get("text"))
    if raw_type == "message" or "message" in raw_type:
        return EventKind.MESSAGE, _message_text(row)
    if raw_type in {"tool_use", "tool_result", "tool_call"} or "tool" in raw_type:
        return EventKind.TOOL, _tool_text(row)
    if raw_type in {"init", "session"}:
        return EventKind.MESSAGE, None
    return EventKind.MESSAGE, _first_str(row.get("text"), row.get("content"))


def parse_jsonl(lines: list[str]) -> list[Event]:
    """Parse Gemini JSONL rows into normalized events."""

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
        events.append(Event(kind=kind, text=text, raw=row))
    return events


class GeminiProvider(Provider):
    """Gemini adapter using ``gemini --prompt ... --output-format stream-json``."""

    name = "gemini"
    transport = "gemini-cli-jsonl"

    def __init__(
        self,
        binary: str | None = None,
        *,
        auth_source: AuthSource = "auto",
        oauthpy_home: str | os.PathLike[str] | None = None,
    ) -> None:
        self._binary = binary or _gemini_binary()
        self._auth_source = normalize_auth_source(auth_source)
        self._oauthpy_home = resolve_oauthpy_home(oauthpy_home)
        self._gemini_home = provider_state_dir("gemini", self._oauthpy_home)

    async def auth_status(self) -> AuthStatus:
        path = _subprocess.which(self._binary)
        if path is None:
            return AuthStatus(
                provider="gemini",
                installed=False,
                authenticated=False,
                mode="unknown",
                details={
                    "binary": self._binary,
                    "found": False,
                    "requested_source": self._auth_source,
                    "source": "none",
                    "provider_home": str(self._gemini_home),
                    "reason": "binary_missing",
                },
            )

        if self._auth_source == "oauthpy":
            return self._unsupported_oauthpy_status(path, requested_source="oauthpy")

        env_status = self._env_auth_status(path, requested_source=self._auth_source)
        if env_status.authenticated:
            return env_status

        selected_auth = _selected_auth_type()
        if selected_auth is not None:
            return AuthStatus(
                provider="gemini",
                installed=True,
                authenticated=True,
                mode="login-state",
                details={
                    "binary": path,
                    "source": "external",
                    "requested_source": self._auth_source,
                    "provider_home": None,
                    "gemini_config_dir": str(_gemini_config_dir()),
                    "auth_method": selected_auth,
                    "auth_verified": False,
                    "reason": "cached_login_plausible",
                },
            )

        return AuthStatus(
            provider="gemini",
            installed=True,
            authenticated=False,
            mode="unknown",
            details={
                "binary": path,
                "source": "external" if self._auth_source == "external" else "none",
                "requested_source": self._auth_source,
                "provider_home": None,
                "gemini_config_dir": str(_gemini_config_dir()),
                "auth_verified": False,
                "reason": "cached_login_unverified",
            },
        )

    async def login(self) -> None:
        gemini_bin = _subprocess.which(self._binary)
        if gemini_bin is None:
            raise ProviderNotInstalledError(
                "gemini CLI not found. Install it with `npm install -g @google/gemini-cli` "
                "or set OAUTHPY_GEMINI_BINARY."
            )
        if self._auth_source == "oauthpy":
            raise OauthPyError(
                "Gemini oauthpy-isolated login is not supported because Gemini CLI does not "
                "document a safe config/auth-root override. Use --source external."
            )
        result = await _subprocess.run_interactive([gemini_bin], env=None, timeout=None)
        if result.returncode != 0:
            raise CommandExecutionError(
                f"gemini login exited with code {result.returncode}",
                returncode=result.returncode,
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
                "gemini CLI not found. Install it with `npm install -g @google/gemini-cli` "
                "or set OAUTHPY_GEMINI_BINARY."
            )
        if self._auth_source == "oauthpy":
            raise AuthRequiredError(
                "Gemini oauthpy-isolated auth is not supported. Use auth_source='external' "
                "or auth_source='auto' to reuse official Gemini CLI auth."
            )

        argv = self._build_argv(prompt, cwd=cwd, model=model, provider_options=provider_options)
        emitted_done = False
        try:
            async for line in _subprocess.stream_lines(
                argv,
                cwd=cwd,
                env=dict(env) if env else None,
                timeout=timeout,
            ):
                for event in parse_jsonl([line]):
                    if event.kind is EventKind.DONE:
                        emitted_done = True
                    yield event
        except CommandExecutionError as exc:
            stderr = (exc.stderr or "").lower()
            if any(
                marker in stderr
                for marker in ("authentication", "unauthorized", "credentials", "api key")
            ):
                raise AuthRequiredError(
                    "gemini is not authenticated; run `oauthpy auth login --provider gemini` "
                    "or configure Gemini CLI env auth."
                ) from exc
            raise ProtocolError(_gemini_exec_error_message(exc)) from exc

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)

    def _build_argv(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None,
        model: str | None,
        provider_options: Mapping[str, Any] | None,
    ) -> list[str]:
        options = dict(provider_options or {})
        reasoning_effort = options.pop("reasoning_effort", None)
        if reasoning_effort is not None:
            raise ProtocolError(
                "Gemini CLI does not expose a documented reasoning-effort flag through oauthpy."
            )
        output_format = str(options.pop("output_format", "stream-json"))
        if output_format not in {"stream-json", "json"}:
            raise ProtocolError("Gemini output_format must be 'stream-json' or 'json'.")

        argv = [self._binary, "--prompt", prompt, "--output-format", output_format]
        if model:
            argv.extend(["--model", model])
        if cwd is not None and options.pop("include_cwd", False):
            argv.extend(["--include-directories", os.fspath(cwd)])
        if options.pop("all_files", False):
            argv.append("--all-files")
        if options.pop("yolo", False):
            argv.append("--yolo")
        sandbox = options.pop("sandbox", None)
        if sandbox is not None:
            argv.append("--sandbox" if sandbox is True else f"--sandbox={sandbox}")
        approval_mode = options.pop("approval_mode", None)
        if approval_mode:
            argv.extend(["--approval-mode", str(approval_mode)])
        include_dirs = options.pop("include_directories", None)
        if include_dirs:
            if isinstance(include_dirs, str):
                value = include_dirs
            else:
                value = ",".join(os.fspath(path) for path in include_dirs)
            argv.extend(["--include-directories", value])
        extra_argv = options.pop("extra_argv", None)
        if isinstance(extra_argv, list | tuple):
            argv.extend(str(arg) for arg in extra_argv)
        if options:
            unsupported = ", ".join(sorted(options))
            raise ProtocolError(
                "unsupported Gemini provider_options keys: "
                f"{unsupported}; use extra_argv for additional CLI flags"
            )
        return argv

    def _final_text(self, events: list[Event]) -> str:
        final_done = next(
            (event for event in reversed(events) if event.kind is EventKind.DONE and event.text),
            None,
        )
        if final_done is not None and final_done.text:
            return final_done.text
        return super()._final_text(events)

    def _usage(self, events: list[Event]) -> Usage | None:
        for event in reversed(events):
            if event.kind is not EventKind.DONE or not isinstance(event.raw, Mapping):
                continue
            usage = event.raw.get("usage")
            if isinstance(usage, Mapping):
                return _usage_from_mapping(usage)
            stats = event.raw.get("stats")
            if isinstance(stats, Mapping):
                return _usage_from_stats(stats)
        return None

    def _unsupported_oauthpy_status(
        self, binary_path: str, *, requested_source: AuthSource
    ) -> AuthStatus:
        return AuthStatus(
            provider="gemini",
            installed=True,
            authenticated=False,
            mode="unknown",
            details={
                "binary": binary_path,
                "source": "oauthpy",
                "requested_source": requested_source,
                "provider_home": str(self._gemini_home),
                "reason": "isolated_auth_unsupported",
            },
        )

    def _env_auth_status(self, binary_path: str, *, requested_source: AuthSource) -> AuthStatus:
        mode = "unknown"
        env_name: str | None = None
        if os.environ.get("GEMINI_API_KEY"):
            mode = "api-key"
            env_name = "GEMINI_API_KEY"
        elif os.environ.get("GOOGLE_API_KEY"):
            mode = "api-key"
            env_name = "GOOGLE_API_KEY"
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            mode = "cloud"
            env_name = "GOOGLE_APPLICATION_CREDENTIALS"
        elif os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"):
            mode = "cloud"
            env_name = "GOOGLE_GENAI_USE_VERTEXAI"
        elif os.environ.get("GOOGLE_GENAI_USE_GCA"):
            mode = "cloud"
            env_name = "GOOGLE_GENAI_USE_GCA"

        return AuthStatus(
            provider="gemini",
            installed=True,
            authenticated=env_name is not None,
            mode=mode,
            details={
                "binary": binary_path,
                "source": "external" if env_name else "none",
                "requested_source": requested_source,
                "provider_home": None,
                "env_auth": env_name,
                "cloud_env_present": any(os.environ.get(key) for key in _GEMINI_ENV_KEYS),
                "reason": "authenticated" if env_name else "not_authenticated",
            },
        )


def _selected_auth_type() -> str | None:
    settings = _gemini_config_dir() / "settings.json"
    try:
        payload = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    security = payload.get("security")
    if isinstance(security, dict):
        auth = security.get("auth")
        if isinstance(auth, dict):
            selected = auth.get("selectedType")
            if isinstance(selected, str) and selected:
                return selected
    selected = payload.get("selectedAuthType")
    if isinstance(selected, str) and selected:
        return selected
    return None


def _gemini_config_dir() -> Path:
    return Path.home() / ".gemini"


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _message_text(row: Mapping[str, Any]) -> str | None:
    for key in ("text", "content", "delta"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    message = row.get("message")
    if isinstance(message, str) and message:
        return message
    if isinstance(message, Mapping):
        return _first_str(message.get("text"), message.get("content"), message.get("delta"))
    return None


def _tool_text(row: Mapping[str, Any]) -> str | None:
    for key in ("name", "tool", "tool_name", "toolName", "id", "tool_use_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    tool = row.get("tool")
    if isinstance(tool, Mapping):
        return _first_str(tool.get("name"), tool.get("id"))
    return None


def _error_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        return _first_str(value.get("message"), value.get("type"), value.get("code"))
    return None


def _usage_from_stats(stats: Mapping[str, Any]) -> Usage | None:
    models = stats.get("models")
    if isinstance(models, Mapping):
        input_tokens = output_tokens = total_tokens = 0
        found = False
        for model_stats in models.values():
            if not isinstance(model_stats, Mapping):
                continue
            tokens = model_stats.get("tokens")
            if not isinstance(tokens, Mapping):
                continue
            found = True
            input_tokens += _int_value(tokens.get("prompt")) or 0
            output_tokens += _int_value(tokens.get("candidates")) or 0
            total_tokens += _int_value(tokens.get("total")) or 0
        if found:
            return Usage(
                input_tokens=input_tokens or None,
                output_tokens=output_tokens or None,
                total_tokens=total_tokens or None,
            )
    tokens = stats.get("tokens")
    if isinstance(tokens, Mapping):
        return _usage_from_mapping(tokens)
    return None


def _usage_from_mapping(raw: Mapping[str, Any]) -> Usage:
    input_tokens = _int_value(
        raw.get("input_tokens") or raw.get("inputTokens") or raw.get("prompt")
    )
    output_tokens = _int_value(
        raw.get("output_tokens") or raw.get("outputTokens") or raw.get("candidates")
    )
    total_tokens = _int_value(raw.get("total_tokens") or raw.get("totalTokens") or raw.get("total"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _gemini_exec_error_message(exc: CommandExecutionError) -> str:
    message = f"gemini CLI failed: {exc}"
    stderr = (exc.stderr or "").strip()
    if not stderr:
        return message
    return f"{message}\nstderr:\n{redact(stderr[-2000:])}"


__all__ = ["GeminiProvider", "classify_event", "parse_jsonl"]
