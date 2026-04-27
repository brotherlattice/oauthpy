"""Tests for :class:`oauthpy.providers.gemini.GeminiProvider` with mocked subprocess."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from oauthpy import AuthRequiredError, EventKind, ProtocolError, ProviderNotInstalledError
from oauthpy._subprocess import CompletedProcess
from oauthpy.defaults import DEFAULT_GEMINI_MODEL
from oauthpy.providers import gemini as gemini_mod


@pytest.fixture(autouse=True)
def no_real_gemini_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Provider unit tests must install explicit fake Gemini binary/home state."""

    monkeypatch.setenv("HOME", str(tmp_path))

    def fail_unmocked_which(binary: str) -> str | None:
        raise AssertionError(f"test attempted real provider discovery for {binary!r}")

    monkeypatch.setattr(gemini_mod._subprocess, "which", fail_unmocked_which)


async def test_auth_status_no_binary(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: None)
    provider = gemini_mod.GeminiProvider()
    status = await provider.auth_status()
    assert status.provider == "gemini"
    assert status.installed is False
    assert status.authenticated is False
    assert status.details["reason"] == "binary_missing"


async def test_auth_status_env_api_key_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    provider = gemini_mod.GeminiProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is True
    assert status.mode == "api-key"
    assert status.details["env_auth"] == "GEMINI_API_KEY"
    assert "secret-key" not in str(status.details)


async def test_auth_status_gca_env_mode(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    monkeypatch.setenv("GOOGLE_GENAI_USE_GCA", "true")
    provider = gemini_mod.GeminiProvider(auth_source="external")
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "cloud"
    assert status.details["env_auth"] == "GOOGLE_GENAI_USE_GCA"


async def test_auth_status_oauthpy_source_is_unsupported(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: Path
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    provider = gemini_mod.GeminiProvider(auth_source="oauthpy", oauthpy_home=tmp_path)
    status = await provider.auth_status()
    assert status.installed is True
    assert status.authenticated is False
    assert status.mode == "unknown"
    assert status.details["source"] == "oauthpy"
    assert status.details["reason"] == "isolated_auth_unsupported"
    assert status.details["provider_home"] == str(tmp_path / "gemini")


async def test_auth_status_settings_login_state_is_best_effort(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: Path
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    (gemini_dir / "settings.json").write_text(
        '{"security": {"auth": {"selectedType": "oauth-personal"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_mod, "_gemini_config_dir", lambda: gemini_dir)
    provider = gemini_mod.GeminiProvider(auth_source="auto")
    status = await provider.auth_status()
    assert status.authenticated is True
    assert status.mode == "login-state"
    assert status.details["auth_method"] == "oauth-personal"
    assert status.details["auth_verified"] is False


async def test_login_missing_binary_raises(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: None)
    provider = gemini_mod.GeminiProvider(auth_source="external")
    with pytest.raises(ProviderNotInstalledError):
        await provider.login()


async def test_login_uses_external_interactive_cli(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    captured: dict[str, object] = {}

    async def fake_run_interactive(argv: list[str], **kwargs: object) -> CompletedProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcess(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gemini_mod._subprocess, "run_interactive", fake_run_interactive)
    provider = gemini_mod.GeminiProvider(auth_source="auto")
    await provider.login()
    assert captured["argv"] == ["/usr/bin/gemini"]
    assert captured["kwargs"] == {"env": None, "timeout": None}


async def test_run_streams_and_aggregates(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **kwargs: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        yield '{"type": "init", "session_id": "s1", "model": "gemini-2.5-flash"}'
        yield '{"type": "message", "message": {"role": "assistant", "content": "hello"}}'
        yield '{"type": "tool_use", "name": "read_file"}'
        yield (
            '{"type": "result", "response": "hello final", '
            '"stats": {"models": {"gemini-2.5-flash": '
            '{"tokens": {"prompt": 2, "candidates": 3, "total": 5}}}}}'
        )

    monkeypatch.setattr(gemini_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = gemini_mod.GeminiProvider(auth_source="external")
    result = await provider.run(
        "say hi",
        cwd="/tmp/work",
        model="gemini-2.5-flash",
        provider_options={"all_files": True},
    )

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[:5] == ["gemini", "--prompt", "say hi", "--output-format", "stream-json"]
    assert "--model" in argv and "gemini-2.5-flash" in argv
    assert "--all-files" in argv
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == "/tmp/work"
    assert kwargs["env"] is None
    assert result.provider == "gemini"
    assert result.transport == "gemini-cli-jsonl"
    assert result.text == "hello final"
    assert any(event.kind is EventKind.TOOL for event in result.events)
    assert result.usage is not None
    assert result.usage.input_tokens == 2
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 5


async def test_run_defaults_to_auto_model(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    captured: dict[str, object] = {}

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        captured["argv"] = argv
        yield '{"type": "result", "response": "ok"}'

    monkeypatch.setattr(gemini_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = gemini_mod.GeminiProvider(auth_source="external")
    await provider.run("p")
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--model" in argv
    assert DEFAULT_GEMINI_MODEL in argv


async def test_json_output_object_is_supported(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        assert "--output-format" in argv and "json" in argv
        yield '{"response": "single json", "stats": {"tokens": {"prompt": 1, "candidates": 2}}}'

    monkeypatch.setattr(gemini_mod._subprocess, "stream_lines", fake_stream_lines)
    provider = gemini_mod.GeminiProvider(auth_source="external")
    result = await provider.run("p", provider_options={"output_format": "json"})
    assert result.text == "single json"
    assert result.usage is not None
    assert result.usage.total_tokens == 3


async def test_oauthpy_run_source_raises(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    provider = gemini_mod.GeminiProvider(auth_source="oauthpy")
    with pytest.raises(AuthRequiredError):
        await provider.run("hello")


async def test_reasoning_effort_is_rejected(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(gemini_mod._subprocess, "which", lambda _binary: "/usr/bin/gemini")
    provider = gemini_mod.GeminiProvider(auth_source="external")
    with pytest.raises(ProtocolError):
        await provider.run("hello", provider_options={"reasoning_effort": "low"})
