from __future__ import annotations

from oauthpy._redact import redact, redact_argv, redact_env, safe_repr


def test_openai_key_redacted() -> None:
    out = redact("key=sk-abcdefghijklmnop123 tail")
    assert "sk-abcdefghijklmnop" not in out
    assert "tail" in out


def test_anthropic_key_redacted() -> None:
    out = redact("ANTHROPIC_API_KEY=sk-ant-abc123def456ghij78")
    assert "sk-ant-abc123" not in out


def test_github_token_redacted() -> None:
    out = redact("token=ghp_abcdefghijklmnopqrstuvwxyz01234567")
    assert "ghp_abcdefghij" not in out


def test_bearer_token_redacted() -> None:
    out = redact("Authorization: Bearer some-token-abc-123")
    assert "some-token-abc-123" not in out


def test_jwt_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123xyz"
    out = redact(f"token={jwt} ok")
    assert jwt not in out


def test_none_returns_empty_string() -> None:
    assert redact(None) == ""


def test_redact_env_masks_sensitive_keys() -> None:
    env = {
        "FOO": "bar",
        "CLAUDE_CODE_OAUTH_TOKEN": "this-should-be-masked",
        "OPENAI_API_KEY": "sk-abcdefghijklmnop",
    }
    masked = redact_env(env)
    assert masked["FOO"] == "bar"
    assert masked["CLAUDE_CODE_OAUTH_TOKEN"] == "***REDACTED***"
    assert masked["OPENAI_API_KEY"] == "***REDACTED***"


def test_redact_argv_masks_embedded_secret() -> None:
    argv = ["codex", "exec", "--config", "api_key=sk-abcdefghijklmnop"]
    out = redact_argv(argv)
    assert "sk-abcdefghijklmnop" not in " ".join(out)


def test_safe_repr() -> None:
    obj = {"token": "sk-ant-abcdefghijklmnop"}
    assert "sk-ant-abcdefghij" not in safe_repr(obj)
