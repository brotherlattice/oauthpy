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

**Local Python access to OAuth-authenticated coding agents.**

`oauthpy` is a **local, user-operated** Python library that wraps local Codex, Claude Code, and optional Gemini CLI sessions behind a small, typed, async-core-with-sync-facade API:

- **Codex** (OpenAI), driven by the official `codex` CLI via `codex exec --json`
- **Claude Code** (Anthropic), driven by the official `claude-agent-sdk`
- **Gemini** (Google), optionally driven by the official `gemini` CLI in headless JSON mode

It is not a hosted service, a multi-user gateway, or a credential broker. It runs on your machine and lets you either isolate provider login state under `~/.oauthpy/` or explicitly reuse the normal vendor CLI/session state.

## Scope

In scope for v0.1:

- One-shot execution via `Client.run(prompt, cwd=..., model=..., timeout=..., env=..., provider_options=...)`.
- Streaming via `Client.stream(...)` as an async iterator of normalized `Event` records.
- Best-effort, read-only `Client.auth_status()` per provider.
- `Client.login()` that shells out to the provider's official login flow.
- `Client.available()` installed/provider-ready check.
- Auth-source selection: `auto`, `oauthpy`, or `external`.
- A tiny debugging CLI (`oauthpy run`, `oauthpy interactive`, `oauthpy auth login`, `oauthpy auth status`, `oauthpy available`).

Out of scope for v0.1:

- Hosting, relaying, or proxying anyone else's OAuth.
- Reverse-engineering vendor web endpoints.
- Scraping TUI output.
- Wire-compatibility with vendor cloud APIs.
- Persistent multi-turn session management. `oauthpy interactive` is only a local in-memory debugging facade.
- Editing `~/.codex/auth.json` or Claude credential files directly.
- Isolated Gemini OAuth state until Gemini CLI documents a safe config/auth-root override.

## Installation

```bash
python -m pip install oauthpy
```

Tested on Python 3.10-3.13 across Windows, Linux, and macOS.

Gemini support has no extra Python dependency, but the optional extra is reserved so users can opt into the provider surface explicitly:

```bash
python -m pip install "oauthpy[gemini]"
```

You still need to install the external Gemini CLI separately, for example with `npm install -g @google/gemini-cli`.

## Auth prerequisites

`oauthpy` never implements vendor OAuth itself in v0.1. It delegates login, refresh, and credential formats to the provider's official local tooling.

- **Default isolated login**: `oauthpy auth login --provider codex` or `oauthpy auth login --provider claude`. This creates `~/.oauthpy/<provider>/` with private directory permissions where supported, then runs the official CLI login with provider-specific config env vars.
- **Gemini login**: `oauthpy auth login --provider gemini` opens the official Gemini CLI interactive auth flow and always uses external Gemini CLI state.
- **External session reuse**: existing `codex`, `claude`, and `gemini` logins are still reusable out of the box. The default `auth_source="auto"` prefers authenticated oauthpy-isolated state where supported, then falls back to normal vendor CLI/session state.
- **Forced source**: use `Client("codex", auth_source="oauthpy")` for isolated state or `Client("claude", auth_source="external")` for normal vendor behavior.

Provider-specific auth isolation:

- **Codex** — install the `codex` CLI (`npm i -g @openai/codex`). In `oauthpy` source mode, oauthpy sets `CODEX_HOME=~/.oauthpy/codex` and ensures `config.toml` contains `cli_auth_credentials_store = "file"` unless you already set a supported value (`file`, `keyring`, or `auto`).
- **Claude** — install the Claude Code CLI. The Python `claude-agent-sdk` dependency is installed with oauthpy by default. In `oauthpy` source mode, oauthpy sets `CLAUDE_CONFIG_DIR=~/.oauthpy/claude` for CLI status/login and SDK runs.
- **Gemini** — install the Gemini CLI (`npm install -g @google/gemini-cli`). oauthpy uses `gemini --prompt ... --output-format stream-json`. Gemini auth currently stays external because the CLI documents `~/.gemini` and env auth, but not a `CODEX_HOME`/`CLAUDE_CONFIG_DIR`-style override.

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

## Gemini quickstart

Gemini is optional and shells out to the installed `gemini` CLI:

```python
from oauthpy import Client

client = Client("gemini")
result = client.run("Summarize this repo", cwd=".")
print(result.text)
```

Use `oauthpy auth login --provider gemini` or run `gemini` directly to configure the official CLI login. Environment auth such as `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_GENAI_USE_VERTEXAI`, or `GOOGLE_GENAI_USE_GCA` is detected without printing values.

## Model and reasoning defaults

`oauthpy` applies lightweight per-run defaults without editing your vendor config files:

- **Codex** uses the Codex CLI's provider/default model, but sends `model_reasoning_effort=low` through `--config` unless you override it.
- **Claude Code** uses the documented `opus` model alias and `low` effort by default through `ClaudeAgentOptions(model="opus", effort="low")`.
- **Gemini** uses the Gemini CLI's `auto` model alias by default. This lets the upstream CLI choose the model while oauthpy still exposes explicit model selection; reasoning-effort or thinking-budget is not exposed because the CLI does not document a stable flag through oauthpy.

