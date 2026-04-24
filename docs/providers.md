# Providers

Both providers expose the same public methods via `Client`:

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
```

`auth_source="auto"` checks isolated oauthpy state first and then falls back to external vendor state. `oauthpy_home` or `OAUTHPY_HOME` can override the default `~/.oauthpy`.

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
