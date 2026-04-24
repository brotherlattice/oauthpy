"""Tests that the sync facade wraps the async core correctly."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from oauthpy import Client, Event, EventKind, UnsupportedProviderError
from oauthpy._subprocess import CompletedProcess
from oauthpy.providers import codex as codex_mod


@pytest.fixture()
def mocked_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_mod._subprocess, "which", lambda _b: "/usr/local/bin/codex")

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        yield '{"type": "agent_message", "text": "sync hi"}'
        yield '{"type": "task_complete"}'

    async def fake_run(argv: list[str], **_: object) -> CompletedProcess:
        return CompletedProcess(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)
    monkeypatch.setattr(codex_mod._subprocess, "run", fake_run)


def test_unsupported_provider_rejected() -> None:
    with pytest.raises(UnsupportedProviderError):
        Client("grok")  # type: ignore[arg-type]


def test_run_works_from_sync_context(mocked_codex: None) -> None:
    client = Client("codex")
    result = client.run("hi")
    assert result.text == "sync hi"


def test_stream_sync_yields_events(mocked_codex: None) -> None:
    client = Client("codex")
    events: list[Event] = list(client.stream_sync("hi"))
    assert [e.kind for e in events] == [EventKind.MESSAGE, EventKind.DONE]


def test_stream_sync_close_cancels_background_stream(
    mocked_codex: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = threading.Event()

    async def fake_stream_lines(argv: list[str], **_: object) -> AsyncIterator[str]:
        try:
            yield '{"type": "agent_message", "text": "first"}'
            await asyncio.sleep(30)
        finally:
            closed.set()

    monkeypatch.setattr(codex_mod._subprocess, "stream_lines", fake_stream_lines)

    client = Client("codex")
    iterator = client.stream_sync("hi")
    assert next(iterator).text == "first"
    iterator.close()
    assert closed.wait(timeout=2.0)


def test_run_returns_coroutine_in_async_context(mocked_codex: None) -> None:
    async def _do() -> Any:
        client = Client("codex")
        coro = client.run("hi")
        assert asyncio.iscoroutine(coro)
        return await coro

    result = asyncio.run(_do())
    assert result.text == "sync hi"


def test_stream_is_async_iterator(mocked_codex: None) -> None:
    async def _do() -> list[EventKind]:
        client = Client("codex")
        kinds = []
        async for ev in client.stream("hi"):
            kinds.append(ev.kind)
        return kinds

    kinds = asyncio.run(_do())
    assert EventKind.DONE in kinds