Override the model with the shared `model=` argument:

```python
Client("codex").run("summarize", cwd=".", model="gpt-5.3-codex")
Client("claude").run("summarize", cwd=".", model="sonnet")
Client("gemini").run("summarize", cwd=".", model="pro")
```

Override reasoning effort through provider options:

```python
Client("codex").run("deep review", provider_options={"reasoning_effort": "high"})
Client("claude").run("deep review", provider_options={"reasoning_effort": "high"})
```

CLI equivalents:

```bash
oauthpy run --provider codex --reasoning-effort high "review this repo"
oauthpy run --provider claude --model sonnet --reasoning-effort low "summarize this repo"
oauthpy run --provider gemini --model flash-lite "summarize this repo"
```

Interactive helpers mirror the upstream naming:

- Codex reasoning efforts: `minimal`, `low`, `medium`, `high`, `xhigh`.
- Claude effort levels: `low`, `medium`, `high`, `xhigh`, `max`.
- Claude model aliases include `default`, `best`, `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, and `opusplan`.
- Gemini model aliases/examples include `auto`, `pro`, `flash`, `flash-lite`, `gemini-3-pro-preview`, `gemini-3-flash-preview`, `gemini-2.5-pro`, `gemini-2.5-flash`, and `gemini-2.5-flash-lite`.

Inside `oauthpy interactive`, use `/model NAME`, `/model clear`, `/effort LEVEL`, `/effort clear`, `/models`, and `/efforts`.

## Auth-source selection

`Client(provider, auth_source="auto", oauthpy_home=None)` keeps the shared API small while making auth state explicit:

| Source | Behavior |
|--------|----------|
| `auto` | Prefer authenticated `~/.oauthpy/<provider>/` state; otherwise reuse normal vendor CLI/session auth; if login is needed, create isolated oauthpy state. |
| `oauthpy` | Force isolated state under `OAUTHPY_HOME` or `~/.oauthpy`. |
| `external` | Force normal vendor behavior without oauthpy env overrides. |

For Gemini, `auto` and `external` both use the official external Gemini CLI state. `oauthpy` source is reported as unsupported until Gemini CLI documents a safe isolated config/auth-root override.

CLI equivalents:

```bash
oauthpy auth login --provider codex          # defaults to --source oauthpy
oauthpy auth login --provider claude --source external
oauthpy auth login --provider gemini         # defaults to --source external
oauthpy auth status --provider codex --source auto
oauthpy run --provider claude --source oauthpy "summarize this repo"
oauthpy interactive --provider codex --source auto --cwd .
```

## Interactive CLI

Use `oauthpy interactive` to debug setup and try repeated prompts without writing Python:

```bash
oauthpy interactive --provider codex --source auto --cwd .
oauthpy interactive --provider claude --source auto --cwd .
oauthpy interactive --provider gemini --source auto --cwd .
```

Plain text sends a transcript-aware chat turn. Slash commands handle setup and diagnostics: `/status`, `/available`, `/login`, `/provider`, `/source`, `/cwd`, `/model`, `/models`, `/effort`, `/reasoning`, `/efforts`, `/timeout`, `/events`, `/run`, `/stream`, `/clear`, `/help`, and `/exit`.

Slash-command tab completion is enabled when `prompt-toolkit` is installed. It is part of oauthpy's default install; if it is unavailable, the CLI falls back to standard `input()` without completion.

Example Claude session:

```text
$ oauthpy interactive --provider claude --source auto --cwd .
oauthpy interactive. Type /help for commands; /exit to quit.
oauthpy[claude:auto]> Hello, is claude code ready?
claude> Yes, Claude Code is ready! How can I help you today?
oauthpy[claude:auto]>
```

In this example, `--source auto` does not ask oauthpy to implement OAuth itself. It asks oauthpy to resolve local auth state: first use authenticated isolated Claude Code config under `~/.oauthpy/claude` if present, otherwise fall back to the normal Claude Code CLI/session auth on the machine. oauthpy passes the resolved non-secret environment/config location to the official Claude Code CLI/SDK and never prints token values.

`oauthpy chat` remains as a compatibility alias for the same local in-memory interaction mode.

## CLI setup debugging walk-through

Use this sequence when validating a fresh machine or debugging provider setup. It checks the Python package, the vendor CLIs, auth-source resolution, one-shot runs, and the examples separately so failures are easier to localize.

Create a clean environment and install oauthpy:

```bash
conda create -y -n oauthpy python=3.12 pip
conda activate oauthpy
python -m pip install -e ".[dev]"
python -c "from oauthpy import Client; print(Client)"
oauthpy --help
```

Check that the provider CLIs are installed and visible:

```bash
codex --version
claude --version
gemini --version
```

Inspect auth without printing secrets:

```bash
oauthpy auth status --provider codex --source auto --json
oauthpy auth status --provider claude --source auto --json
oauthpy auth status --provider gemini --source auto --json
oauthpy available --provider codex
oauthpy available --provider claude
oauthpy available --provider gemini
oauthpy interactive --provider codex --source auto --cwd .
```

If either provider is unauthenticated, use oauthpy-isolated login by default:

```bash
oauthpy auth login --provider codex
oauthpy auth login --provider claude
oauthpy auth login --provider gemini
```

To debug against the normal vendor CLI/session state instead, force external mode:

```bash
oauthpy auth status --provider codex --source external --json
oauthpy auth status --provider claude --source external --json
oauthpy auth status --provider gemini --source external --json
```

Run minimal one-shot smoke tests:

```bash
oauthpy run --provider codex --source auto --cwd . "Reply with exactly oauthpy-codex-smoke"
oauthpy run --provider claude --source auto --cwd . "Reply with exactly oauthpy-claude-smoke"
oauthpy run --provider gemini --source auto --cwd . "Reply with exactly oauthpy-gemini-smoke"
```

Then run the examples:

```bash
python examples/basic_codex.py
python examples/basic_claude.py
python examples/basic_gemini.py
python examples/stream_codex.py
python examples/stream_claude.py
python examples/stream_gemini.py
```

Failure triage:

- If Conda cannot create the environment, check network access to the configured channels and use writable cache directories such as `CONDA_PKGS_DIRS=/tmp/oauthpy-conda-pkgs` and `XDG_CACHE_HOME=/tmp/oauthpy-cache`.
- If auth succeeds but Codex runs fail with a read-only filesystem error, ensure Codex can write its session/config state, or run an oauthpy-isolated login with a writable `OAUTHPY_HOME`.
- If Claude auth succeeds but runs hang, test the upstream CLI directly with `claude -p "Reply with exactly oauthpy-claude-smoke"` to separate Claude Code/network issues from oauthpy wrapper issues.
- If Gemini auth status is inconclusive, test the upstream CLI directly with `gemini -p "Reply with exactly oauthpy-gemini-smoke" --output-format json`. Gemini does not currently expose a separate documented auth-status command.
- Do not copy, paste, commit, or share credential files from `~/.oauthpy/` or the normal vendor config directories.

## Architecture note

Codex, Claude Code, and Gemini expose different integration surfaces on the supported, local path:

- **Codex** does not have a stable Python SDK in v0.1, but its official CLI already has a mature `codex exec --json` mode that emits a JSONL event stream. `oauthpy` parses that stream into normalized `Event` records.
- **Claude Code** ships an official Python SDK (`claude-agent-sdk`) with a streaming `query(prompt, options=ClaudeAgentOptions(...))` entrypoint. `oauthpy` calls that directly instead of shelling out.
- **Gemini** ships an official CLI with headless JSON and JSONL output via `gemini --prompt ... --output-format json|stream-json`. `oauthpy` parses that output and reuses normal Gemini CLI auth/config state.

Both adapters normalize to the same `Event` / `RunResult` / `AuthStatus` models and preserve the raw provider payload on `Event.raw` so advanced callers can drop down a level when they need to.

## Security note

`oauthpy` is designed to run **on your machine, for your account**. It:

- never prints or persists OAuth tokens beyond what the upstream tool already does;
- creates `~/.oauthpy/` and provider subdirectories with `0700` permissions where the OS supports it;
- never copies existing vendor tokens into `~/.oauthpy/` by default;
- never edits normal vendor credential files directly;
- does not inspect Gemini OAuth credential files; it only reads non-secret Gemini settings to identify a plausible selected auth type;
- passes subprocess arguments as argv lists (`shell=False` everywhere);
- redacts secrets from logs, reprs, and exception messages on a best-effort basis.

File-based OAuth credential storage is sensitive. If Codex stores credentials in `auth.json`, Claude stores state under `CLAUDE_CONFIG_DIR`, or Gemini stores state under `~/.gemini`, treat those files like passwords: do not commit them, paste them into tickets, or share them. Some upstream tools may use an OS keychain depending on platform and config; oauthpy does not abstract that away.

**This is not a hosted credential relay.** Do not deploy it as a gateway for other users. If you need that, build your own service on top of vendor-approved primitives.

## Anthropic compliance note

The official `claude-agent-sdk` documentation states:

> Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK.

`oauthpy` is a local wrapper for the user's own Claude auth. It does not offer Claude.ai login to other people. If you fork this to build a third-party product that re-distributes Claude.ai access, you need vendor-approved authentication and policy review. See `docs/limitations.md`.

## Development

```bash
python -m pip install -e .[dev]
pre-commit install
pytest -m "not live_codex and not live_claude and not live_gemini"
ruff check .
```

Docs:

```bash
python -m pip install -e .[docs]
sphinx-build -b html docs docs/_build/html
```

See `docs/development.md` for the full developer guide.
