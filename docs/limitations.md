# Limitations and compliance

## Scope

`oauthpy` v0.1 is a **local, single-user** Python library. It is designed to run on your machine, as you, using the OAuth login your official vendor tool already configured.

### In scope

- One-shot execution via `Client.run`.
- Streaming via `Client.stream` / `Client.stream_sync`.
- Best-effort, read-only `Client.auth_status`.
- `Client.login` that invokes the vendor's official login flow.
- A tiny debugging CLI.

### Explicitly out of scope for v0.1

- **Hosting or relaying other people's OAuth.** `oauthpy` is not a credential broker, a multi-user gateway, a proxy, or a hosted agent API.
- **Reverse-engineering vendor web endpoints.** We drive OAuth-authenticated local clients only.
- **Scraping TUI output.** The Codex TUI is not a supported integration surface.
- **Wire-compatibility with vendor cloud APIs.** `oauthpy` exposes its own small explicit Python API rather than pretending to be a clone of OpenAI's Responses API or Anthropic's Messages API.
- **Editing credential files.** `oauthpy` never writes `~/.codex/auth.json` or any Claude credential file. We do not implement our own PKCE dance.
- **HTTP proxy/server.** The library is the product. A future proxy could wrap it; v0.1 does not include one.
- **Full conversational session management.** Multi-turn resume is only supported to the extent the underlying provider trivially allows it (e.g. Claude's `resume` option via `provider_options`).

## Compliance notes

### Anthropic (Claude)

The official `claude-agent-sdk` documentation states:

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead.

`oauthpy` is not offering Claude.ai login to other people. It is a user's local wrapper around their own existing Claude auth — whatever the user's own `claude-agent-sdk` / Claude Code install already has configured. The SDK's authentication is entirely owned by the SDK; `oauthpy` never touches token files.

If you intend to redistribute Claude access through `oauthpy` — e.g. build a hosted product, a team gateway, or a shared agent — you must switch to API-key auth and consult Anthropic's terms. Do not build a service that hands out Claude.ai sessions on someone else's behalf.

### OpenAI (Codex)

OpenAI explicitly supports ChatGPT OAuth in third-party tools (the Codex CLI is designed for this). `oauthpy` runs the official `codex` CLI locally with your login. See [Codex authentication](https://developers.openai.com/codex/auth) for the authoritative documentation.

Do not use `oauthpy` to proxy ChatGPT Plus/Pro capacity to other users — that is not what the OAuth grant covers.

### Security posture

- `shell=False` everywhere. All subprocess calls pass argv lists.
- Best-effort secret redaction in logs, `repr`, and exception messages via `oauthpy._redact`.
- `oauthpy` never prints OAuth tokens.
- `oauthpy` never persists OAuth tokens beyond what upstream tools already manage.
- No hidden global state: no module-level singletons, no env mutation outside the caller's explicit `env` argument.

The redactor is heuristic — it catches common secret shapes (`sk-*`, `sk-ant-*`, `ghp_*`, `Bearer …`, JWTs, long hex blobs). It is not a replacement for careful logging, and it is not a security boundary. Do not print arbitrary user data and trust the redactor to catch everything.

### Reporting issues

If you find a credential-exposure bug, a sandbox escape, or anything else that looks like it could hurt a user, please open an issue marked `security` rather than a PR.
