# Architecture

## Why Codex uses CLI JSONL and Claude uses the Agent SDK

Both Codex and Claude Code expose a "run a prompt locally, using my OAuth login" capability, but through very different integration surfaces:

- **Codex** does not ship a stable Python SDK in v0.1. It does ship a mature `codex exec --json` mode in the official CLI that emits a newline-delimited JSON event stream covering thread/turn lifecycle events, item events, messages, reasoning, plan updates, tool calls, command executions, file changes, errors, and `turn.completed`. That stream is our supported local programmatic surface, so `oauthpy.providers.codex` parses it.
- **Claude Code** ships `claude-agent-sdk` on PyPI with a streaming `query(prompt, options=ClaudeAgentOptions(...))` entrypoint. Messages are typed (`SystemMessage`, `AssistantMessage`, `ResultMessage`, ...). There's no reason to shell out to the `claude` CLI from Python when the SDK is right there, so `oauthpy.providers.claude` uses it directly.

The consequence is that the two adapters look internally very different but converge on the same `Event` / `RunResult` / `AuthStatus` shapes, which is the entire point of this package.

## Async core + sync facade

The primitive is `async def Provider.run(...)` / `async def Provider.stream(...)`. `Client` is an async-core-with-sync-facade wrapper on top:

- Called from synchronous code, `Client.run` and `Client.auth_status` block and return the resolved value.
- Called from inside a running event loop, they return the coroutine so callers can `await` it.
- `Client.stream_sync` drains the async stream on a background thread and closes the underlying stream when the caller stops iteration early.

This mirrors the shape most Python developers expect from a modern SDK. In async applications, use `Client.stream`; `Client.stream_sync` is for synchronous callers.

## Auth Source Layer

Auth state is resolved before provider calls:

| Source | Codex effect | Claude effect |
|--------|--------------|---------------|
| `auto` | Prefer authenticated `~/.oauthpy/codex`, then normal Codex CLI state. | Prefer authenticated `~/.oauthpy/claude`, then normal Claude CLI/session/env state. |
| `oauthpy` | Set `CODEX_HOME=$OAUTHPY_HOME/codex`. | Set `CLAUDE_CONFIG_DIR=$OAUTHPY_HOME/claude`. |
| `external` | Do not set `CODEX_HOME`. | Do not set `CLAUDE_CONFIG_DIR`. |

`OAUTHPY_HOME` defaults to `~/.oauthpy`. Directory creation is private (`0700`) where the platform supports it. oauthpy never copies external vendor credentials into this tree by default.

## Event taxonomy

Every event is normalized to one of eight `EventKind` values:

| Kind | Meaning |
|------|---------|
| `message` | Assistant-authored text to display to the user |
| `reasoning` | Chain-of-thought / scratchpad summary |
| `plan` | Plan update from a planning tool / agent scaffold |
| `tool` | A tool call (invocation; output usually follows) |
| `command` | A shell command the agent ran |
| `file_change` | A file edit or write by the agent |
| `error` | Any error surfaced by the provider |
| `done` | Terminal marker; always the last event of a stream |

`Event.raw` always holds the underlying provider payload so advanced callers can drop down a level.

## Error taxonomy

```
OauthPyError
├── UnsupportedProviderError    # bad provider name
├── ProviderNotInstalledError   # CLI missing / SDK not importable
├── AuthRequiredError           # provider installed but not logged in
├── ProtocolError               # provider returned malformed output
├── CommandExecutionError       # subprocess exited non-zero
└── TimeoutExceededError        # run / stream exceeded its budget
```

All error messages go through `_redact` so tokens and other secrets are masked before they ever reach a log or traceback.

## Auth backend interface

`auth.py` defines a minimal `AuthBackend` Protocol with `status(provider)` and `login(provider)`. The default implementation is `SubprocessAuthBackend`, which delegates to each provider adapter (which in turn shells out to the upstream CLI / SDK).

A future direct backend would implement the same Protocol and be passed in via `Client(provider, auth_backend=...)`. The public `Client` API does not need to change. v0.1 intentionally does not implement that backend because the supported path is official local CLIs/SDKs.

## What we deliberately do not do in v0.1

- Do not edit `~/.codex/auth.json` or any Claude credential file.
- Do not copy existing vendor tokens into `~/.oauthpy/` automatically.
- Do not reverse-engineer vendor web endpoints.
- Do not scrape TUI output.
- Do not try to be wire-compatible with vendor cloud APIs.
- Do not build an HTTP proxy/server.
- Do not implement our own PKCE dance.

See [limitations.md](limitations.md) for more.
