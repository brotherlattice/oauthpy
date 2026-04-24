# Auth

`oauthpy` v0.1 does not implement vendor OAuth flows, mint tokens, refresh tokens, or copy credential files. It delegates all auth behavior to the official local tools and SDKs, then controls which provider config directory they see.

## Auth Sources

`Client(provider, auth_source="auto", oauthpy_home=None)` supports three sources:

| Source | Meaning |
|--------|---------|
| `auto` | Prefer authenticated oauthpy-isolated state, then fall back to normal vendor CLI/session state. If login is needed, default to oauthpy-isolated state. |
| `oauthpy` | Force isolated provider state under `OAUTHPY_HOME` or `~/.oauthpy`. |
| `external` | Force normal vendor CLI/session behavior with no oauthpy config-directory override. |

`OAUTHPY_HOME` defaults to `~/.oauthpy`. oauthpy creates this directory and provider subdirectories with `0700` permissions where the operating system supports POSIX modes.

```
~/.oauthpy/
  codex/
    config.toml
    auth.json          # only if Codex uses file credential storage
  claude/
    ...                # owned by Claude Code / claude-agent-sdk
```

Do not commit, copy casually, paste, or share any file-based credential material under this tree. Treat provider credential files like passwords.

## CLI

```bash
oauthpy auth login --provider codex          # isolated oauthpy source
oauthpy auth login --provider claude         # isolated oauthpy source
oauthpy auth login --provider claude --source external

oauthpy auth status --provider codex --source auto
oauthpy auth status --provider claude --source oauthpy
oauthpy available --provider codex
```

`AuthStatus.details` intentionally includes only non-secret operational data such as `source`, `requested_source`, provider config path, binary path, and login-status exit code. It must not include token values.

## Codex

`oauthpy` uses the official `codex` CLI:

- Login: `codex login`
- Status: `codex login status`
- Runs: `codex exec --json`

For `auth_source="oauthpy"`, oauthpy sets:

```bash
CODEX_HOME=$OAUTHPY_HOME/codex
```

It also ensures `CODEX_HOME/config.toml` contains:

```toml
cli_auth_credentials_store = "file"
```

If that key already exists with a supported value (`file`, `keyring`, or `auto`), oauthpy preserves it and does not overwrite unrelated config. This is intentional: Codex owns the credential format and refresh behavior.

For `auth_source="external"`, oauthpy does not set `CODEX_HOME` and the normal Codex CLI config is used. For `auth_source="auto"`, oauthpy checks isolated state first if it appears to exist, then checks external status.

Codex may store cached credentials in `auth.json` under `CODEX_HOME` or in the OS credential store, depending on `cli_auth_credentials_store` and platform support. If file storage is used, the file contains sensitive tokens.

## Claude

`oauthpy` uses the official Claude Code CLI for auth status/login and `claude-agent-sdk` for runs:

- Login: `claude auth login`
- Status: `claude auth status --json`
- Runs: `claude_agent_sdk.query(..., options=ClaudeAgentOptions(...))`

For `auth_source="oauthpy"`, oauthpy sets:

```bash
CLAUDE_CONFIG_DIR=$OAUTHPY_HOME/claude
```

The same resolved environment is passed into `ClaudeAgentOptions(env=...)` for SDK runs. For `auth_source="external"`, oauthpy does not set `CLAUDE_CONFIG_DIR`.

For `auth_source="auto"`, oauthpy checks isolated Claude status first if isolated state appears to exist, then checks external `claude auth status --json`. If CLI status is unavailable, it falls back to documented environment-based auth indicators without exposing values:

- `CLAUDE_CODE_USE_BEDROCK`
- `CLAUDE_CODE_USE_VERTEX`
- `CLAUDE_CODE_USE_FOUNDRY`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_API_KEY`
- `CLAUDE_CODE_OAUTH_TOKEN`

`claude setup-token` is not normal interactive login. It is a separate CI/headless helper that prints a long-lived token; oauthpy does not call it for `Client("claude").login()`.

## OpenClaw Precedent

OpenClaw's OAuth/provider docs describe a broader operational layer for gateways and orchestrators: token provenance, locks, refresh state, and explicit provider credential management. oauthpy deliberately stays below that layer in v0.1:

- It is a local wrapper, not a hosted credential relay.
- It does not reverse-engineer OAuth endpoints.
- It does not copy external vendor tokens into `~/.oauthpy/` by default.
- It relies on official CLIs/SDKs to own auth and refresh behavior.

The auth-source layer leaves room for a future direct backend without changing the public API, but that backend is intentionally not implemented in v0.1.
