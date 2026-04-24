"""Smoke tests for the debugging CLI."""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator

import pytest

from oauthpy._subprocess import CompletedProcess
from oauthpy.cli import main
from oauthpy.providers import codex as codex_mod


def test_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower() or "oauthpy" in out.lower()


def test_auth_status_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: None)
    rc = main(["auth", "status", "--provider", "codex", "--json"])
    assert rc == 1  # not authenticated
    out = capsys.readouterr().out
    assert '"provider": "codex"' in out
    assert '"installed": false' in out


def test_available_no(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: None)
    rc = main(["available", "--provider", "codex"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == "no"


def test_run_emits_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "agent_message", "text": "cli out"}'
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    rc = main(["run", "--provider", "codex", "hello"])
    assert rc == 0
    assert "cli out" in capsys.readouterr().out


def test_run_accepts_source_external(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "agent_message", "text": "external out"}'
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    rc = main(["run", "--provider", "codex", "--source", "external", "hello"])
    assert rc == 0
    assert "external out" in capsys.readouterr().out


def test_experimental_chat_reconstructs_transcript(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: "/usr/local/bin/codex")
    prompts: list[str] = []

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        prompts.append(argv[-1])
        yield '{"type": "agent_message", "text": "assistant out"}'
        yield '{"type": "task_complete"}'

    inputs = iter(["first", "second", "/exit"])
    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(inputs))
    rc = main(["chat", "--provider", "codex", "--source", "external"])
    assert rc == 0
    assert prompts[0] == "first"
    assert "user: first" in prompts[1]
    assert "assistant: assistant out" in prompts[1]
    assert "user: second" in prompts[1]
    assert "assistant out" in capsys.readouterr().out
