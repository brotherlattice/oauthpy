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
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "OAUTHPY_CODEX_BINARY",
        "OAUTHPY_CLAUDE_BINARY",
        "OAUTHPY_HOME",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
