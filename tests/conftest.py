from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def codex_fixtures_dir() -> Path:
    return FIXTURES / "codex_jsonl"


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove auth-related env vars so tests get a predictable baseline."""

    for key in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OAUTHPY_CODEX_BINARY",
        "CLAUDE_CONFIG_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
