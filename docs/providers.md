# Providers

Providers expose the same public methods via `Client`:

| Method | Returns |
|--------|---------|
| `run(prompt, cwd=None, model=None, timeout=None, env=None, provider_options=None)` | `RunResult` |
| `stream(prompt, ...)` | async iterator of `Event` |
| `stream_sync(prompt, ...)` | synchronous iterator of `Event` |
| `auth_status()` | `AuthStatus` |
| `login()` | `None` (side-effect: runs login flow) |
| `available()` | `bool` |

Provider-specific behavior and options live on `provider_options` so the shared API stays small.

Provider auth state is selected at construction time:

```python
from oauthpy import Client

Client("codex")                         # auth_source="auto"
Client("codex", auth_source="oauthpy")   # CODEX_HOME under ~/.oauthpy
Client("claude", auth_source="external") # normal Claude Code session
Client("gemini")                         # normal Gemini CLI session
```

`auth_source="auto"` checks isolated oauthpy state first and then falls back to external vendor state. `oauthpy_home` or `OAUTHPY_HOME` can override the default `~/.oauthpy`.

## Common retry options

Retries are **off by default** for every provider. Set `provider_options["max_retries"]` to opt in for batch workloads that can tolerate repeated provider calls.

| Key | Default | Meaning |
|-----|---------|---------|
| `max_retries` | `0` | Number of retries after the first failed attempt. |
| `retry_backoff_s` | `1.0` | Initial exponential backoff delay. |
| `retry_backoff_max_s` | `8.0` | Maximum delay after jitter. |
| `retry_jitter_s` | `0.25` | Added random jitter range. |
| `retry_on_timeout` | `False` | Retry `TimeoutExceededError` only when explicitly enabled. |

The shared retry wrapper only retries failures before usable output is emitted. Provider adapters classify their own transient failures:

- Claude retries known `claude-agent-sdk` reader/transport failures such as `Fatal error in message reader` and opaque `Command failed with exit code 1` reader failures.
- Codex and Gemini retry configured timeouts and temporary CLI/network failures.
- No provider retries auth failures, invalid model/config errors, permission/sandbox denials, deterministic output/schema failures, or post-output stream failures.

If all attempts fail, oauthpy raises one redacted `CommandExecutionError` with all failed-attempt diagnostics. If a later attempt succeeds, retry metadata is available on `RunResult.raw["retry"]`.

## Codex

**Transport**: `codex-cli-jsonl` — shells out to `codex exec --json` and parses the JSONL stream.

**Binary lookup**: defaults to `codex` on PATH. Override with the `OAUTHPY_CODEX_BINARY` environment variable (useful in tests).

**Auth isolation**: `auth_source="oauthpy"` sets `CODEX_HOME=$OAUTHPY_HOME/codex`, creates the directory privately where supported, and ensures `config.toml` uses file credential storage unless the user already set a supported store. `auth_source="external"` does not set `CODEX_HOME`.

**Supported `provider_options` keys**:

| Key | Maps to `codex exec` flag |
|-----|----------------------------|
| `sandbox` | `--sandbox read-only` / `workspace-write` / `danger-full-access` |
| `full_auto` | `--full-auto` (low-friction preset) |
| `ask_for_approval` | `--ask-for-approval never` / `on-request` / `untrusted` |
| `skip_git_repo_check` | `--skip-git-repo-check` |
| `config` (dict) | repeatable `--config KEY=VALUE` |
| `extra_argv` (list) | appended to the argv before the prompt |
| *any other key* | appended as `--config KEY=VALUE` |

**Example**:

```python
from oauthpy import Client

result = Client("codex").run(
    "Refactor src/app.py to split the main() function into helpers",
    cwd=".",
    model="gpt-5",
    timeout=300,
    provider_options={"sandbox": "workspace-write", "ask_for_approval": "on-request"},
)
print(result.text)
for ev in result.events:
    print(ev.kind, ev.text)
```

**Event sources**: `EventKind.MESSAGE` (assistant text), `REASONING` (scratchpad), `PLAN` (plan updates), `TOOL` (tool calls), `COMMAND` (shell exec), `FILE_CHANGE` (edits), `ERROR`, `DONE`.

Current Codex JSONL schema support includes top-level `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.started`, `item.updated` / `item.delta`, `item.completed`, and `error` rows. Meaningful item payloads are read from `row["item"]`; raw rows are preserved on `Event.raw`.

## Claude

**Transport**: `claude-agent-sdk` — calls `claude_agent_sdk.query(prompt, options=ClaudeAgentOptions(...))` directly.

