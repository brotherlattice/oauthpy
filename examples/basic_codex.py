"""One-shot prompt to Codex via `codex exec --json`.

Prerequisite:
    1. `npm i -g @openai/codex`
    2. `codex login` (or `oauthpy auth login --provider codex`)
"""

from __future__ import annotations

from oauthpy import Client


def main() -> None:
    client = Client("codex")
    if not client.available():
        status = client.auth_status()
        print(f"codex not ready: installed={status.installed} authenticated={status.authenticated}")
        print("Run `oauthpy auth login --provider codex` first.")
        return

    result = client.run(
        "Summarize this repository in one paragraph.",
        cwd=".",
        timeout=120,
    )
    print(result.text)
    print(f"\n[{len(result.events)} events, {result.elapsed_s:.1f}s]")


if __name__ == "__main__":
    main()
