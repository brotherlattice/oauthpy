"""Tests for :class:`oauthpy.providers.codex.CodexProvider` with mocked subprocess."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from oauthpy import EventKind, ProviderNotInstalledError
from oauthpy._subprocess import CompletedProcess
from oauthpy.providers import codex as codex_mod


async def test_auth_status_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    status = await provider.auth_status()
    assert status.installed is False
    assert status.authenticated is False
    assert status.mode == "unknown"


async def test_auth_status_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider()
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is True
    assert status.mode == "oauth"


async def test_auth_status_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider()
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is False


async def test_run_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    with pytest.raises(ProviderNotInstalledError):
        await provider.run("hello")


async def test_run_streams_and_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        for line in (
            '{"type": "session.created"}',
            '{"type": "agent_message", "text": "hello"}',
            '{"type": "agent_message", "text": "there"}',
            '{"type": "task_complete"}',
        ):
            yield line

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider()

    result = await provider.run(
        "say hi",
        cwd="/tmp/work",
        model="gpt-5",
        provider_options={"sandbox": "workspace-write"},
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "exec" in argv and "--json" in argv
    assert "--skip-git-repo-check" in argv
    assert "--model" in argv and "gpt-5" in argv
    assert "--cd" in argv and "/tmp/work" in argv
    assert "--sandbox" in argv and "workspace-write" in argv
    assert argv[-1] == "say hi"

    assert result.provider == "codex"
    assert result.transport == "codex-cli-jsonl"
    assert result.text == "hello\nthere"
    assert any(e.kind is EventKind.DONE for e in result.events)


async def test_stream_synthesizes_done_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "agent_message", "text": "no done marker here"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider()

    events = []
    async for ev in provider.stream("hi"):
        events.append(ev)
    assert events[-1].kind is EventKind.DONE


async def test_provider_options_pass_through_unknown_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider()
    await provider.run(
        "p",
        provider_options={"full_auto": True, "ask_for_approval": "never", "custom_key": "v"},
    )
    argv = captured["argv"]
    assert isinstance(argv, list)
    joined = " ".join(argv)
    assert "--full-auto" in argv
    assert "--ask-for-approval" in argv and "never" in argv
    assert "custom_key=v" in joined


def test_codex_binary_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTHPY_CODEX_BINARY", "/custom/codex-bin")
    provider = codex_mod.CodexProvider()
    assert provider._binary == "/custom/codex-bin"
