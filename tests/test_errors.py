from __future__ import annotations

import pytest

from oauthpy import (
    AuthRequiredError,
    CommandExecutionError,
    OauthPyError,
    ProtocolError,
    ProviderNotInstalledError,
    TimeoutExceededError,
    UnsupportedProviderError,
)


@pytest.mark.parametrize(
    "cls",
    [
        UnsupportedProviderError,
        ProviderNotInstalledError,
        AuthRequiredError,
        ProtocolError,
        CommandExecutionError,
        TimeoutExceededError,
    ],
)
def test_all_errors_inherit_from_oauthpyerror(cls: type) -> None:
    assert issubclass(cls, OauthPyError)


def test_error_message_is_redacted() -> None:
    exc = OauthPyError("auth header: Bearer sk-ant-abcdefghijklmnop123")
    assert "sk-ant-abcdefghijklmnop" not in str(exc)
    assert "REDACTED" in str(exc)


def test_command_execution_error_carries_returncode_and_stderr() -> None:
    exc = CommandExecutionError(
        "codex exec failed",
        returncode=2,
        stderr="key=sk-abcdefghijklmnopqrstuvwxyz1234",
    )
    assert exc.returncode == 2
    assert "sk-abcdefghijklmnop" not in (exc.stderr or "")


def test_command_execution_error_redacts_details() -> None:
    exc = CommandExecutionError(
        "provider failed",
        details={"attempts": [{"stderr": "Bearer abc.def.ghi sk-ant-abcdefghijklmnop123"}]},
    )
    assert "sk-ant-abcdefghijklmnop" not in str(exc.details)
    assert "Bearer abc.def.ghi" not in str(exc.details)
    assert "REDACTED" in str(exc.details)


def test_error_raises_cleanly() -> None:
    with pytest.raises(OauthPyError) as excinfo:
        raise AuthRequiredError("please log in")
    assert "please log in" in str(excinfo.value)
