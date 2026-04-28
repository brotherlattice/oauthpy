"""Claude provider: drives the official ``claude-agent-sdk``."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import re
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

from .. import _subprocess
from .._redact import redact
from ..auth import (
    ensure_private_dir,
    normalize_auth_source,
    provider_state_dir,
    resolve_oauthpy_home,
)
from ..defaults import DEFAULT_CLAUDE_MODEL, DEFAULT_CLAUDE_REASONING_EFFORT
from ..errors import (
    CommandExecutionError,
    ProtocolError,
    ProviderNotInstalledError,
    TimeoutExceededError,
)
from ..models import AuthSource, AuthStatus, Event, EventKind, TransportName, Usage
from .base import Provider, RetryDecision, RetryPolicy

_CLAUDE_AUTH_ENV_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
_CLAUDE_CLOUD_ENV_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
_CLAUDE_SDK_QUERY_LOGGER = "claude_agent_sdk._internal.query"
_DIAGNOSTIC_TAIL_CHARS = 4000
_DIAGNOSTIC_TAIL_LINES = 80
_EXIT_CODE_RE = re.compile(
    r"(?:exit(?:ed)?(?:\s+with)?\s+code|exit\s*code)[:\s]+(-?\d+)",
    re.IGNORECASE,
)
_CLAUDE_READER_FAILURE_MARKERS = (
    "fatal error in message reader",
    "message reader",
    "command failed with exit code 1",
    "check stderr output for details",
)
_CLAUDE_NON_RETRYABLE_MARKERS = (
    "not logged in",
    "authentication",
    "unauthorized",
    "permission denied",
    "permission_mode",
    "invalid model",
    "unknown model",
    "claudeagentoptions rejected",
    "unsupported",
    "usage policy",
    "refusal",
)
_CLAUDE_POLICY_REFUSAL_MARKERS = (
    "usage policy",
    "unable to respond",
    "stop_reason",
    "refusal",
)


class _TailBuffer:
    def __init__(self) -> None:
        self._lines: deque[str] = deque(maxlen=_DIAGNOSTIC_TAIL_LINES)

    def append(self, value: Any) -> None:
        line = redact(value).strip()
        if not line:
            return
        self._lines.append(line[-_DIAGNOSTIC_TAIL_CHARS:])

    def text(self) -> str:
        return "\n".join(self._lines)[-_DIAGNOSTIC_TAIL_CHARS:]


class _ClaudeDiagnostics(logging.Handler):
    """Capture noisy Claude SDK internals without letting them propagate."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._sdk_logs = _TailBuffer()
        self._stderr = _TailBuffer()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.add_sdk_log(f"{record.levelname}: {record.getMessage()}")
        except Exception:  # pragma: no cover - logging must never break a run
            pass

    def add_sdk_log(self, line: Any) -> None:
        self._sdk_logs.append(line)

    def add_stderr(self, line: Any) -> None:
        self._stderr.append(line)

    def stderr_callback(self, user_callback: Any) -> Callable[[str], None]:
        def _callback(line: str) -> None:
            self.add_stderr(line)
            if user_callback is None:
                return
            try:
                user_callback(line)
            except Exception as exc:  # pragma: no cover - defensive callback isolation
                self.add_sdk_log(f"stderr callback failed: {exc}")

        return _callback

    @contextmanager
    def capture_sdk_logger(self) -> Iterator[None]:
        logger = logging.getLogger(_CLAUDE_SDK_QUERY_LOGGER)
        previous_propagate = logger.propagate
        previous_level = logger.level
        previous_disabled = logger.disabled
        logger.addHandler(self)
        logger.propagate = False
        logger.disabled = False
        if logger.level > logging.ERROR:
            logger.setLevel(logging.ERROR)
        try:
            yield
        finally:
            with suppress(ValueError):
                logger.removeHandler(self)
            logger.propagate = previous_propagate
            logger.setLevel(previous_level)
            logger.disabled = previous_disabled

    def sdk_log_tail(self) -> str:
        return self._sdk_logs.text()

    def stderr_tail(self) -> str:
        return self._stderr.text()


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


def _claude_binary() -> str:
    return os.environ.get("OAUTHPY_CLAUDE_BINARY", "claude")


