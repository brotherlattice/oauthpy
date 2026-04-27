"""Tests for :class:`oauthpy.providers.codex.CodexProvider` with mocked subprocess."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from oauthpy import (
    AuthRequiredError,
    CommandExecutionError,
    EventKind,
    ProtocolError,
    ProviderNotInstalledError,
    TimeoutExceededError,
)
from oauthpy._subprocess import CompletedProcess
from oauthpy.defaults import DEFAULT_CODEX_REASONING_EFFORT
from oauthpy.providers import codex as codex_mod


@pytest.fixture(autouse=True)
def no_real_codex_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider unit tests must install an explicit fake Codex binary state."""

    def fail_unmocked_which(binary: str) -> str | None:
        raise AssertionError(f"test attempted real provider discovery for {binary!r}")

    monkeypatch.setattr(codex_mod._subprocess, "which", fail_unmocked_which)


async def test_auth_status_no_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    status = await provider.auth_status()
    assert status.installed is False
    assert status.authenticated is False
    assert status.mode == "unknown"
    assert status.details["reason"] == "binary_missing"


async def test_auth_status_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is True
    assert status.mode == "oauth"
    assert status.details["reason"] == "authenticated"


async def test_auth_status_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is False
    assert status.details["reason"] == "not_authenticated"


async def test_auto_auth_status_reports_no_source_when_logged_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=1, stdout="", stderr="not logged in")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider(auth_source="auto", oauthpy_home=tmp_path)
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is False
    assert status.details["source"] == "none"
    assert status.details["reason"] == "not_authenticated"
    assert status.details["oauthpy_checked"] is False


async def test_available_false_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    assert await provider.available() is False


async def test_run_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    with pytest.raises(ProviderNotInstalledError):
        await provider.run("hello")


async def test_login_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: None)
    provider = codex_mod.CodexProvider()
    with pytest.raises(ProviderNotInstalledError):
        await provider.login()


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
    provider = codex_mod.CodexProvider(auth_source="external")

    result = await provider.run(
        "say hi",
        cwd="/tmp/work",
        model="gpt-5",
        provider_options={"sandbox": "workspace-write"},
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "exec" in argv and "--json" in argv
    assert "--skip-git-repo-check" not in argv
    assert "--model" in argv and "gpt-5" in argv
    assert "--cd" in argv and "/tmp/work" in argv
    assert "--sandbox" in argv and "workspace-write" in argv
    assert "--config" in argv
    assert f"model_reasoning_effort={DEFAULT_CODEX_REASONING_EFFORT}" in argv
    assert argv[-1] == "say hi"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] is None

    assert result.provider == "codex"
    assert result.transport == "codex-cli-jsonl"
    assert result.text == "hello\nthere"
    assert any(e.kind is EventKind.DONE for e in result.events)


async def test_run_extracts_usage_from_turn_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}'
        yield (
            '{"type": "turn.completed", '
            '"usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}'
        )

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    result = await provider.run("p")
    assert result.usage is not None
    assert result.usage.input_tokens == 2
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 5


async def test_stream_synthesizes_done_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "agent_message", "text": "no done marker here"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")

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
    provider = codex_mod.CodexProvider(auth_source="external")
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


async def test_skip_git_repo_check_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    await provider.run("p", provider_options={"skip_git_repo_check": True})
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--skip-git-repo-check" in argv


async def test_codex_reasoning_effort_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    await provider.run("p", provider_options={"reasoning_effort": "high"})
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "model_reasoning_effort=high" in argv
    assert f"model_reasoning_effort={DEFAULT_CODEX_REASONING_EFFORT}" not in argv


async def test_codex_config_mapping_overrides_default_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    await provider.run("p", provider_options={"config": {"model_reasoning_effort": "xhigh"}})
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "model_reasoning_effort=xhigh" in argv
    assert f"model_reasoning_effort={DEFAULT_CODEX_REASONING_EFFORT}" not in argv


async def test_oauthpy_source_applies_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        captured["kwargs"] = kwargs
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="oauthpy", oauthpy_home=tmp_path)
    await provider.run("p")
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["CODEX_HOME"] == str(tmp_path / "codex")
    assert env["OPENAI_API_KEY"] is None