**Auth isolation**: `auth_source="oauthpy"` sets `CLAUDE_CONFIG_DIR=$OAUTHPY_HOME/claude` for `claude auth status --json`, `claude auth login`, and SDK runs. `auth_source="external"` does not set `CLAUDE_CONFIG_DIR`.

**Login/status**: `Client("claude").login()` runs `claude auth login`, not `claude setup-token`. Status uses `claude auth status --json` when the CLI is installed, with documented environment-variable fallback only when CLI status is unavailable.

**Supported `provider_options` keys**: anything `ClaudeAgentOptions` accepts. Common ones:

| Key | Effect |
|-----|--------|
| `allowed_tools` | whitelist of built-in tools (`Read`, `Edit`, `Bash`, `Glob`, `Grep`, ...) |
| `permission_mode` | `"default"`, `"acceptEdits"`, `"plan"`, etc. |
| `mcp_servers` | dict of MCP server configs |
| `hooks` | hook callback dict |
| `agents` | subagent definitions |
| `setting_sources` | restrict which `.claude/` dirs load |
| `resume` | resume a captured `session_id` |
| `env` | merged with oauthpy's resolved SDK environment |

`cwd` and `model` are set from the shared API.

**Example**:

```python
from oauthpy import Client

result = Client("claude").run(
    "Find all TODO comments and summarize them",
    cwd=".",
    model="claude-opus-4-7",
    provider_options={"allowed_tools": ["Read", "Glob", "Grep"]},
)
print(result.text)
```

**Event sources**: mapping from SDK message/block types:

| SDK message | `EventKind` |
|-------------|-------------|
| `SystemMessage` | `MESSAGE` |
| `TextBlock` | `MESSAGE` |
| `ThinkingBlock` | `REASONING` |
| `ToolUseBlock` / `ServerToolUseBlock` | `TOOL` |
| `ToolResultBlock` / `ServerToolResultBlock` | `TOOL` or `ERROR` |
| `ResultMessage` | `DONE` |
| Error-bearing messages | `ERROR` |
| *unknown* | `MESSAGE` |

The raw SDK object is always on `Event.raw`, so callers who want strict typing can narrow it themselves.

## Gemini

**Transport**: `gemini-cli-jsonl` — shells out to `gemini --prompt ... --output-format stream-json` and parses newline-delimited JSON events. Single-object `--output-format json` is also supported through `provider_options={"output_format": "json"}`.

**Binary lookup**: defaults to `gemini` on PATH. Override with the `OAUTHPY_GEMINI_BINARY` environment variable.

**Auth isolation**: Gemini currently uses external CLI state only. The official CLI documents `~/.gemini`, project `.gemini`, and env auth, but no safe config/auth-root override equivalent to `CODEX_HOME` or `CLAUDE_CONFIG_DIR`. `auth_source="oauthpy"` is therefore reported as unsupported.

**Model default**: oauthpy passes `--model auto` unless the shared `model=` argument is set. This delegates model choice to the Gemini CLI while preserving explicit `model=` overrides; thinking-budget control is not exposed because Gemini CLI does not document a stable reasoning-effort flag.

**Supported `provider_options` keys**:

| Key | Maps to `gemini` flag |
|-----|------------------------|
| `output_format` | `--output-format stream-json` or `json` |
| `all_files` | `--all-files` |
| `include_directories` | `--include-directories DIRS` |
| `sandbox` | `--sandbox` / `--sandbox=VALUE` |
| `approval_mode` | `--approval-mode VALUE` |
| `yolo` | `--yolo` |
| `extra_argv` | appended to argv |

`cwd` is passed as the subprocess working directory. `model` maps to `--model`.

**Example**:

```python
from oauthpy import Client

result = Client("gemini").run(
    "Summarize this repository in one paragraph",
    cwd=".",
    model="gemini-2.5-flash",
    timeout=120,
)
print(result.text)
```

**Event sources**: Gemini JSONL `message` rows map to `MESSAGE`, `tool_use` / `tool_result` rows map to `TOOL`, `error` maps to `ERROR`, and `result` maps to `DONE` with best-effort usage extraction from `stats`.

## Running streams manually

```python
import asyncio
from oauthpy import Client, EventKind

async def main() -> None:
    async for ev in Client("codex").stream("Write a hello-world", cwd="."):
        if ev.kind is EventKind.MESSAGE and ev.text:
            print(ev.text)
        elif ev.kind is EventKind.COMMAND and ev.text:
            print(f"[$] {ev.text}")
        elif ev.kind is EventKind.ERROR:
            print(f"[!] {ev.text}")

asyncio.run(main())
```