def _events_from_sdk_message(msg: Any) -> list[Event]:
    """Normalize one SDK message into one or more oauthpy events."""

    cls_name = type(msg).__name__
    if cls_name == "ResultMessage" or hasattr(msg, "result"):
        events: list[Event] = []
        errors = getattr(msg, "errors", None)
        if getattr(msg, "is_error", False) or errors:
            text = _first_str(getattr(msg, "result", None), _join_strs(errors))
            events.append(Event(kind=EventKind.ERROR, text=text, raw=msg))
        result = getattr(msg, "result", None)
        structured_text = _structured_output_text(_structured_output_from_raw(msg))
        events.append(
            Event(
                kind=EventKind.DONE,
                text=structured_text or (result if isinstance(result, str) else None),
                raw=msg,
            )
        )
        return events

    if cls_name == "SystemMessage":
        subtype = getattr(msg, "subtype", None) or ""
        data = getattr(msg, "data", None)
        text = str(data)[:200] if data is not None else str(subtype) or None
        return [Event(kind=EventKind.MESSAGE, text=text, raw=msg)]

    if cls_name in {"AssistantMessage", "UserMessage"} or hasattr(msg, "content"):
        error = getattr(msg, "error", None)
        events = []
        if error:
            events.append(Event(kind=EventKind.ERROR, text=str(error), raw=msg))
        events.extend(_events_from_content(getattr(msg, "content", None), msg))
        return events or [Event(kind=EventKind.MESSAGE, text=None, raw=msg)]

    if cls_name == "RateLimitEvent":
        info = getattr(msg, "rate_limit_info", None)
        status = getattr(info, "status", None)
        text = f"rate_limit={status}" if status else None
        return [Event(kind=EventKind.MESSAGE, text=text, raw=msg)]

    if cls_name == "StreamEvent":
        event = getattr(msg, "event", None)
        text = str(event.get("type")) if isinstance(event, dict) and event.get("type") else None
        return [Event(kind=EventKind.MESSAGE, text=text, raw=msg)]

    if "tool" in cls_name.lower():
        name = getattr(msg, "name", None) or getattr(msg, "tool_use_id", None)
        return [Event(kind=EventKind.TOOL, text=str(name) if name else None, raw=msg)]
    if "error" in cls_name.lower():
        err = getattr(msg, "message", None) or getattr(msg, "error", None)
        return [Event(kind=EventKind.ERROR, text=str(err) if err else None, raw=msg)]
    if hasattr(msg, "text"):
        text = str(msg.text or "") or None
        return [Event(kind=EventKind.MESSAGE, text=text, raw=msg)]
    return [Event(kind=EventKind.MESSAGE, text=None, raw=msg)]


def _classify_sdk_message(msg: Any) -> tuple[EventKind, str | None]:
    """Backward-compatible classifier used by tests and advanced callers."""

    event = _events_from_sdk_message(msg)[0]
    return event.kind, event.text


def _events_from_content(content: Any, raw: Any) -> list[Event]:
    if isinstance(content, str):
        return [Event(kind=EventKind.MESSAGE, text=content or None, raw=raw)]
    if not isinstance(content, list):
        return []
    events: list[Event] = []
    text_parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        cls_name = type(block).__name__
        if cls_name == "TextBlock":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        elif cls_name == "ThinkingBlock":
            thinking = getattr(block, "thinking", None)
            events.append(
                Event(
                    kind=EventKind.REASONING,
                    text=thinking if isinstance(thinking, str) else None,
                    raw=block,
                )
            )
        elif cls_name in {"ToolUseBlock", "ServerToolUseBlock"}:
            name = getattr(block, "name", None)
            events.append(Event(kind=EventKind.TOOL, text=str(name) if name else None, raw=block))
        elif cls_name in {"ToolResultBlock", "ServerToolResultBlock"}:
            tool_id = getattr(block, "tool_use_id", None)
            is_error = getattr(block, "is_error", False)
            kind = EventKind.ERROR if is_error else EventKind.TOOL
            events.append(Event(kind=kind, text=str(tool_id) if tool_id else None, raw=block))
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
    if text_parts:
        events.insert(0, Event(kind=EventKind.MESSAGE, text="".join(text_parts), raw=raw))
    return events


def _build_options(
    options_cls: Any,
    *,
    cwd: str | os.PathLike[str] | None,
    model: str | None,
    provider_options: Mapping[str, Any] | None,
    env: Mapping[str, str] | None,
    diagnostics: _ClaudeDiagnostics | None = None,
) -> Any:
    """Construct ``ClaudeAgentOptions`` from oauthpy's run args."""

    kwargs: dict[str, Any] = {}
    options = dict(provider_options or {})
    user_stderr = options.pop("stderr", None)
    if cwd is not None:
        kwargs["cwd"] = os.fspath(cwd)
    if model is not None:
        kwargs["model"] = model
    elif "model" not in options:
        kwargs["model"] = DEFAULT_CLAUDE_MODEL
    reasoning_effort = options.pop("reasoning_effort", DEFAULT_CLAUDE_REASONING_EFFORT)
    if reasoning_effort is not None:
        options.setdefault("effort", reasoning_effort)
    if options:
        kwargs.update(options)
    if env:
        merged_env = dict(kwargs.get("env") or {})
        merged_env.update(env)
        kwargs["env"] = merged_env
    if diagnostics is not None:
        kwargs["stderr"] = diagnostics.stderr_callback(user_stderr)
    elif user_stderr is not None:
        kwargs["stderr"] = user_stderr
    try:
        return options_cls(**kwargs)
    except TypeError as exc:
        raise ProviderNotInstalledError(
            f"claude-agent-sdk ClaudeAgentOptions rejected kwargs {list(kwargs)}: {exc}"
        ) from exc


