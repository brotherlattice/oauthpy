# oauthpy

`oauthpy` is a local, user-operated Python library that gives a small, typed, async-core-with-sync-facade API over two OAuth-authenticated local coding agents: **Codex** (via the official `codex` CLI) and **Claude Code** (via the official `claude-agent-sdk`).

It is not a hosted service, a multi-user gateway, or a credential broker. It runs on your machine, reuses the auth your official client already set up, and lets you script those agents from Python.

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
