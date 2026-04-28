# Limitations and compliance

## Scope

`oauthpy` v0.1 is a **local, single-user** Python library. It is designed to run on your machine, as you, using official local CLIs/SDKs and either isolated `~/.oauthpy/` provider state or the normal vendor CLI/session state.

### In scope

- One-shot execution via `Client.run`.
- Streaming via `Client.stream` / `Client.stream_sync`.
- Best-effort, read-only `Client.auth_status`.
- `Client.login` that invokes the vendor's official login flow.
- A tiny debugging CLI.
- Isolated provider config directories via `auth_source="oauthpy"` where the upstream tool documents a safe override.
- External vendor session reuse via `auth_source="external"` or fallback from `auto`.

### Explicitly out of scope for v0.1

- **Hosting or relaying other people's OAuth.** `oauthpy` is not a credential broker, a multi-user gateway, a proxy, or a hosted agent API.
- **Reverse-engineering vendor web endpoints.** We drive OAuth-authenticated local clients only.
- **Scraping TUI output.** The Codex TUI is not a supported integration surface.
- **Wire-compatibility with vendor cloud APIs.** `oauthpy` exposes its own small explicit Python API rather than pretending to be a clone of OpenAI's Responses API or Anthropic's Messages API.
- **Copying or importing tokens by default.** `oauthpy` never copies existing vendor credentials into `~/.oauthpy/` automatically.
- **Editing normal vendor credential files.** `oauthpy` does not directly edit `~/.codex/auth.json` or Claude credential files. It may create provider-owned config files under `~/.oauthpy/` so the official tools can operate there.
- **Forcing isolated Gemini OAuth.** Gemini CLI does not currently document a safe config/auth-root override comparable to `CODEX_HOME` or `CLAUDE_CONFIG_DIR`, so Gemini support reuses official external CLI state.
- **HTTP proxy/server.** The library is the product. A future proxy could wrap it; v0.1 does not include one.
- **Full conversational session management.** `oauthpy interactive` is a local in-memory debugging helper, not a persistent session product. Multi-turn resume is only supported to the extent the underlying provider trivially allows it (e.g. Claude's `resume` option via `provider_options`).
- **Automatic retries by default.** Retries are available for transient provider/transport failures, but they are disabled unless `provider_options["max_retries"]` is set. This avoids hidden extra cost and repeated tool side effects.
- **Hiding provider refusals.** Claude structured-output support normalizes validated SDK `structured_output`, but it does not bypass or mask real Claude policy refusals, `is_error=True` result messages, or schema-generation failures. Those remain provider errors.

## Compliance notes

### Anthropic (Claude)

The official `claude-agent-sdk` documentation states:

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead.

`oauthpy` is not offering Claude.ai login to other people. It is a user's local wrapper around their own Claude auth. Login uses `claude auth login`; `claude setup-token` is a separate CI/headless helper and is not oauthpy's normal login path. The SDK's authentication is owned by Claude Code / the SDK; oauthpy only selects the config directory or passes documented env indicators through.

If you intend to redistribute Claude access through `oauthpy` — e.g. build a hosted product, a team gateway, or a shared agent — you must switch to API-key auth and consult Anthropic's terms. Do not build a service that hands out Claude.ai sessions on someone else's behalf.

### OpenAI (Codex)

OpenAI documents Codex CLI authentication and credential storage. `oauthpy` runs the official `codex` CLI locally with your login and, in isolated mode, sets `CODEX_HOME` so Codex owns state under `~/.oauthpy/codex`. See [Codex authentication](https://developers.openai.com/codex/auth) for the authoritative documentation.

Do not use `oauthpy` to proxy ChatGPT Plus/Pro capacity to other users — that is not what the OAuth grant covers.

### Google (Gemini)

Gemini support shells out to the official `gemini` CLI in headless JSON mode. Login uses the official interactive Gemini CLI flow or documented env auth such as `GEMINI_API_KEY`, Vertex AI variables, or ADC-related configuration.

`oauthpy` does not implement Google OAuth, does not copy Gemini tokens, and does not parse Gemini OAuth credential files. Cached Google login status is only best-effort because Gemini CLI does not expose a separate documented `auth status` command; oauthpy may read non-secret `~/.gemini/settings.json` to identify a selected auth type.

### Security posture

- `shell=False` everywhere. All subprocess calls pass argv lists.
- Best-effort secret redaction in logs, `repr`, and exception messages via `oauthpy._redact`.
- `oauthpy` never prints OAuth tokens.
- `oauthpy` never persists OAuth tokens itself beyond what upstream tools already manage under the selected provider config directory.
- No hidden global state: no module-level singletons, no env mutation outside the caller's explicit `env` argument.

File-based credential storage is sensitive. Codex may store credentials in `auth.json`; Claude may store provider state under `CLAUDE_CONFIG_DIR`; Gemini may store state under `~/.gemini`; and upstream tools may use OS keychain behavior depending on platform/config. oauthpy does not turn those files into a portable credential bundle.

The redactor is heuristic — it catches common secret shapes (`sk-*`, `sk-ant-*`, `ghp_*`, `Bearer …`, JWTs, long hex blobs). It is not a replacement for careful logging, and it is not a security boundary. Do not print arbitrary user data and trust the redactor to catch everything.

Retries can repeat provider work. Enable them mainly for read-only one-shot batch workloads where a transient CLI/SDK failure is more likely than a deterministic prompt failure. Avoid broad retries around tool-mutating prompts unless the surrounding workflow is idempotent.

### Reporting issues

If you find a credential-exposure bug, a sandbox escape, or anything else that looks like it could hurt a user, please open an issue marked `security` rather than a PR.