def _json_schema_output_format(
    provider_options: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    options = dict(provider_options or {})
    output_format = options.get("output_format")
    if not isinstance(output_format, Mapping):
        return None
    if output_format.get("type") != "json_schema":
        return None
    return output_format


def _normalize_schema_provider_options(
    provider_options: Mapping[str, Any] | None,
) -> dict[str, Any]:
    options = dict(provider_options or {})
    output_format = _json_schema_output_format(options)
    if output_format is None:
        return options
    schema = output_format.get("schema")
    if not isinstance(schema, Mapping):
        raise ProtocolError("Claude output_format.schema must be a JSON schema mapping.")
    if "max_turns" in options and options["max_turns"] is not None:
        options["max_turns"] = _claude_schema_max_turns(options["max_turns"])
    return options


def _claude_cli_list_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return ",".join(str(item) for item in value)
    return str(value)


def _claude_schema_max_turns(value: Any) -> int:
    try:
        max_turns = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            "Claude structured-output provider_options.max_turns must be an integer."
        ) from exc
    if max_turns < 1:
        raise ProtocolError(
            "Claude structured-output provider_options.max_turns must be >= 1."
        )
    # Claude structured-output mode performs an internal finalization step.
    # With max_turns=1, Claude can stop before emitting structured_output even
    # when the model has already produced the answer.
    return max(2, max_turns)


def _structured_output_from_raw(raw: Any) -> Any:
    if isinstance(raw, Mapping):
        return raw.get("structured_output")
    return getattr(raw, "structured_output", None)


def _structured_output_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


