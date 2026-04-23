"""Stream events from Claude via `claude-agent-sdk`.

Shows the event kinds as they come off the SDK's async iterator.
"""

from __future__ import annotations

import asyncio

from oauthpy import Client


async def main() -> None:
    async for event in Client("claude").stream(
        "List the top-level files in this repo.",
        cwd=".",
        provider_options={"allowed_tools": ["Read", "Glob", "Bash"]},
    ):
        prefix = f"[{event.kind.value}]"
        text = event.text or ""
        print(f"{prefix} {text}" if text else prefix)


if __name__ == "__main__":
    asyncio.run(main())
