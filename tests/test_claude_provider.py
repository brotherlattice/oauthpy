"""Tests for :class:`oauthpy.providers.claude.ClaudeProvider`.

The ``claude-agent-sdk`` dependency is replaced with ``fixtures.fake_claude_sdk``
via a monkeypatched ``_sdk`` function so these tests run offline with no real
Claude install.
"""

from __future__ import annotations

import pytest

from oauthpy import EventKind, ProviderNotInstalledError
from oauthpy._subprocess import CompletedProcess
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
    provider = claude_mod.ClaudeProvider(auth_source="oauthpy", oauthpy_home="/tmp/oauthpy-test")
    await provider.run("hello", env={"ANTHROPIC_API_KEY": "explicit"})
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/oauthpy-test/claude"
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
