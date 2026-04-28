"""Tests for :class:`oauthpy.providers.claude.ClaudeProvider`.

The ``claude-agent-sdk`` dependency is replaced with ``fixtures.fake_claude_sdk``
via a monkeypatched ``_sdk`` function so these tests run offline with no real
Claude install.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from oauthpy import (
    CommandExecutionError,
    EventKind,
    ProviderNotInstalledError,
    TimeoutExceededError,
)
from oauthpy._subprocess import CompletedProcess
from oauthpy.defaults import DEFAULT_CLAUDE_MODEL, DEFAULT_CLAUDE_REASONING_EFFORT
from oauthpy.providers import claude as claude_mod
from tests.fixtures import fake_claude_sdk


@pytest.fixture(autouse=True)
def no_real_claude_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider unit tests must install an explicit fake Claude CLI state."""

    def fail_unmocked_which(binary: str) -> str | None:
        raise AssertionError(f"test attempted real provider discovery for {binary!r}")

    monkeypatch.setattr(claude_mod._subprocess, "which", fail_unmocked_which)


@pytest.fixture()
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> list:
    """Install a fake claude-agent-sdk. Returns the (mutable) messages list."""

    messages: list = fake_claude_sdk.default_messages()

    def _sdk_factory() -> tuple:
        return fake_claude_sdk.make_query(messages), fake_claude_sdk.ClaudeAgentOptions

    monkeypatch.setattr(claude_mod, "_sdk", _sdk_factory)
    return messages


async def test_run_returns_result(fake_sdk: list, clean_env: None) -> None:
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run("hello", cwd="/tmp/work", model="claude-opus-4-7")
    assert result.provider == "claude"
    assert result.transport == "claude-agent-sdk"
    # text should be pulled from the ResultMessage payload (final DONE).
    assert result.text == "ok"
    assert any(e.kind is EventKind.DONE for e in result.events)
    assert any(e.kind is EventKind.MESSAGE for e in result.events)


