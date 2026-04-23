"""One-shot prompt to Claude via `claude-agent-sdk`.

Prerequisite:
    1. `pip install claude-agent-sdk` (or `pip install oauthpy[claude]`)
    2. Configure auth — one of:
       * `export CLAUDE_CODE_OAUTH_TOKEN=...`
       * `export ANTHROPIC_API_KEY=...`
       * log in via Claude Code so `~/.claude.json` exists
"""

from __future__ import annotations

from oauthpy import Client


def main() -> None:
    client = Client("claude")
    if not client.available():
        status = client.auth_status()
        print(
            f"claude not ready: installed={status.installed} "
            f"authenticated={status.authenticated} mode={status.mode}"
        )
        return

    result = client.run(
        "Write a failing test for a hypothetical `add(a, b)` function.",
        cwd=".",
        provider_options={"allowed_tools": ["Read", "Glob", "Grep"]},
    )
    print(result.text)
    print(f"\n[{len(result.events)} events, {result.elapsed_s:.1f}s]")


if __name__ == "__main__":
    main()
