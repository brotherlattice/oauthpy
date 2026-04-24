"""Golden-fixture tests for the Codex JSONL parser."""

from __future__ import annotations

from pathlib import Path

from oauthpy.models import EventKind
from oauthpy.providers.codex import classify_event, parse_jsonl


def _load(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_simple_message_golden(codex_fixtures_dir: Path) -> None:
    events = parse_jsonl(_load(codex_fixtures_dir / "simple_message.jsonl"))
    kinds = [e.kind for e in events]
    assert EventKind.MESSAGE in kinds
    assert EventKind.DONE in kinds
    text = next((e.text for e in events if e.kind is EventKind.MESSAGE and e.text), None)
    assert text == "Hello, world."


def test_with_tool_calls_golden(codex_fixtures_dir: Path) -> None:
    events = parse_jsonl(_load(codex_fixtures_dir / "with_tool_calls.jsonl"))
    kinds = [e.kind for e in events]
    assert EventKind.REASONING in kinds
    assert EventKind.PLAN in kinds
    assert EventKind.TOOL in kinds
    assert EventKind.COMMAND in kinds
    assert EventKind.FILE_CHANGE in kinds
    assert EventKind.MESSAGE in kinds
    assert EventKind.DONE in kinds
    # The last non-done message is the final assistant text
    final_msg = next(
        (e for e in reversed(events) if e.kind is EventKind.MESSAGE and e.text),
        None,
    )
    assert final_msg is not None
    assert final_msg.text == "I made the change."


def test_with_error_golden(codex_fixtures_dir: Path) -> None:
    events = parse_jsonl(_load(codex_fixtures_dir / "with_error.jsonl"))
    assert any(e.kind is EventKind.ERROR and e.text == "model unavailable" for e in events)


def test_windows_paths_golden(codex_fixtures_dir: Path) -> None:
    events = parse_jsonl(_load(codex_fixtures_dir / "windows_paths.jsonl"))
    file_change = next((e for e in events if e.kind is EventKind.FILE_CHANGE), None)
    assert file_change is not None
    assert file_change.text == r"C:\Users\alice\project\src\main.py"


def test_current_schema_golden(codex_fixtures_dir: Path) -> None:
    events = parse_jsonl(_load(codex_fixtures_dir / "current_schema.jsonl"))
    kinds = [event.kind for event in events]
    assert EventKind.MESSAGE in kinds
    assert EventKind.COMMAND in kinds
    assert EventKind.FILE_CHANGE in kinds
    assert kinds.count(EventKind.TOOL) == 2
    assert EventKind.PLAN in kinds
    assert EventKind.DONE in kinds
    assert EventKind.ERROR in kinds
    assert any(event.text == "Hello from current schema." for event in events)
    assert any(event.text == "pytest -q" for event in events)
    assert any(event.text == "src/oauthpy/example.py" for event in events)
    done = next(event for event in events if event.kind is EventKind.DONE)
    assert done.raw["usage"]["total_tokens"] == 15


def test_malformed_line_becomes_error_event() -> None:
    events = parse_jsonl(['{"type": "agent_message", not valid json'])
    assert len(events) == 1
    assert events[0].kind is EventKind.ERROR


def test_non_object_row_becomes_error_event() -> None:
    events = parse_jsonl(["[1, 2, 3]"])
    assert len(events) == 1
    assert events[0].kind is EventKind.ERROR


def test_blank_lines_are_skipped() -> None:
    events = parse_jsonl(["", "   ", '{"type": "task_complete"}'])
    assert len(events) == 1
    assert events[0].kind is EventKind.DONE


def test_classify_event_message_with_content_list() -> None:
    kind, text = classify_event(
        {"type": "agent_message", "content": [{"type": "text", "text": "hi"}]}
    )
    assert kind is EventKind.MESSAGE
    assert text == "hi"


def test_classify_event_unknown_type_with_text_falls_back_to_message() -> None:
    kind, text = classify_event({"type": "weird_new_event", "text": "some payload"})
    assert kind is EventKind.MESSAGE
    assert text == "some payload"


def test_classify_event_error_key_wins() -> None:
    kind, text = classify_event({"type": "whatever", "error": "boom"})
    assert kind is EventKind.ERROR


def test_event_raw_preserved() -> None:
    events = parse_jsonl(['{"type": "agent_message", "text": "hi", "extra": {"opaque": true}}'])
    assert events[0].raw == {"type": "agent_message", "text": "hi", "extra": {"opaque": True}}
