"""Live smoke test — requires a real `gemini` CLI that is authenticated.

Skipped unless the ``OAUTHPY_LIVE_GEMINI=1`` environment variable is set AND the
``gemini`` binary is on PATH. Run:

.. code-block:: bash

    OAUTHPY_LIVE_GEMINI=1 pytest -m live_gemini
"""

from __future__ import annotations

import os
import shutil

import pytest

from oauthpy import Client, EventKind

pytestmark = [
    pytest.mark.live_gemini,
    pytest.mark.skipif(
        os.environ.get("OAUTHPY_LIVE_GEMINI") != "1" or shutil.which("gemini") is None,
        reason="OAUTHPY_LIVE_GEMINI=1 not set or `gemini` not on PATH",
    ),
]


async def test_live_auth_status_installed() -> None:
    client = Client("gemini")
    status = await client.auth_status()
    assert status.installed is True


async def test_live_run_simple_prompt() -> None:
    client = Client("gemini")
    result = await client.run(
        "Reply with just the word 'oauthpy-smoke' and nothing else.",
        cwd=".",
        timeout=120.0,
    )
    assert any(e.kind is EventKind.DONE for e in result.events)