async def test_run_defaults_to_opus_low_effort(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    messages = fake_claude_sdk.default_messages()

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["model"] = options.model
        captured["effort"] = options.effort
        for message in messages:
            yield message

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    await provider.run("hello")
    assert captured["model"] == DEFAULT_CLAUDE_MODEL
    assert captured["effort"] == DEFAULT_CLAUDE_REASONING_EFFORT


async def test_provider_options_override_claude_default_effort(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    messages = fake_claude_sdk.default_messages()

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["model"] = options.model
        captured["effort"] = options.effort
        for message in messages:
            yield message

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    await provider.run("hello", model="sonnet", provider_options={"reasoning_effort": "high"})
    assert captured["model"] == "sonnet"
    assert captured["effort"] == "high"


async def test_stream_yields_events_in_order(fake_sdk: list, clean_env: None) -> None:
    provider = claude_mod.ClaudeProvider(auth_source="external")
    kinds = []
    async for ev in provider.stream("hello"):
        kinds.append(ev.kind)
    assert EventKind.MESSAGE in kinds
    assert EventKind.DONE in kinds
    assert kinds[-1] is EventKind.DONE


async def test_run_raises_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod, "_sdk", lambda: None)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(ProviderNotInstalledError):
        await provider.run("hi")


async def test_login_missing_cli_raises(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(ProviderNotInstalledError):
        await provider.login()


async def test_auth_status_no_sdk_no_cli(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(claude_mod, "_sdk", lambda: None)
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.installed is False
    assert status.authenticated is False
    assert status.details["reason"] == "cli_missing"


async def test_available_false_without_auth(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    assert await provider.available() is False


async def test_auth_status_env_var_mode(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "some-token-value")
    provider = claude_mod.ClaudeProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is True
    assert status.mode == "env"
    assert status.details["reason"] == "authenticated"


async def test_auth_status_api_key_mode(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abcdefghijklmnop")
    provider = claude_mod.ClaudeProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "api-key"
    assert status.details["reason"] == "authenticated"


async def test_auth_status_uses_claude_auth_status_json(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        assert argv == ["/usr/bin/claude", "auth", "status", "--json"]
        return CompletedProcess(
            returncode=0,
            stdout='{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty"}',
            stderr="",
        )

    monkeypatch.setattr(claude_mod._subprocess, "run", fake_run)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "oauth"
    assert status.details["auth_method"] == "claude.ai"
    assert status.details["reason"] == "authenticated"


async def test_auto_falls_back_when_oauthpy_status_times_out(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None, tmp_path
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")
    claude_home = tmp_path / "claude"
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text("{}", encoding="utf-8")

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        env = kwargs.get("env")
        if isinstance(env, dict) and env.get("CLAUDE_CONFIG_DIR") == str(claude_home):
            raise claude_mod.TimeoutExceededError("status timed out")
        return CompletedProcess(
            returncode=0,
            stdout='{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty"}',
            stderr="",
        )

    monkeypatch.setattr(claude_mod._subprocess, "run", fake_run)
    provider = claude_mod.ClaudeProvider(auth_source="auto", oauthpy_home=tmp_path)
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.details["source"] == "external"


async def test_auth_status_unknown(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: None)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.authenticated is False
    assert status.mode == "unknown"
    assert status.details["reason"] == "cli_missing"


async def test_classifier_handles_unknown_object() -> None:
    class Weird:
        text = "something"

    kind, text = claude_mod._classify_sdk_message(Weird())
    assert kind is EventKind.MESSAGE
    assert text == "something"


async def test_classifier_result_message_without_text() -> None:
    class FakeResult:
        result = None

    kind, text = claude_mod._classify_sdk_message(FakeResult())
    assert kind is EventKind.DONE
    assert text is None or text == ""


async def test_login_uses_claude_auth_login(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")

    async def fake_run_interactive(argv: list[str], **kwargs: object) -> CompletedProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(claude_mod._subprocess, "run_interactive", fake_run_interactive)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    await provider.login()
    assert captured["argv"] == ["/usr/bin/claude", "auth", "login"]


async def test_env_passed_to_claude_agent_options(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    messages = fake_claude_sdk.default_messages()

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["env"] = options.env
        for message in messages:
            yield message

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="oauthpy", oauthpy_home=tmp_path)
    await provider.run("hello", env={"ANTHROPIC_API_KEY": "explicit"})
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert env["ANTHROPIC_API_KEY"] == "explicit"


async def test_external_source_does_not_set_claude_config_dir(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    messages = fake_claude_sdk.default_messages()

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["env"] = options.env
        for message in messages:
            yield message

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    await provider.run("hello")
    assert captured["env"] == {}


async def test_tool_blocks_classified(fake_sdk: list, clean_env: None) -> None:
    fake_sdk[:] = [
        fake_claude_sdk.AssistantMessage(
            content=[
                fake_claude_sdk.ToolUseBlock(id="1", name="Read", input={}),
                fake_claude_sdk.ToolResultBlock(tool_use_id="1", content="ok"),
            ]
        ),
        fake_claude_sdk.ResultMessage(result="done"),
    ]
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run("hello")
    assert [event.kind for event in result.events].count(EventKind.TOOL) == 2


async def test_result_message_usage_and_cost_extracted(fake_sdk: list, clean_env: None) -> None:
    fake_sdk[:] = [
        fake_claude_sdk.ResultMessage(
            result="done",
            usage={"input_tokens": 4, "output_tokens": 6},
            total_cost_usd=0.12,
        )
    ]
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run("hello")
    assert result.usage is not None
    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 6
    assert result.usage.total_tokens == 10
    assert result.usage.cost_usd == 0.12


async def test_structured_output_uses_sdk_primary(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    schema = {
        "type": "object",
        "properties": {"relationship": {"type": "integer"}, "unrelated": {"type": "integer"}},
        "required": ["relationship", "unrelated"],
    }

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["prompt"] = prompt
        captured["output_format"] = options.output_format
        yield fake_claude_sdk.ResultMessage(
            result="",
            structured_output={"relationship": 0, "unrelated": 1},
            subtype="success",
            usage={"input_tokens": 2, "output_tokens": 3},
        )

    async def fail_cli(*args: object, **kwargs: object) -> CompletedProcess:
        raise AssertionError("structured-output SDK success should not fall back to CLI")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    monkeypatch.setattr(claude_mod._subprocess, "run", fail_cli)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run(
        "classify",
        provider_options={"output_format": {"type": "json_schema", "schema": schema}},
    )

    assert captured["prompt"] == "classify"
    assert captured["output_format"] == {"type": "json_schema", "schema": schema}
    assert result.transport == "claude-agent-sdk"
    assert result.text == '{"relationship":0,"unrelated":1}'
    assert result.usage is not None
    assert result.usage.total_tokens == 5
    assert isinstance(result.raw, dict)
    assert result.raw["claude_sdk"]["structured_output"] == {"relationship": 0, "unrelated": 1}


async def test_structured_output_max_turns_is_at_least_two(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        captured["max_turns"] = options.max_turns
        yield fake_claude_sdk.ResultMessage(
            result="",
            structured_output={"ok": True},
            subtype="success",
        )

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    await provider.run(
        "classify",
        provider_options={
            "output_format": {"type": "json_schema", "schema": schema},
            "max_turns": 1,
        },
    )
    assert captured["max_turns"] == 2


async def test_structured_output_sdk_error_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        yield fake_claude_sdk.ResultMessage(
            result="I cannot comply with that request.",
            is_error=True,
            subtype="error",
            stop_reason="refusal",
            num_turns=1,
        )

    async def fail_cli(*args: object, **kwargs: object) -> CompletedProcess:
        raise AssertionError("SDK error results must not fall back to CLI")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    monkeypatch.setattr(claude_mod._subprocess, "run", fail_cli)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run(
            "classify",
            provider_options={"output_format": {"type": "json_schema", "schema": schema}},
        )

    assert "Claude structured-output SDK returned an error result" in str(excinfo.value)
    assert excinfo.value.details["transport"] == "claude-agent-sdk"
    assert excinfo.value.details["sdk_result"]["stop_reason"] == "refusal"


async def test_structured_output_missing_sdk_falls_back_to_cli(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    captured: dict[str, object] = {}
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    monkeypatch.setattr(claude_mod, "_sdk", lambda: None)
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcess(
            returncode=0,
            stdout='{"structured_output": {"ok": true}, "usage": {"input_tokens": 1}}',
            stderr="",
        )

    monkeypatch.setattr(claude_mod._subprocess, "run", fake_run)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run(
        "classify",
        cwd="/tmp/work",
        model="opus",
        provider_options={
            "output_format": {"type": "json_schema", "schema": schema},
            "max_turns": 1,
            "permission_mode": "dontAsk",
            "allowed_tools": ["Read"],
            "disallowed_tools": ["Bash"],
        },
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:5] == ["/usr/bin/claude", "--print", "--output-format", "json", "--json-schema"]
    assert "--model" in argv and "opus" in argv
    assert "--max-turns" in argv and "2" in argv
    assert "--permission-mode" in argv and "dontAsk" in argv
    assert "--allowedTools" in argv and "Read" in argv
    assert "--disallowedTools" in argv and "Bash" in argv
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == "/tmp/work"
    assert kwargs["stdin"] == "classify"
    assert result.transport == "claude-cli-json"
    assert result.text == '{"ok":true}'
    assert isinstance(result.raw, dict)
    assert result.raw["claude_cli"]["structured_output"] == {"ok": True}


async def test_structured_output_options_rejection_falls_back_to_cli(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    class NoOutputFormatOptions(fake_claude_sdk.ClaudeAgentOptions):
        def __init__(self, **kwargs: object) -> None:
            if "output_format" in kwargs:
                raise TypeError("unexpected keyword argument 'output_format'")
            super().__init__(**kwargs)

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        raise AssertionError("query should not run when options reject output_format")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(claude_mod, "_sdk", lambda: (fake_query, NoOutputFormatOptions))
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")
    monkeypatch.setattr(claude_mod._subprocess, "run", fake_run)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run(
        "classify",
        provider_options={"output_format": {"type": "json_schema", "schema": schema}},
    )
    assert result.transport == "claude-cli-json"
    assert result.text == '{"ok":true}'


async def test_structured_output_cli_nonzero_raises(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    monkeypatch.setattr(claude_mod, "_sdk", lambda: None)
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _binary: "/usr/bin/claude")

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        return CompletedProcess(returncode=2, stdout="", stderr="raw sk-ant-abcdefghijklmnop123")

    monkeypatch.setattr(claude_mod._subprocess, "run", fake_run)
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run(
            "classify",
            provider_options={"output_format": {"type": "json_schema", "schema": schema}},
        )
    assert "CLI fallback failed" in str(excinfo.value)
    assert excinfo.value.returncode == 2
    assert "sk-ant-abcdefghijklmnop" not in str(excinfo.value)


async def test_sdk_reader_failure_wrapped_with_diagnostics(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("claude_agent_sdk._internal.query")
    caplog.set_level(logging.ERROR)

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        assert callable(options.stderr)
        logger.error("Fatal error in message reader: Command failed with exit code 1")
        options.stderr("raw claude stderr: permission denied")
        raise RuntimeError("Command failed with exit code 1")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run("boom")

    message = str(excinfo.value)
    assert "claude-agent-sdk run failed: Command failed with exit code 1" in message
    assert "claude-agent-sdk logs:" in message
    assert "Fatal error in message reader: Command failed with exit code 1" in message
    assert "claude stderr:" in message
    assert "raw claude stderr: permission denied" in message
    assert excinfo.value.returncode == 1
    assert excinfo.value.stderr == "raw claude stderr: permission denied"
    assert not any(
        "Fatal error in message reader" in record.getMessage() for record in caplog.records
    )


async def test_caller_stderr_callback_still_runs(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    seen: list[str] = []

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        assert callable(options.stderr)
        options.stderr("claude stderr line")
        yield fake_claude_sdk.ResultMessage(result="ok")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run("hello", provider_options={"stderr": seen.append})
    assert result.text == "ok"
    assert seen == ["claude stderr line"]


async def test_sdk_failure_diagnostics_are_redacted(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz1234"
    logger = logging.getLogger("claude_agent_sdk._internal.query")

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        assert callable(options.stderr)
        logger.error("Fatal token %s", secret)
        options.stderr(f"raw claude stderr: Bearer abc.def.ghi {secret}")
        raise RuntimeError(f"Command failed with exit code 1: {secret}")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run("boom")

    message = str(excinfo.value)
    assert secret not in message
    assert "Bearer abc.def.ghi" not in message
    assert "***REDACTED***" in message
    assert secret not in (excinfo.value.stderr or "")


async def test_sdk_error_payload_raises_command_execution_error(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        yield {"type": "error", "error": "reader failed"}

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run("boom")
    assert "reader failed" in str(excinfo.value)


async def test_sdk_timeout_is_not_wrapped(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        await asyncio.sleep(1)
        yield fake_claude_sdk.ResultMessage(result="never")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(TimeoutExceededError):
        await provider.run("slow", timeout=0.001)


async def test_existing_command_execution_error_is_not_wrapped(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        raise CommandExecutionError("already structured", returncode=7, stderr="raw stderr")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run("boom")
    assert str(excinfo.value) == "already structured"
    assert excinfo.value.returncode == 7
    assert excinfo.value.stderr == "raw stderr"


async def test_reader_failure_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Fatal error in message reader: Command failed with exit code 1")
        yield fake_claude_sdk.AssistantMessage(content=[fake_claude_sdk.TextBlock(text="retry ok")])
        yield fake_claude_sdk.ResultMessage(result='{"relationship": 1, "unrelated": 0}')

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    result = await provider.run(
        "classify",
        provider_options={"max_retries": 2, "retry_backoff_s": 0, "retry_jitter_s": 0},
    )

    assert calls == 2
    assert result.text == '{"relationship": 1, "unrelated": 0}'
    assert isinstance(result.raw, dict)
    assert result.raw["retry"]["retry_count"] == 1
    assert result.raw["retry"]["failed_attempts"][0]["retryable"] is True


async def test_reader_failure_default_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        raise RuntimeError("Fatal error in message reader: Command failed with exit code 1")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError):
        await provider.run("boom")
    assert calls == 1


async def test_reader_failure_exhaustion_includes_all_attempts(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        options.stderr(f"stderr attempt {calls}")
        raise RuntimeError("Fatal error in message reader: Command failed with exit code 1")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError) as excinfo:
        await provider.run(
            "boom",
            provider_options={"max_retries": 1, "retry_backoff_s": 0, "retry_jitter_s": 0},
        )

    assert calls == 2
    message = str(excinfo.value)
    assert "failed after 2 attempts" in message
    assert "attempt 1" in message
    assert "attempt 2" in message
    assert excinfo.value.details["attempts"][0]["details"]["stderr_tail"] == "stderr attempt 1"
    assert excinfo.value.details["attempts"][1]["details"]["stderr_tail"] == "stderr attempt 2"


async def test_reader_retry_does_not_retry_auth_like_errors(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        raise RuntimeError("not logged in: run claude auth login")
        yield fake_claude_sdk.ResultMessage(result="never")  # pragma: no cover

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError):
        await provider.run(
            "boom",
            provider_options={"max_retries": 2, "retry_backoff_s": 0, "retry_jitter_s": 0},
        )
    assert calls == 1


async def test_stream_reader_failure_retries_before_events(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Fatal error in message reader: Command failed with exit code 1")
        yield fake_claude_sdk.ResultMessage(result="ok")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    events = [
        event
        async for event in provider.stream(
            "boom",
            provider_options={"max_retries": 1, "retry_backoff_s": 0, "retry_jitter_s": 0},
        )
    ]
    assert calls == 2
    assert events[-1].kind is EventKind.DONE


async def test_stream_reader_failure_after_event_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    calls = 0

    async def fake_query(*, prompt: str, options: fake_claude_sdk.ClaudeAgentOptions):
        nonlocal calls
        calls += 1
        yield fake_claude_sdk.AssistantMessage(content=[fake_claude_sdk.TextBlock(text="partial")])
        raise RuntimeError("Fatal error in message reader: Command failed with exit code 1")

    monkeypatch.setattr(
        claude_mod,
        "_sdk",
        lambda: (fake_query, fake_claude_sdk.ClaudeAgentOptions),
    )
    provider = claude_mod.ClaudeProvider(auth_source="external")
    with pytest.raises(CommandExecutionError):
        async for _ in provider.stream(
            "boom",
            provider_options={"max_retries": 2, "retry_backoff_s": 0, "retry_jitter_s": 0},
        ):
            pass
    assert calls == 1
