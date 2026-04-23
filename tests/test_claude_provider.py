"""Tests for :class:`oauthpy.providers.claude.ClaudeProvider`.

The ``claude-agent-sdk`` dependency is replaced with ``fixtures.fake_claude_sdk``
via a monkeypatched ``_sdk`` function so these tests run offline with no real
Claude install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oauthpy import EventKind, ProviderNotInstalledError
from oauthpy.providers import claude as claude_mod
from tests.fixtures import fake_claude_sdk


@pytest.fixture()
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> list:
    """Install a fake claude-agent-sdk. Returns the (mutable) messages list."""

    messages: list = fake_claude_sdk.default_messages()

    def _sdk_factory() -> tuple:
        return fake_claude_sdk.make_query(messages), fake_claude_sdk.ClaudeAgentOptions

    monkeypatch.setattr(claude_mod, "_sdk", _sdk_factory)
    return messages


async def test_run_returns_result(fake_sdk: list, clean_env: None) -> None:
    provider = claude_mod.ClaudeProvider()
    result = await provider.run("hello", cwd="/tmp/work", model="claude-opus-4-7")
    assert result.provider == "claude"
    assert result.transport == "claude-agent-sdk"
    # text should be pulled from the ResultMessage payload (final DONE).
    assert result.text == "ok"
    assert any(e.kind is EventKind.DONE for e in result.events)
    assert any(e.kind is EventKind.MESSAGE for e in result.events)


async def test_stream_yields_events_in_order(fake_sdk: list, clean_env: None) -> None:
    provider = claude_mod.ClaudeProvider()
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
    provider = claude_mod.ClaudeProvider()
    with pytest.raises(ProviderNotInstalledError):
        await provider.run("hi")


async def test_auth_status_env_var_mode(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "some-token-value")
    provider = claude_mod.ClaudeProvider()
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is True
    assert status.mode == "env"


async def test_auth_status_api_key_mode(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, clean_env: None
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abcdefghijklmnop")
    provider = claude_mod.ClaudeProvider()
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "api-key"


async def test_auth_status_login_state_mode(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, tmp_path: Path, clean_env: None
) -> None:
    # Point CLAUDE_CONFIG_HOME at a tmp dir with a .claude.json file.
    fake_json = tmp_path / ".claude.json"
    fake_json.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_HOME", str(tmp_path))
    provider = claude_mod.ClaudeProvider()
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "login-state"


async def test_auth_status_unknown(
    monkeypatch: pytest.MonkeyPatch, fake_sdk: list, tmp_path: Path, clean_env: None
) -> None:
    # CLAUDE_CONFIG_HOME points at a dir *without* a .claude.json — so unknown.
    monkeypatch.setenv("CLAUDE_CONFIG_HOME", str(tmp_path))
    provider = claude_mod.ClaudeProvider()
    status = await provider.auth_status()
    assert status.authenticated is False
    assert status.mode == "unknown"


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
