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

It is not a hosted service, a multi-user gateway, or a credential broker. It runs on your machine and lets you either isolate provider login state under `~/.oauthpy/` or explicitly reuse the normal vendor CLI/session state.

## Scope

In scope for v0.1:

- One-shot execution via `Client.run(prompt, cwd=..., model=..., timeout=..., env=..., provider_options=...)`.
- Streaming via `Client.stream(...)` as an async iterator of normalized `Event` records.
- Best-effort, read-only `Client.auth_status()` per provider.
- `Client.login()` that shells out to the provider's official login flow.
- `Client.available()` installed/provider-ready check.
- Auth-source selection: `auto`, `oauthpy`, or `external`.
- A tiny debugging CLI (`oauthpy run`, `oauthpy auth login`, `oauthpy auth status`, `oauthpy available`, `oauthpy chat`).

Out of scope for v0.1:

- Hosting, relaying, or proxying anyone else's OAuth.
- Reverse-engineering vendor web endpoints.
- Scraping TUI output.
- Wire-compatibility with vendor cloud APIs.
- Persistent multi-turn session management. `oauthpy chat` is only a local in-memory debugging facade.
- Editing `~/.codex/auth.json` or Claude credential files directly.

## Installation

```bash
python -m pip install oauthpy
# or, with the Claude provider dependency included:
python -m pip install "oauthpy[claude]"
```

Python 3.10+. Cross-platform (Windows, Linux, macOS).

## Auth prerequisites

`oauthpy` never implements vendor OAuth itself in v0.1. It delegates login, refresh, and credential formats to the provider's official local tooling.

- **Default isolated login**: `oauthpy auth login --provider codex` or `oauthpy auth login --provider claude`. This creates `~/.oauthpy/<provider>/` with private directory permissions where supported, then runs the official CLI login with provider-specific config env vars.
- **External session reuse**: existing `codex` and `claude` logins are still reusable out of the box. The default `auth_source="auto"` prefers authenticated oauthpy-isolated state, then falls back to normal vendor CLI/session state.
- **Forced source**: use `Client("codex", auth_source="oauthpy")` for isolated state or `Client("claude", auth_source="external")` for normal vendor behavior.

Provider-specific auth isolation:

- **Codex** — install the `codex` CLI (`npm i -g @openai/codex`). In `oauthpy` source mode, oauthpy sets `CODEX_HOME=~/.oauthpy/codex` and ensures `config.toml` contains `cli_auth_credentials_store = "file"` unless you already set a supported value (`file`, `keyring`, or `auto`).
- **Claude** — install the Claude Code CLI and `claude-agent-sdk` (`pip install "oauthpy[claude]"`). In `oauthpy` source mode, oauthpy sets `CLAUDE_CONFIG_DIR=~/.oauthpy/claude` for CLI status/login and SDK runs.

Normal Claude login uses `claude auth login`. `claude setup-token` is a separate headless/CI helper that prints a long-lived token; oauthpy does not use it for regular login.

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

## Auth-source selection

`Client(provider, auth_source="auto", oauthpy_home=None)` keeps the shared API small while making auth state explicit:

| Source | Behavior |
|--------|----------|
| `auto` | Prefer authenticated `~/.oauthpy/<provider>/` state; otherwise reuse normal vendor CLI/session auth; if login is needed, create isolated oauthpy state. |
| `oauthpy` | Force isolated state under `OAUTHPY_HOME` or `~/.oauthpy`. |
| `external` | Force normal vendor behavior without oauthpy env overrides. |

CLI equivalents:

```bash
oauthpy auth login --provider codex          # defaults to --source oauthpy
oauthpy auth login --provider claude --source external
oauthpy auth status --provider codex --source auto
oauthpy run --provider claude --source oauthpy "summarize this repo"
```

## CLI setup debugging walk-through

Use this sequence when validating a fresh machine or debugging provider setup. It checks the Python package, the vendor CLIs, auth-source resolution, one-shot runs, and the examples separately so failures are easier to localize.

