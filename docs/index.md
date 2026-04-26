# oauthpy

**Local Python access to OAuth-authenticated coding agents.**

`oauthpy` is a local, user-operated Python library that wraps local **Codex** and **Claude Code** sessions behind a small, typed, async-core-with-sync-facade API. Codex is driven via the official `codex` CLI; Claude Code is driven via the official `claude-agent-sdk`.

It is not a hosted service, a multi-user gateway, or a credential broker. It runs on your machine and can either isolate provider state under `~/.oauthpy/` or explicitly reuse the normal vendor CLI/session state.

```{toctree}
:maxdepth: 2
:caption: Guide

architecture
auth
providers
development
limitations
api/index
```
