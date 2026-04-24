[![PyPI - Version](https://img.shields.io/pypi/v/oauthpy)](https://pypi.org/project/oauthpy/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/oauthpy)](https://pypi.org/project/oauthpy/)
[![PyPI - License](https://img.shields.io/pypi/l/oauthpy)](LICENSE)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/oauthpy)](https://pypistats.org/packages/oauthpy)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/oauthpy?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=all+time+downloads)](https://pepy.tech/projects/oauthpy)
[![GitHub Actions](https://github.com/brotherlattice/oauthpy/actions/workflows/pypi-release.yml/badge.svg)](https://github.com/brotherlattice/oauthpy/actions/workflows/pypi-release.yml)
[![Documentation Status](https://readthedocs.org/projects/oauthpy/badge/?version=latest)](https://oauthpy.readthedocs.io/en/latest/?badge=latest)

## 🚧 Under Development

This project is still in an **alpha stage**. Expect rapid changes, incomplete features, and possible breaking updates between releases.

- The API may evolve as we stabilize core functionality.
- Documentation and examples are incomplete.
- Feedback and bug reports are especially valuable at this stage.

# oauthpy

`oauthpy` is a **local, user-operated** Python library that gives a small, typed, async-core-with-sync-facade API over two OAuth-authenticated local coding agents:

- **Codex** (OpenAI), driven by the official `codex` CLI via `codex exec --json`
- **Claude Code** (Anthropic), driven by the official `claude-agent-sdk`

It is not a hosted service, a multi-user gateway, or a credential broker. It runs on your machine, reuses the auth your official client already set up, and lets you script those agents from Python with a clean `Client(...)` API.

## Scope

In scope for v0.1:

- One-shot execution via `Client.run(prompt, cwd=..., model=..., timeout=..., env=..., provider_options=...)`.
- Streaming via `Client.stream(...)` as an async iterator of normalized `Event` records.
- Best-effort, read-only `Client.auth_status()` per provider.
- `Client.login()` that shells out to the provider's official login flow.
- `Client.available()` installed/provider-ready check.
- A tiny debugging CLI (`oauthpy run`, `oauthpy auth login`, `oauthpy auth status`).

Out of scope for v0.1:

- Hosting, relaying, or proxying anyone else's OAuth.
- Reverse-engineering vendor web endpoints.
- Scraping TUI output.
- Wire-compatibility with vendor cloud APIs.
- Multi-turn session management beyond what falls out trivially.
- Editing `~/.codex/auth.json` or Claude credential files directly.

## Installation

```bash
python -m pip install oauthpy
# or, with the Claude provider dependency included:
python -m pip install "oauthpy[claude]"
```

Python 3.10+. Cross-platform (Windows, Linux, macOS).

## Auth prerequisites

`oauthpy` never manages OAuth tokens itself in v0.1 — it delegates to the provider's official tooling.

- **Codex** — install the `codex` CLI (`npm i -g @openai/codex`), then run `codex login` once. That writes `~/.codex/auth.json`, which the CLI owns and refreshes.
- **Claude** — install the Claude Code CLI and/or `pip install claude-agent-sdk`. The SDK picks up auth from `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, or the local `~/.claude.json` login state that Claude Code already writes.

See `docs/auth.md` for details.

## Codex quickstart

```python
from oauthpy import Client

client = Client("codex")
result = client.run("Summarize this repo", cwd=".")
print(result.text)
```

Streaming:

```python
import asyncio
from oauthpy import Client

async def main():
    async for event in Client("codex").stream("Refactor module X", cwd="."):
        print(event.kind, event.text)

asyncio.run(main())
```

## Claude quickstart

```python
from oauthpy import Client

client = Client("claude")
result = client.run("Write a failing test first for foo()", cwd=".")
print(result.text)
```

Streaming is identical: `async for event in Client("claude").stream(prompt, cwd="."): ...`.

## Architecture note

Codex and Claude Code expose very different integration surfaces on the supported, local path:

- **Codex** does not have a stable Python SDK in v0.1, but its official CLI already has a mature `codex exec --json` mode that emits a JSONL event stream. `oauthpy` parses that stream into normalized `Event` records.
- **Claude Code** ships an official Python SDK (`claude-agent-sdk`) with a streaming `query(prompt, options=ClaudeAgentOptions(...))` entrypoint. `oauthpy` calls that directly instead of shelling out.

Both adapters normalize to the same `Event` / `RunResult` / `AuthStatus` models and preserve the raw provider payload on `Event.raw` so advanced callers can drop down a level when they need to.

## Security note

`oauthpy` is designed to run **on your machine, for your account**. It:

- never prints or persists OAuth tokens beyond what the upstream tool already does;
- never edits `~/.codex/auth.json` or any Claude credential file;
- passes subprocess arguments as argv lists (`shell=False` everywhere);
- redacts secrets from logs, reprs, and exception messages on a best-effort basis.

**This is not a hosted credential relay.** Do not deploy it as a gateway for other users. If you need that, build your own service on top of vendor-approved primitives.

## Anthropic compliance note

The official `claude-agent-sdk` documentation states:

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK.

`oauthpy` is a local wrapper for the user's own Claude auth. It does not offer Claude.ai login to other people. If you fork this to build a third-party product that re-distributes Claude.ai access, you need to switch to API-key auth and talk to Anthropic. See `docs/limitations.md`.

## Development

```bash
python -m pip install -e .[dev]
pre-commit install
pytest -m "not live_codex and not live_claude"
ruff check .
```

Docs:

```bash
python -m pip install -e .[docs]
sphinx-build -b html docs docs/_build/html
```

See `docs/development.md` for the full developer guide.