Create a clean environment and install oauthpy:

```bash
conda create -y -n oauthpy python=3.12 pip
conda activate oauthpy
python -m pip install -e ".[claude,dev]"
python -c "from oauthpy import Client; print(Client)"
oauthpy --help
```

Check that the provider CLIs are installed and visible:

```bash
codex --version
claude --version
```

Inspect auth without printing secrets:

```bash
oauthpy auth status --provider codex --source auto --json
oauthpy auth status --provider claude --source auto --json
oauthpy available --provider codex
oauthpy available --provider claude
```

If either provider is unauthenticated, use oauthpy-isolated login by default:

```bash
oauthpy auth login --provider codex
oauthpy auth login --provider claude
```

To debug against the normal vendor CLI/session state instead, force external mode:

```bash
oauthpy auth status --provider codex --source external --json
oauthpy auth status --provider claude --source external --json
```

Run minimal one-shot smoke tests:

```bash
oauthpy run --provider codex --source auto --cwd . "Reply with exactly oauthpy-codex-smoke"
oauthpy run --provider claude --source auto --cwd . "Reply with exactly oauthpy-claude-smoke"
```

Then run the examples:

```bash
python examples/basic_codex.py
python examples/basic_claude.py
python examples/stream_codex.py
python examples/stream_claude.py
```

Failure triage:

- If Conda cannot create the environment, check network access to the configured channels and use writable cache directories such as `CONDA_PKGS_DIRS=/tmp/oauthpy-conda-pkgs` and `XDG_CACHE_HOME=/tmp/oauthpy-cache`.
- If auth succeeds but Codex runs fail with a read-only filesystem error, ensure Codex can write its session/config state, or run an oauthpy-isolated login with a writable `OAUTHPY_HOME`.
- If Claude auth succeeds but runs hang, test the upstream CLI directly with `claude -p "Reply with exactly oauthpy-claude-smoke"` to separate Claude Code/network issues from oauthpy wrapper issues.
- Do not copy, paste, commit, or share credential files from `~/.oauthpy/` or the normal vendor config directories.

## Architecture note

Codex and Claude Code expose very different integration surfaces on the supported, local path:

- **Codex** does not have a stable Python SDK in v0.1, but its official CLI already has a mature `codex exec --json` mode that emits a JSONL event stream. `oauthpy` parses that stream into normalized `Event` records.
- **Claude Code** ships an official Python SDK (`claude-agent-sdk`) with a streaming `query(prompt, options=ClaudeAgentOptions(...))` entrypoint. `oauthpy` calls that directly instead of shelling out.

Both adapters normalize to the same `Event` / `RunResult` / `AuthStatus` models and preserve the raw provider payload on `Event.raw` so advanced callers can drop down a level when they need to.

## Security note

`oauthpy` is designed to run **on your machine, for your account**. It:

- never prints or persists OAuth tokens beyond what the upstream tool already does;
- creates `~/.oauthpy/` and provider subdirectories with `0700` permissions where the OS supports it;
- never copies existing vendor tokens into `~/.oauthpy/` by default;
- never edits normal vendor credential files directly;
- passes subprocess arguments as argv lists (`shell=False` everywhere);
- redacts secrets from logs, reprs, and exception messages on a best-effort basis.

File-based OAuth credential storage is sensitive. If Codex stores credentials in `auth.json`, treat that file like a password: do not commit it, paste it into tickets, or share it. Some upstream tools may use an OS keychain depending on platform and config; oauthpy does not abstract that away.

**This is not a hosted credential relay.** Do not deploy it as a gateway for other users. If you need that, build your own service on top of vendor-approved primitives.

## Anthropic compliance note

The official `claude-agent-sdk` documentation states:

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK.

`oauthpy` is a local wrapper for the user's own Claude auth. It does not offer Claude.ai login to other people. If you fork this to build a third-party product that re-distributes Claude.ai access, you need vendor-approved authentication and policy review. See `docs/limitations.md`.

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
