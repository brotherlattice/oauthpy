"""Stream events from Codex as they arrive.

Shows the event kinds coming off `codex exec --json` in real time.
"""

from __future__ import annotations

import asyncio

from oauthpy import Client, EventKind


async def main() -> None:
    async for event in Client("codex").stream(
        "List the top-level files in this repo.",
        cwd=".",
        timeout=120,
    ):
        if event.kind is EventKind.MESSAGE and event.text:
            print(f"[msg] {event.text}")
        elif event.kind is EventKind.COMMAND and event.text:
            print(f"[cmd] {event.text}")
        elif event.kind is EventKind.FILE_CHANGE and event.text:
            print(f"[edit] {event.text}")
        elif event.kind is EventKind.ERROR:
            print(f"[err] {event.text}")
        elif event.kind is EventKind.DONE:
            print("[done]")


if __name__ == "__main__":
    asyncio.run(main())
