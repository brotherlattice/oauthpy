"""Stream events from Gemini CLI as they arrive."""

from __future__ import annotations

import asyncio

from oauthpy import Client, EventKind


async def main() -> None:
    async for event in Client("gemini").stream(
        "List the top-level files in this repo.",
        cwd=".",
        timeout=120,
    ):
        if event.kind is EventKind.MESSAGE and event.text:
            print(f"[msg] {event.text}")
        elif event.kind is EventKind.TOOL and event.text:
            print(f"[tool] {event.text}")
        elif event.kind is EventKind.ERROR:
            print(f"[err] {event.text}")
        elif event.kind is EventKind.DONE:
            print("[done]")


if __name__ == "__main__":
    asyncio.run(main())
