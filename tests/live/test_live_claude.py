"""Live smoke test — requires a real Claude auth.

Skipped unless ``OAUTHPY_LIVE_CLAUDE=1`` AND ``claude-agent-sdk`` is importable
AND some form of auth is configured (``CLAUDE_CODE_OAUTH_TOKEN``,
``ANTHROPIC_API_KEY``, or ``~/.claude.json``). Run:

.. code-block:: bash

    OAUTHPY_LIVE_CLAUDE=1 pytest -m live_claude
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from oauthpy import Client, EventKind


def _sdk_available() -> bool:
    return importlib.util.find_spec("claude_agent_sdk") is not None


pytestmark = [
    pytest.mark.live_claude,
    pytest.mark.skipif(
        os.environ.get("OAUTHPY_LIVE_CLAUDE") != "1" or not _sdk_available(),
        reason="OAUTHPY_LIVE_CLAUDE=1 not set or claude-agent-sdk missing",
    ),
]


async def test_live_auth_status() -> None:
    client = Client("claude")
    status = await client.auth_status()
    assert status.installed is True
    assert status.authenticated is True, "configure Claude auth first"


async def test_live_run_simple_prompt() -> None:
    client = Client("claude")
    result = await client.run(
        "Reply with just the word 'oauthpy-smoke' and nothing else.",
        cwd=".",
        timeout=120.0,
    )
    assert any(e.kind is EventKind.DONE for e in result.events)
