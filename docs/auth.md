# Auth

`oauthpy` v0.1 does not manage OAuth tokens itself. It delegates to the upstream tool each provider ships, which already owns the interactive flow, the refresh loop, and the on-disk token file.

## Codex

- **Login**: run `codex login`. The CLI opens your browser, runs ChatGPT OAuth via PKCE, catches the callback on `127.0.0.1:1455`, and writes `~/.codex/auth.json`.
- **Check status**: run `codex login status`. Exit code `0` means authenticated.
- **Logout**: run `codex logout`. This removes the on-disk token.
- **Environment-only alternative**: if you have an OpenAI API key and prefer key auth, `codex login --with-api-key` is the official path. API-key auth is out of scope for the v0.1 oauthpy examples but is transparently supported — the CLI does not care how you authenticated.

`oauthpy.Client("codex").auth_status()` wraps `codex login status`. It never reads `auth.json` directly.

`oauthpy.Client("codex").login()` shells out to `codex login`.

## Claude

`claude-agent-sdk` accepts auth from three sources, in priority order:

1. `CLAUDE_CODE_OAUTH_TOKEN` environment variable — a long-lived OAuth token created by `claude setup-token`. Useful for CI and headless contexts.
2. `ANTHROPIC_API_KEY` environment variable — classic API-key auth.
3. `~/.claude.json` login state — what `claude` / Claude Code writes when you log in interactively.

`oauthpy.Client("claude").auth_status()` returns `mode=...` reflecting which of those it detected:

| `mode` | Source |
|--------|--------|
| `env` | `CLAUDE_CODE_OAUTH_TOKEN` is set |
| `api-key` | `ANTHROPIC_API_KEY` is set |
| `login-state` | `~/.claude.json` exists (contents never parsed) |
| `unknown` | None of the above |

`oauthpy.Client("claude").login()` shells out to `claude setup-token`.

## Why oauthpy does not refresh tokens itself

OpenClaw's OAuth concepts documentation [describes](https://docs.openclaw.ai/concepts/oauth) a layer that *does* manage refresh — with file locks, provenance tracking, and mirroring from upstream CLI credential stores. That is the right shape for a gateway/orchestrator that wants a single canonical credential store across many agents.

`oauthpy` v0.1 is deliberately a layer below that. Every call flows through the upstream tool (`codex` CLI / `claude-agent-sdk`), which means:

- The upstream tool's refresh logic is always in charge; `oauthpy` never writes tokens.
- If `codex` or `claude-agent-sdk` changes its auth format, `oauthpy` is unaffected.
- There is no "stale mirror" failure mode: the on-disk token read by Codex is the only one.
- A future oauthpy backend could add file-locked refresh or direct PKCE without breaking the public API — see `auth.py`.

## Required permissions on the host

- **Codex**: ability to run the `codex` binary, read/write its own state under `~/.codex/`, and open `http://127.0.0.1:1455` during login.
- **Claude**: ability to import `claude-agent-sdk`, read `~/.claude.json` if it exists, and see `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` from the environment.

No other network or filesystem capabilities are required by oauthpy itself.