async def test_login_defaults_to_oauthpy_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_run_interactive(argv: list[str], **kwargs: object) -> CompletedProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_mod._subprocess, "run_interactive", fake_run_interactive)
    provider = codex_mod.CodexProvider(auth_source="auto", oauthpy_home=tmp_path)
    await provider.login()
    assert captured["argv"] == ["codex", "login"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["CODEX_HOME"] == str(tmp_path / "codex")


def test_codex_config_store_setup_preserves_existing_config(tmp_path: Path) -> None:
    provider = codex_mod.CodexProvider(auth_source="oauthpy", oauthpy_home=tmp_path)
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True)
    config = codex_home / "config.toml"
    config.write_text('[sandbox]\nmode = "workspace-write"\n', encoding="utf-8")
    provider._ensure_codex_config()
    text = config.read_text(encoding="utf-8")
    assert 'cli_auth_credentials_store = "file"' in text
    assert '[sandbox]\nmode = "workspace-write"\n' in text


async def test_auto_prefers_authenticated_oauthpy_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    seen_envs: list[object] = []

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        seen_envs.append(kwargs.get("env"))
        env = kwargs.get("env")
        if isinstance(env, dict) and env.get("CODEX_HOME") == str(codex_home):
            return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")
        return CompletedProcess(returncode=1, stdout="", stderr="Not logged in")

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        seen_envs.append(kwargs.get("env"))
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="auto", oauthpy_home=tmp_path)
    await provider.run("p")
    assert any(
        isinstance(env, dict) and env.get("CODEX_HOME") == str(codex_home) for env in seen_envs
    )


async def test_auto_run_falls_back_to_authenticated_external_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    status_envs: list[object] = []
    stream_envs: list[object] = []

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        status_envs.append(kwargs.get("env"))
        env = kwargs.get("env")
        if isinstance(env, dict) and env.get("CODEX_HOME") == str(codex_home):
            return CompletedProcess(returncode=1, stdout="", stderr="Not logged in")
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        stream_envs.append(kwargs.get("env"))
        yield '{"type": "agent_message", "text": "external ok"}'
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="auto", oauthpy_home=tmp_path)
    result = await provider.run("p")

    assert result.text == "external ok"
    assert any(
        isinstance(env, dict) and env.get("CODEX_HOME") == str(codex_home) for env in status_envs
    )
    assert None in status_envs
    assert stream_envs == [None]


async def test_auto_falls_back_when_oauthpy_status_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    codex_home = tmp_path / "codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')

    async def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess:
        env = kwargs.get("env")
        if isinstance(env, dict) and env.get("CODEX_HOME") == str(codex_home):
            raise codex_mod.TimeoutExceededError("status timed out")
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)
    provider = codex_mod.CodexProvider(auth_source="auto", oauthpy_home=tmp_path)
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.details["source"] == "external"


def test_oauthpy_home_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OAUTHPY_HOME", str(tmp_path))
    provider = codex_mod.CodexProvider(auth_source="oauthpy")
    assert provider._codex_home == tmp_path / "codex"


def test_codex_binary_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAUTHPY_CODEX_BINARY", "/custom/codex-bin")
    provider = codex_mod.CodexProvider()
    assert provider._binary == "/custom/codex-bin"


async def test_codex_exec_error_includes_resolved_source_and_redacted_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        if False:
            yield ""
        raise CommandExecutionError(
            "'codex' exited with code 1",
            returncode=1,
            stderr="Windows launcher failed with sk-abcdefghijklmnopqrstuvwxyz1234",
        )

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")

    with pytest.raises(ProtocolError) as excinfo:
        await provider.run("p")

    message = str(excinfo.value)
    assert "auth_source=external" in message
    assert "Windows launcher failed" in message
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in message
    assert "***REDACTED***" in message


async def test_codex_timeout_retries_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    calls = 0

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutExceededError("codex stream timed out")
        yield '{"type": "agent_message", "text": "retry ok"}'
        yield '{"type": "task_complete"}'

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    result = await provider.run(
        "p",
        provider_options={
            "max_retries": 1,
            "retry_on_timeout": True,
            "retry_backoff_s": 0,
            "retry_jitter_s": 0,
        },
    )

    assert calls == 2
    assert result.text == "retry ok"
    assert isinstance(result.raw, dict)
    assert result.raw["retry"]["retry_count"] == 1


async def test_codex_timeout_does_not_retry_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    calls = 0

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        raise TimeoutExceededError("codex stream timed out")
        yield ""  # pragma: no cover

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    with pytest.raises(TimeoutExceededError):
        await provider.run("p", provider_options={"max_retries": 2})
    assert calls == 1


async def test_codex_auth_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _binary: "/usr/local/bin/codex")
    calls = 0

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        raise CommandExecutionError("codex exited", returncode=1, stderr="not logged in")
        yield ""  # pragma: no cover

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = codex_mod.CodexProvider(auth_source="external")
    with pytest.raises(AuthRequiredError):
        await provider.run(
            "p",
            provider_options={"max_retries": 2, "retry_backoff_s": 0, "retry_jitter_s": 0},
        )
    assert calls == 1
