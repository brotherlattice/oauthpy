from __future__ import annotations

from oauthpy import AuthStatus, Event, EventKind, RunResult, Usage


def test_event_kind_values_are_stable_strings() -> None:
    assert EventKind.MESSAGE.value == "message"
    assert EventKind.REASONING.value == "reasoning"
    assert EventKind.PLAN.value == "plan"
    assert EventKind.TOOL.value == "tool"
    assert EventKind.COMMAND.value == "command"
    assert EventKind.FILE_CHANGE.value == "file_change"
    assert EventKind.ERROR.value == "error"
    assert EventKind.DONE.value == "done"


def test_event_repr_redacts_token() -> None:
    ev = Event(kind=EventKind.MESSAGE, text="key=sk-ant-abcdefghijklmnop_secret_123 ok")
    assert "sk-ant-abcdefghijklmnop" not in repr(ev)
    assert "REDACTED" in repr(ev)


def test_runresult_repr_is_terse() -> None:
    rr = RunResult(
        provider="codex",
        transport="codex-cli-jsonl",
        model="gpt-5",
        text="hello",
        events=(Event(kind=EventKind.DONE),),
        elapsed_s=0.123,
        cwd="/tmp/x",
    )
    r = repr(rr)
    assert "provider='codex'" in r
    assert "text_len=5" in r
    assert "events=1" in r


def test_auth_status_construction() -> None:
    s = AuthStatus(
        provider="claude",
        installed=True,
        authenticated=False,
        mode="unknown",
        details={"binary": "/usr/local/bin/claude"},
    )
    assert s.provider == "claude"
    assert s.installed is True
    assert s.authenticated is False
    assert s.mode == "unknown"
    assert s.details == {"binary": "/usr/local/bin/claude"}


def test_usage_defaults_to_none() -> None:
    u = Usage()
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.total_tokens is None
    assert u.cost_usd is None
