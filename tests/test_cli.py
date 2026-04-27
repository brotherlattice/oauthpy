"""Smoke tests for the debugging CLI."""

from __future__ import annotations

import builtins
import sys
import types
from collections.abc import AsyncIterator

import pytest

from oauthpy._subprocess import CompletedProcess
from oauthpy.cli import (
    _completion_matches,
    _handle_interactive_line,
    _interactive_banner,
    _InteractiveState,
    _prompt_toolkit_input,
    main,
)
from oauthpy.providers import claude as claude_mod
from oauthpy.providers import codex as codex_mod


@pytest.fixture(autouse=True)
def no_real_provider_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI tests must explicitly fake provider discovery/auth state."""

    def fail_unmocked_which(binary: str) -> str | None:
        raise AssertionError(f"test attempted real provider discovery for {binary!r}")

    monkeypatch.setattr(codex_mod._subprocess, "which", fail_unmocked_which)
    monkeypatch.setattr(claude_mod._subprocess, "which", fail_unmocked_which)


def test_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower() or "oauthpy" in out.lower()


def test_interactive_slash_completion_candidates() -> None:
    command_matches = _completion_matches("/h")
    assert ("/help", -2) in command_matches
    assert ("/help", -1) in _completion_matches("/")
    assert _completion_matches("hello") == []
    assert ("codex", -1) in _completion_matches("/provider c")
    assert ("claude", 0) in _completion_matches("/provider ")
    assert ("high", -1) in _completion_matches("/effort h")
    assert ("sonnet", -1) in _completion_matches("/model s")


def test_interactive_enter_accepts_unique_command_prefix() -> None:
    state = _InteractiveState(provider="codex", source="external")
    assert _handle_interactive_line(state, "/ex") is True


def test_interactive_enter_rejects_ambiguous_command_prefix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _InteractiveState(provider="codex", source="external")
    assert _handle_interactive_line(state, "/e") is False
    assert "ambiguous command: /e" in capsys.readouterr().err


def test_interactive_banner_mentions_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oauthpy.cli._completion_backend", lambda: "prompt_toolkit")
    assert "Press Tab to complete /commands" in _interactive_banner()
    monkeypatch.setattr("oauthpy.cli._completion_backend", lambda: "none")
    assert "Tab completion unavailable" in _interactive_banner()


def test_prompt_toolkit_backend_imports_complete_style_from_shortcuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    package = types.ModuleType("prompt_toolkit")
    package.__path__ = []  # type: ignore[attr-defined]

    def fake_prompt(message: str, **kwargs: object) -> str:
        calls["message"] = message
        calls["kwargs"] = kwargs
        return "/exit"

    package.prompt = fake_prompt  # type: ignore[attr-defined]

    completion = types.ModuleType("prompt_toolkit.completion")

    class Completer:
        pass

    class Completion:
        def __init__(self, text: str, *, start_position: int) -> None:
            self.text = text
            self.start_position = start_position

    completion.Completer = Completer  # type: ignore[attr-defined]
    completion.Completion = Completion  # type: ignore[attr-defined]

    shortcuts = types.ModuleType("prompt_toolkit.shortcuts")

    class CompleteStyle:
        MULTI_COLUMN = "multi-column"

    shortcuts.CompleteStyle = CompleteStyle  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "prompt_toolkit", package)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.shortcuts", shortcuts)

    read = _prompt_toolkit_input()
    assert read is not None
    assert read("oauthpy> ") == "/exit"
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["complete_style"] == "multi-column"
    assert kwargs["complete_while_typing"] is True


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


def test_claude_auth_status_json_when_sdk_and_cli_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(claude_mod, "_sdk", lambda: None)
    monkeypatch.setattr(claude_mod._subprocess, "which", lambda _b: None)
    rc = main(["auth", "status", "--provider", "claude", "--source", "external", "--json"])
    assert rc == 1
    out = capsys.readouterr().out
    assert '"provider": "claude"' in out
    assert '"installed": false' in out
    assert '"reason": "cli_missing"' in out


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
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "agent_message", "text": "external out"}'
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    rc = main(
        [
            "run",
            "--provider",
            "codex",
            "--source",
            "external",
            "--reasoning-effort",
            "high",
            "hello",
        ]
    )
    assert rc == 0
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "model_reasoning_effort=high" in argv
    assert "external out" in capsys.readouterr().out


def test_interactive_repl_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: "/usr/local/bin/codex")
    prompts: list[str] = []
    argv_seen: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []
    login_calls: list[list[str]] = []

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    async def fake_run_interactive(argv: list[str], **_: object) -> CompletedProcess:
        login_calls.append(argv)
        return CompletedProcess(returncode=0, stdout="", stderr="")

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        prompts.append(argv[-1])
        argv_seen.append(argv)
        kwargs_seen.append(kwargs)
        yield '{"type": "reasoning", "text": "thinking"}'
        yield '{"type": "agent_message", "text": "assistant out"}'
        yield '{"type": "task_complete"}'

    inputs = iter(
        [
            "/status",
            "/available",
            "/login external",
            "/cwd /tmp/work",
            "/model gpt-test",
            "/models",
            "/efforts",
            "/effort high",
            "/timeout 12.5",
            "/events on",
            "/run one shot",
            "/stream streamed",
            "first",
            "/clear",
            "/source external",
            "/provider claude",
            "/help",
            "/exit",
        ]
    )

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(codex_mod._subprocess, "run_interactive", fake_run_interactive)
    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(inputs))

    rc = main(["interactive", "--provider", "codex", "--source", "external"])
    assert rc == 0
    assert prompts[0] == "one shot"
    assert prompts[1] == "streamed"
    assert prompts[2] == "first"
    assert login_calls == [["codex", "login"]]
    assert all(kwargs["cwd"] == "/tmp/work" for kwargs in kwargs_seen)
    assert all("model_reasoning_effort=high" in argv for argv in argv_seen[:3])

    out = capsys.readouterr()
    assert "codex> assistant out" in out.out
    assert "[message] assistant out" in out.out
    assert "provider=codex installed=True authenticated=True mode=oauth" in out.err
    assert "yes" in out.err
    assert "login completed provider=codex source=external" in out.err
    assert "cwd=/tmp/work" in out.err
    assert "model=gpt-test" in out.err
    assert "Codex model examples:" in out.err
    assert "Codex reasoning efforts:" in out.err
    assert "reasoning_effort=high" in out.err
    assert "timeout=12.5" in out.err
    assert "events=on" in out.err
    assert "[reasoning] thinking" in out.err
    assert "transcript cleared" in out.err
    assert "source set to external; transcript cleared" in out.err
    assert "provider set to claude; transcript cleared" in out.err
    assert "Commands:" in out.err


def test_interactive_eof_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)
    rc = main(["interactive", "--provider", "codex"])
    assert rc == 0


def test_chat_alias_reconstructs_transcript(
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
