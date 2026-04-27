"""One-shot prompt to Gemini via the official `gemini` CLI.

Prerequisite:
    1. `npm install -g @google/gemini-cli`
    2. `gemini` or `oauthpy auth login --provider gemini`
"""

from __future__ import annotations

from oauthpy import Client


def main() -> None:
    client = Client("gemini")
    if not client.available():
        status = client.auth_status()
        print(
            f"gemini not ready: installed={status.installed} "
            f"authenticated={status.authenticated} mode={status.mode}"
        )
        print("Run `oauthpy auth login --provider gemini` or configure Gemini CLI env auth.")
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
