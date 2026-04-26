"""Claude provider: drives the official ``claude-agent-sdk``."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .. import _subprocess
from ..auth import (
    ensure_private_dir,
    normalize_auth_source,
    provider_state_dir,
    resolve_oauthpy_home,
)
from ..errors import CommandExecutionError, ProviderNotInstalledError, TimeoutExceededError
from ..models import AuthSource, AuthStatus, Event, EventKind, Usage
from .base import Provider

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
        events.append(
            Event(kind=EventKind.DONE, text=result if isinstance(result, str) else None, raw=msg)
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
) -> Any:
    """Construct ``ClaudeAgentOptions`` from oauthpy's run args."""

    kwargs: dict[str, Any] = {}
    if cwd is not None:
        kwargs["cwd"] = os.fspath(cwd)
    if model is not None:
        kwargs["model"] = model
    if provider_options:
        kwargs.update(provider_options)
    if env:
        merged_env = dict(kwargs.get("env") or {})
        merged_env.update(env)
        kwargs["env"] = merged_env
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
        source = await self._resolve_run_source()
        query, options_cls = sdk
        options = _build_options(
            options_cls,
            cwd=cwd,
            model=model,
            provider_options=provider_options,
            env=self._sdk_env(source, env),
        )

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
                for event in _events_from_sdk_message(msg):
                    if event.kind is EventKind.DONE:
                        emitted_done = True
                    yield event
        finally:
            aclose = getattr(stream_obj, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass

        if not emitted_done:
            yield Event(kind=EventKind.DONE, text=None, timestamp=None, raw=None)

    def _ensure_oauthpy_state(self) -> None:
        ensure_private_dir(self._oauthpy_home)
        ensure_private_dir(self._claude_config_dir)

    def _oauthpy_state_plausible(self) -> bool:
        if not self._claude_config_dir.exists():
            return False
        return any(
            (self._claude_config_dir / name).exists()
            for name in (".credentials.json", ".claude.json", "settings.json", "projects")
        ) or (self._claude_config_dir / ".claude" / ".credentials.json").exists()

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
