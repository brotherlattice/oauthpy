"""Live smoke test — requires a real `codex` CLI that is logged in.

Skipped unless the ``OAUTHPY_LIVE_CODEX=1`` environment variable is set AND the
``codex`` binary is on PATH. Run:

.. code-block:: bash

    OAUTHPY_LIVE_CODEX=1 pytest -m live_codex
"""

from __future__ import annotations

import os
import shutil

import pytest

from oauthpy import Client, EventKind

pytestmark = [
    pytest.mark.live_codex,
    pytest.mark.skipif(
        os.environ.get("OAUTHPY_LIVE_CODEX") != "1" or shutil.which("codex") is None,
        reason="OAUTHPY_LIVE_CODEX=1 not set or `codex` not on PATH",
    ),
]


async def test_live_auth_status_is_authenticated() -> None:
    client = Client("codex")
    status = await client.auth_status()
    assert status.installed is True
    assert status.authenticated is True, "run `codex login` first"


async def test_live_run_simple_prompt() -> None:
    client = Client("codex")
    result = await client.run(
        "Say the word 'oauthpy-smoke-test' and nothing else.",
        cwd=".",
        timeout=90.0,
    )
    assert any(e.kind is EventKind.DONE for e in result.events)
    # We do not assert exact text — the model may embellish.