def _is_policy_refusal_payload(payload: Mapping[str, Any], text: str | None) -> bool:
    if str(payload.get("stop_reason") or "").lower() == "refusal":
        return True
    return _has_marker(text or "", _CLAUDE_POLICY_REFUSAL_MARKERS)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _sdk_result_payload(raw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attr in (
        "type",
        "subtype",
        "is_error",
        "result",
        "structured_output",
        "stop_reason",
        "terminal_reason",
        "num_turns",
        "total_cost_usd",
        "session_id",
    ):
        if hasattr(raw, attr):
            payload[attr] = _jsonable(getattr(raw, attr))
    for attr in ("usage", "model_usage"):
        if hasattr(raw, attr):
            payload[attr] = _jsonable(getattr(raw, attr))
    return payload


class ClaudeProvider(Provider):
    """Claude adapter via ``claude-agent-sdk.query``."""

    name = "claude"
    transport = "claude-agent-sdk"

    def __init__(
        self,
        *,
        auth_source: AuthSource = "auto",
        oauthpy_home: str | os.PathLike[str] | None = None,
    ) -> None:
        self._binary = _claude_binary()
        self._auth_source = normalize_auth_source(auth_source)
        self._oauthpy_home = resolve_oauthpy_home(oauthpy_home)
        self._claude_config_dir = provider_state_dir("claude", self._oauthpy_home)

    async def auth_status(self) -> AuthStatus:
        sdk_present = _sdk() is not None
        claude_bin = _subprocess.which(self._binary)
        base_details = {
            "sdk": "present" if sdk_present else "missing",
            "binary": claude_bin,
            "requested_source": self._auth_source,
            "config_dir": str(self._claude_config_dir),
        }

        if self._auth_source == "auto":
            oauthpy_status: AuthStatus | None = None
            if claude_bin and self._oauthpy_state_plausible():
                oauthpy_status = await self._status_for_source(
                    "oauthpy", claude_bin, sdk_present, requested_source="auto", ensure_state=False
                )
                if oauthpy_status.authenticated:
                    return oauthpy_status
            external_status = await self._status_for_source(
                "external",
                claude_bin,
                sdk_present,
                requested_source="auto",
                ensure_state=False,
            )
            if external_status.authenticated:
                return external_status
            details = dict(external_status.details)
            details.update(base_details)
            details.update(
                {
                    "source": "none",
                    "oauthpy_checked": oauthpy_status is not None,
                    "external_exit_code": external_status.details.get("exit_code"),
                }
            )
            if oauthpy_status is not None:
                details["oauthpy_exit_code"] = oauthpy_status.details.get("exit_code")
            return AuthStatus(
                provider="claude",
                installed=sdk_present,
                authenticated=False,
                mode="unknown",
                details=details,
            )

        return await self._status_for_source(
            self._auth_source,
            claude_bin,
            sdk_present,
            requested_source=self._auth_source,
            ensure_state=self._auth_source == "oauthpy",
        )

    async def _status_for_source(
        self,
        source: AuthSource,
        claude_bin: str | None,
        sdk_present: bool,
        *,
        requested_source: AuthSource,
        ensure_state: bool,
    ) -> AuthStatus:
        if source == "oauthpy" and ensure_state:
            self._ensure_oauthpy_state()
        if claude_bin is None:
            if source == "external":
                env_status = self._env_auth_status(sdk_present, requested_source=requested_source)
                if env_status.authenticated:
                    return env_status
            return AuthStatus(
                provider="claude",
                installed=sdk_present,
                authenticated=False,
                mode="unknown",
                details={
                    "sdk": "present" if sdk_present else "missing",
                    "binary": None,
                    "source": source,
                    "requested_source": requested_source,
                    "config_dir": str(self._claude_config_dir) if source == "oauthpy" else None,
                    "error": "claude CLI not found",
                    "reason": "cli_missing",
                },
            )

        try:
            result = await _subprocess.run(
                [claude_bin, "auth", "status", "--json"],
                env=self._subprocess_env(source),
                timeout=10.0,
            )
        except (CommandExecutionError, TimeoutExceededError) as exc:
            return AuthStatus(
                provider="claude",
                installed=sdk_present,
                authenticated=False,
                mode="unknown",
                details={
                    "sdk": "present" if sdk_present else "missing",
                    "binary": claude_bin,
                    "source": source,
                    "requested_source": requested_source,
                    "config_dir": str(self._claude_config_dir) if source == "oauthpy" else None,
                    "error": str(exc)[:200],
                    "reason": "status_failed",
                },
            )
        status = self._status_from_cli_json(
            result.stdout,
            source=source,
            requested_source=requested_source,
            sdk_present=sdk_present,
            binary=claude_bin,
            exit_code=result.returncode,
        )
        if not status.authenticated and source == "external":
            env_status = self._env_auth_status(sdk_present, requested_source=requested_source)
            if env_status.authenticated:
                details = dict(env_status.details)
                details["status_command_exit_code"] = result.returncode
                return AuthStatus(
                    provider="claude",
                    installed=sdk_present,
                    authenticated=True,
                    mode=env_status.mode,
                    details=details,
                )
        return status

    def _status_from_cli_json(
        self,
        stdout: str,
        *,
        source: AuthSource,
        requested_source: AuthSource,
        sdk_present: bool,
        binary: str,
        exit_code: int,
    ) -> AuthStatus:
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        logged_in = bool(payload.get("loggedIn")) and exit_code == 0
        mode = _claude_mode_from_payload(payload) if logged_in else "unknown"
        return AuthStatus(
            provider="claude",
            installed=sdk_present,
            authenticated=logged_in,
            mode=mode,
            details={
                "sdk": "present" if sdk_present else "missing",
                "binary": binary,
                "source": source,
                "requested_source": requested_source,
                "config_dir": str(self._claude_config_dir) if source == "oauthpy" else None,
                "exit_code": exit_code,
                "logged_in": logged_in,
                "auth_method": _safe_str(payload.get("authMethod")),
                "api_provider": _safe_str(payload.get("apiProvider")),
                "subscription_type": _safe_str(payload.get("subscriptionType")),
                "reason": "authenticated" if logged_in else "not_authenticated",
            },
        )

    def _env_auth_status(
        self,
        sdk_present: bool,
        *,
        requested_source: AuthSource,
    ) -> AuthStatus:
        mode = "unknown"
        env_name: str | None = None
        for key in _CLAUDE_CLOUD_ENV_KEYS:
            if os.environ.get(key):
                mode = "cloud"
                env_name = key
                break
        if env_name is None and os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            mode = "env"
            env_name = "ANTHROPIC_AUTH_TOKEN"
        if env_name is None and os.environ.get("ANTHROPIC_API_KEY"):
            mode = "api-key"
            env_name = "ANTHROPIC_API_KEY"
        if env_name is None and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            mode = "env"
            env_name = "CLAUDE_CODE_OAUTH_TOKEN"
        return AuthStatus(
            provider="claude",
            installed=sdk_present,
            authenticated=env_name is not None,
            mode=mode,
            details={
                "sdk": "present" if sdk_present else "missing",
                "binary": _subprocess.which(self._binary),
                "source": "external" if env_name else "none",
                "requested_source": requested_source,
                "env_auth": env_name,
                "reason": "authenticated" if env_name else "not_authenticated",
            },
        )

    async def login(self) -> None:
        claude_bin = _subprocess.which(self._binary)
        if claude_bin is None:
            raise ProviderNotInstalledError(
                "claude CLI not found. Install Claude Code from https://code.claude.com/."
            )
        source: AuthSource = "oauthpy" if self._auth_source == "auto" else self._auth_source
        if source == "oauthpy":
            self._ensure_oauthpy_state()
        result = await _subprocess.run_interactive(
            [claude_bin, "auth", "login"],
            env=self._subprocess_env(source),
            timeout=None,
        )
        if result.returncode != 0:
            raise CommandExecutionError(
                f"claude auth login exited with code {result.returncode}",
                returncode=result.returncode,
            )

    def _final_text(self, events: list[Event]) -> str:
        final_done = next(
            (e for e in reversed(events) if e.kind is EventKind.DONE and e.text),
            None,
        )
        if final_done is not None and final_done.text:
            return final_done.text
        return super()._final_text(events)

    def _usage(self, events: list[Event]) -> Usage | None:
        for event in reversed(events):
            if event.kind is not EventKind.DONE:
                continue
            raw = event.raw
            if isinstance(raw, Mapping):
                usage = raw.get("usage")
                cost = raw.get("total_cost_usd")
                if isinstance(usage, Mapping):
                    return _usage_from_mapping(usage, cost)
                model_usage = raw.get("modelUsage")
                if isinstance(model_usage, Mapping):
                    for entry in model_usage.values():
                        if isinstance(entry, Mapping):
                            return _usage_from_mapping(entry, cost)
                if cost is not None:
                    return Usage(cost_usd=_float_value(cost))
            usage = getattr(raw, "usage", None)
            model_usage = getattr(raw, "model_usage", None)
            cost = getattr(raw, "total_cost_usd", None)
            if isinstance(usage, Mapping):
                return _usage_from_mapping(usage, cost)
            if isinstance(model_usage, Mapping):
                return _usage_from_mapping(model_usage, cost)
            if cost is not None:
                return Usage(cost_usd=_float_value(cost))
        return None

    def _transport_for_result(
        self,
        events: list[Event],
        provider_options: Mapping[str, Any] | None,
    ) -> TransportName:
        for event in reversed(events):
            if event.kind is EventKind.DONE and isinstance(event.raw, Mapping):
                if "structured_output" in event.raw:
                    return "claude-cli-json"
        return self.transport

    def _raw_result(
        self,
        events: list[Event],
        retry_raw: dict[str, Any] | None,
    ) -> Any:
        for event in reversed(events):
            if event.kind is not EventKind.DONE:
                continue
            structured_output = _structured_output_from_raw(event.raw)
            if structured_output is None:
                continue
            if isinstance(event.raw, Mapping):
                raw: dict[str, Any] = {"claude_cli": event.raw}
            else:
                raw = {"claude_sdk": _sdk_result_payload(event.raw)}
            if retry_raw:
                raw.update(retry_raw)
            return raw
        return retry_raw

    async def _resolve_run_source(self) -> AuthSource:
        if self._auth_source in {"oauthpy", "external"}:
            if self._auth_source == "oauthpy":
                self._ensure_oauthpy_state()
            return self._auth_source

        sdk_present = _sdk() is not None
        claude_bin = _subprocess.which(self._binary)
        if claude_bin and self._oauthpy_state_plausible():
            oauthpy_status = await self._status_for_source(
                "oauthpy", claude_bin, sdk_present, requested_source="auto", ensure_state=False
            )
            if oauthpy_status.authenticated:
                return "oauthpy"
        external_status = await self._status_for_source(
            "external", claude_bin, sdk_present, requested_source="auto", ensure_state=False
        )
        if external_status.authenticated:
            return "external"
        self._ensure_oauthpy_state()
        return "oauthpy"

    async def _stream_once(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        if _json_schema_output_format(provider_options) is not None:
            try:
                events = await self._collect_schema_sdk_once(
                    prompt,
                    cwd=cwd,
                    model=model,
                    timeout=timeout,
                    env=env,
                    provider_options=provider_options,
                )
                self._validate_schema_sdk_events(events)
            except (CommandExecutionError, ProviderNotInstalledError) as exc:
                if not self._schema_cli_fallback_allowed(exc):
                    raise
                async for event in self._stream_schema_cli_once(
                    prompt,
                    cwd=cwd,
                    model=model,
                    timeout=timeout,
                    env=env,
                    provider_options=provider_options,
                ):
                    yield event
                return
            for event in events:
                yield event
            return

        sdk = _sdk()
        if sdk is None:
            raise ProviderNotInstalledError(
                "claude-agent-sdk is not installed. Reinstall oauthpy or run "
                "`pip install claude-agent-sdk`."
            )
        source = await self._resolve_run_source()
        query, options_cls = sdk
        diagnostics = _ClaudeDiagnostics()
        resolved_model = model or DEFAULT_CLAUDE_MODEL
        options = _build_options(
            options_cls,
            cwd=cwd,
            model=model,
            provider_options=provider_options,
            env=self._sdk_env(source, env),
            diagnostics=diagnostics,
        )

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)
        stream_obj: Any | None = None
        iterator: AsyncIterator[Any] | None = None

        async def _next_msg() -> Any:
            if iterator is None:  # pragma: no cover - defensive internal guard
                raise RuntimeError("claude-agent-sdk stream was not initialized")
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
        events_received = 0
        phase = "startup"
        with diagnostics.capture_sdk_logger():
            try:
                stream_obj = query(prompt=prompt, options=options)
                if inspect.iscoroutine(stream_obj):
                    stream_obj = await stream_obj
                iterator = stream_obj.__aiter__()
                phase = "stream"

                while True:
                    try:
                        msg = await _next_msg()
                    except StopAsyncIteration:
                        break
                    error_text = _sdk_error_payload_text(msg)
                    if error_text:
                        phase = "error_payload"
                        raise RuntimeError(error_text)
                    for event in _events_from_sdk_message(msg):
                        if event.kind is EventKind.DONE:
                            emitted_done = True
                        events_received += 1
                        yield event
            except (CommandExecutionError, TimeoutExceededError):
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _claude_runtime_error(
                    exc,
                    diagnostics,
                    source=source,
                    binary=_safe_which(self._binary),
                    model=resolved_model,
                    phase=phase,
                    events_received=events_received,
                ) from exc
            finally:
                if stream_obj is not None:
                    aclose = getattr(stream_obj, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:  # pragma: no cover - best-effort cleanup
                            pass

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)

    async def _collect_schema_sdk_once(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> list[Event]:
        sdk = _sdk()
        if sdk is None:
            raise ProviderNotInstalledError(
                "claude-agent-sdk is not installed. Reinstall oauthpy or run "
                "`pip install claude-agent-sdk`."
            )
        source = await self._resolve_run_source()
        query, options_cls = sdk
        diagnostics = _ClaudeDiagnostics()
        resolved_model = model or DEFAULT_CLAUDE_MODEL
        options = _build_options(
            options_cls,
            cwd=cwd,
            model=model,
            provider_options=_normalize_schema_provider_options(provider_options),
            env=self._sdk_env(source, env),
            diagnostics=diagnostics,
        )

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)
        stream_obj: Any | None = None
        iterator: AsyncIterator[Any] | None = None
        events: list[Event] = []
        emitted_done = False
        events_received = 0
        phase = "startup"

        async def _next_msg() -> Any:
            if iterator is None:  # pragma: no cover - defensive internal guard
                raise RuntimeError("claude-agent-sdk stream was not initialized")
            if deadline is None:
                return await iterator.__anext__()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutExceededError(f"claude stream timed out after {timeout}s")
            try:
                return await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutExceededError(f"claude stream timed out after {timeout}s") from exc

        with diagnostics.capture_sdk_logger():
            try:
                stream_obj = query(prompt=prompt, options=options)
                if inspect.iscoroutine(stream_obj):
                    stream_obj = await stream_obj
                iterator = stream_obj.__aiter__()
                phase = "stream"

                while True:
                    try:
                        msg = await _next_msg()
                    except StopAsyncIteration:
                        break
                    error_text = _sdk_error_payload_text(msg)
                    if error_text:
                        phase = "error_payload"
                        raise RuntimeError(error_text)
                    for event in _events_from_sdk_message(msg):
                        if event.kind is EventKind.DONE:
                            emitted_done = True
                        events_received += 1
                        events.append(event)
            except (CommandExecutionError, TimeoutExceededError):
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                schema_error = self._schema_sdk_error_from_events(events)
                if schema_error is not None:
                    raise schema_error from exc
                raise _claude_runtime_error(
                    exc,
                    diagnostics,
                    source=source,
                    binary=_safe_which(self._binary),
                    model=resolved_model,
                    phase=phase,
                    events_received=events_received,
                ) from exc
            finally:
                if stream_obj is not None:
                    aclose = getattr(stream_obj, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:  # pragma: no cover - best-effort cleanup
                            pass

        if not emitted_done:
            events.append(Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None))
        return events

    def _validate_schema_sdk_events(self, events: list[Event]) -> None:
        schema_error = self._schema_sdk_error_from_events(events)
        if schema_error is not None:
            raise schema_error
        for event in reversed(events):
            if event.kind is EventKind.DONE:
                if _structured_output_from_raw(event.raw) is not None:
                    return
        raise CommandExecutionError(
            "Claude structured-output SDK did not return structured_output.",
            details={"provider": "claude", "transport": self.transport},
        )

    def _schema_sdk_error_from_events(
        self, events: list[Event]
    ) -> CommandExecutionError | None:
        for event in reversed(events):
            raw = event.raw
            is_error = event.kind is EventKind.ERROR
            if isinstance(raw, Mapping):
                is_error = is_error or bool(raw.get("is_error"))
                payload = dict(raw)
            else:
                is_error = is_error or bool(getattr(raw, "is_error", False))
                payload = _sdk_result_payload(raw)
            if not is_error:
                continue
            text = _first_str(
                event.text,
                payload.get("result"),
                payload.get("error"),
                "Claude structured-output SDK returned an error result.",
            )
            error_kind = (
                "policy_refusal"
                if _is_policy_refusal_payload(payload, text)
                else "sdk_error_result"
            )
            return CommandExecutionError(
                f"Claude structured-output SDK returned an error result: {text}",
                details={
                    "provider": "claude",
                    "transport": self.transport,
                    "error_kind": error_kind,
                    "sdk_result": payload,
                },
            )
        return None

    def _schema_cli_fallback_allowed(self, exc: Exception) -> bool:
        if isinstance(exc, ProviderNotInstalledError):
            return True
        if not isinstance(exc, CommandExecutionError):
            return False
        text = _diagnostic_text(exc)
        return "claudeagentoptions rejected" in text and "output_format" in text

    async def _stream_schema_cli_once(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        claude_bin = _subprocess.which(self._binary)
        if claude_bin is None:
            raise ProviderNotInstalledError(
                "claude CLI not found. Install Claude Code from https://code.claude.com/."
            )
        source = await self._resolve_run_source()
        options = _normalize_schema_provider_options(provider_options)
        output_format = _json_schema_output_format(options)
        if output_format is None:  # pragma: no cover - guarded by caller
            raise ProtocolError(
                "Claude JSON schema CLI mode requires output_format.type=json_schema"
            )
        schema = output_format.get("schema")
        if not isinstance(schema, Mapping):
            raise ProtocolError("Claude output_format.schema must be a JSON schema mapping.")
        options.pop("output_format", None)
        argv = self._schema_cli_argv(
            claude_bin,
            schema=schema,
            model=model,
            provider_options=options,
        )
        result = await _subprocess.run(
            argv,
            cwd=cwd,
            env=self._cli_env(source, env),
            timeout=timeout,
            stdin=prompt,
        )
        if result.returncode != 0:
            raise CommandExecutionError(
                f"claude --print exited with code {result.returncode}",
                returncode=result.returncode,
                stderr=result.stderr,
                details={
                    "provider": "claude",
                    "transport": "claude-cli-json",
                    "source": source,
                    "binary": claude_bin,
                    "model": model or options.get("model") or DEFAULT_CLAUDE_MODEL,
                    "stdout_tail": result.stdout[-2000:] or None,
                    "stderr_tail": result.stderr[-2000:] or None,
                },
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProtocolError(
                "Claude structured-output CLI returned invalid JSON: "
                f"{result.stdout[:500]!r}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProtocolError("Claude structured-output CLI returned non-object JSON.")
        if payload.get("is_error"):
            raise CommandExecutionError(
                "Claude structured-output CLI returned an error result.",
                returncode=None,
                stderr=result.stderr,
                details={
                    "provider": "claude",
                    "transport": "claude-cli-json",
                    "source": source,
                    "binary": claude_bin,
                    "payload": payload,
                },
            )
        if "structured_output" not in payload:
            raise ProtocolError(
                "Claude structured-output CLI response did not include structured_output."
            )
        text = json.dumps(payload["structured_output"], separators=(",", ":"))
        yield Event(kind=EventKind.MESSAGE, text=text, timestamp=None, raw=payload)
        yield Event(kind=EventKind.DONE, text=text, timestamp=None, raw=payload)

    def _schema_cli_argv(
        self,
        claude_bin: str,
        *,
        schema: Mapping[str, Any],
        model: str | None,
        provider_options: Mapping[str, Any],
    ) -> list[str]:
        options = dict(provider_options)
        configured_model = options.pop("model", None)
        resolved_model = model or configured_model or DEFAULT_CLAUDE_MODEL
        reasoning_effort = options.pop("reasoning_effort", DEFAULT_CLAUDE_REASONING_EFFORT)
        if "effort" not in options and reasoning_effort is not None:
            options["effort"] = reasoning_effort
        argv = [
            claude_bin,
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--model",
            str(resolved_model),
        ]
        effort = options.pop("effort", None)
        if effort is not None:
            argv.extend(["--effort", str(effort)])
        permission_mode = options.pop("permission_mode", None)
        if permission_mode is not None:
            argv.extend(["--permission-mode", str(permission_mode)])
        max_turns = options.pop("max_turns", None)
        if max_turns is not None:
            argv.extend(["--max-turns", str(_claude_schema_max_turns(max_turns))])
        max_budget = options.pop("max_budget_usd", None)
        if max_budget is not None:
            argv.extend(["--max-budget-usd", str(max_budget)])
        tools = options.pop("tools", None)
        if tools is not None:
            argv.extend(["--tools", _claude_cli_list_value(tools)])
        allowed_tools = options.pop("allowed_tools", None)
        if allowed_tools:
            argv.extend(["--allowed-tools", _claude_cli_list_value(allowed_tools)])
        disallowed_tools = options.pop("disallowed_tools", None)
        if disallowed_tools:
            argv.extend(["--disallowed-tools", _claude_cli_list_value(disallowed_tools)])
        extra_args = options.pop("extra_args", None)
        if isinstance(extra_args, Mapping):
            for flag, value in extra_args.items():
                argv.append(f"--{flag}")
                if value is not None:
                    argv.append(str(value))
        if options:
            unsupported = ", ".join(sorted(str(key) for key in options))
            raise ProtocolError(
                "unsupported Claude structured-output provider_options keys: "
                f"{unsupported}"
            )
        return argv

    def _retry_decision(
        self,
        exc: Exception,
        *,
        policy: RetryPolicy,
        events_yielded: int,
    ) -> RetryDecision:
        if events_yielded:
            return RetryDecision(False, "events_already_yielded")
        if isinstance(exc, TimeoutExceededError):
            return RetryDecision(policy.retry_on_timeout, "timeout")
        if not isinstance(exc, CommandExecutionError):
            return RetryDecision(False, "not_retryable")
        text = _diagnostic_text(exc)
        if _has_marker(text, _CLAUDE_NON_RETRYABLE_MARKERS):
            return RetryDecision(False, "non_retryable_claude_error")
        if _has_marker(text, _CLAUDE_READER_FAILURE_MARKERS):
            return RetryDecision(True, "transient_claude_reader_failure")
        return RetryDecision(False, "claude_command_error")

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
            "binary": _safe_which(self._binary),
            "requested_source": self._auth_source,
            "model": model or DEFAULT_CLAUDE_MODEL,
            "cwd": os.fspath(cwd) if cwd is not None else None,
        }

    def _ensure_oauthpy_state(self) -> None:
        ensure_private_dir(self._oauthpy_home)
        ensure_private_dir(self._claude_config_dir)

    def _oauthpy_state_plausible(self) -> bool:
        if not self._claude_config_dir.exists():
            return False
        return (
            any(
                (self._claude_config_dir / name).exists()
                for name in (".credentials.json", ".claude.json", "settings.json", "projects")
            )
            or (self._claude_config_dir / ".claude" / ".credentials.json").exists()
        )

    def _subprocess_env(self, source: AuthSource) -> dict[str, str | None] | None:
        if source != "oauthpy":
            return None
        env: dict[str, str | None] = {key: None for key in _CLAUDE_AUTH_ENV_KEYS}
        env["CLAUDE_CONFIG_DIR"] = str(self._claude_config_dir)
        return env

    def _sdk_env(
        self,
        source: AuthSource,
        user_env: Mapping[str, str] | None = None,
    ) -> dict[str, str] | None:
        env: dict[str, str] = {}
        if source == "oauthpy":
            env.update({key: "" for key in _CLAUDE_AUTH_ENV_KEYS})
            if user_env:
                env.update(user_env)
            env["CLAUDE_CONFIG_DIR"] = str(self._claude_config_dir)
        elif user_env:
            env.update(user_env)
        return env or None

    def _cli_env(
        self,
        source: AuthSource,
        user_env: Mapping[str, str] | None = None,
    ) -> dict[str, str | None] | None:
        env = dict(self._subprocess_env(source) or {})
        if user_env:
            env.update(user_env)
        return env or None


def _claude_mode_from_payload(payload: Mapping[str, Any]) -> str:
    method = str(payload.get("authMethod") or "").lower()
    provider = str(payload.get("apiProvider") or "").lower()
    if any(name in method or name in provider for name in ("bedrock", "vertex", "foundry")):
        return "cloud"
    if "api" in method or "key" in method:
        return "api-key"
    if method in {"claude.ai", "oauth", "subscription"} or provider == "firstparty":
        return "oauth"
    return "unknown"


def _safe_which(binary: str) -> str:
    try:
        return _subprocess.which(binary) or binary
    except Exception:
        return binary


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _join_strs(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [part for part in value if isinstance(part, str) and part]
        return "\n".join(parts) if parts else None
    return value if isinstance(value, str) and value else None


def _sdk_error_payload_text(msg: Any) -> str | None:
    if not isinstance(msg, Mapping):
        return None
    if msg.get("type") != "error" and "error" not in msg:
        return None
    return _error_text(msg.get("error") or msg.get("message") or msg)


def _error_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        return _first_str(
            value.get("message"),
            value.get("error"),
            value.get("type"),
            value.get("code"),
        )
    return str(value) if value else None


def _claude_runtime_error(
    exc: Exception,
    diagnostics: _ClaudeDiagnostics,
    *,
    source: AuthSource,
    binary: str,
    model: str,
    phase: str,
    events_received: int,
) -> CommandExecutionError:
    message = f"claude-agent-sdk run failed: {exc or type(exc).__name__}"
    sdk_logs = diagnostics.sdk_log_tail()
    if sdk_logs:
        message += f"\nclaude-agent-sdk logs:\n{sdk_logs}"
    stderr = diagnostics.stderr_tail()
    if stderr:
        message += f"\nclaude stderr:\n{stderr}"
    returncode = _returncode_from_exception(exc)
    return CommandExecutionError(
        redact(message),
        returncode=returncode,
        stderr=stderr or None,
        details={
            "provider": "claude",
            "transport": "claude-agent-sdk",
            "source": source,
            "binary": binary,
            "model": model,
            "phase": phase,
            "events_received": events_received,
            "returncode": returncode,
            "sdk_log_tail": sdk_logs or None,
            "stderr_tail": stderr or None,
        },
    )


def _returncode_from_exception(exc: Exception) -> int | None:
    for attr in ("returncode", "exit_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    match = _EXIT_CODE_RE.search(str(exc))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - guarded by regex
        return None


def _diagnostic_text(exc: CommandExecutionError) -> str:
    details = getattr(exc, "details", {})
    parts = [str(exc), exc.stderr or ""]
    if isinstance(details, Mapping):
        for key in ("sdk_log_tail", "stderr_tail", "phase"):
            value = details.get(key)
            if value is not None:
                parts.append(str(value))
    return "\n".join(parts).lower()


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _usage_from_mapping(raw: Mapping[str, Any], cost: Any = None) -> Usage:
    input_tokens = _int_value(
        raw.get("input_tokens")
        or raw.get("inputTokens")
        or raw.get("total_input_tokens")
        or raw.get("totalInputTokens")
    )
    output_tokens = _int_value(
        raw.get("output_tokens")
        or raw.get("outputTokens")
        or raw.get("total_output_tokens")
        or raw.get("totalOutputTokens")
    )
    total_tokens = _int_value(raw.get("total_tokens") or raw.get("totalTokens"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=_float_value(cost or raw.get("cost_usd") or raw.get("total_cost_usd")),
    )


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_value(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


__all__ = ["ClaudeProvider"]
