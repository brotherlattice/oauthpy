"""Tiny debugging CLI.

Usage:

.. code-block:: bash

    oauthpy run --provider codex "summarize this repo"
    oauthpy interactive --provider claude --cwd .
    oauthpy auth login --provider claude
    oauthpy auth status --provider codex
    oauthpy available --provider codex

The CLI is deliberately small and focused on local setup/debugging.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import TextIO, cast, get_args

from .client import Client
from .errors import OauthPyError
from .models import AuthSource, AuthStatus, ProviderName, RunResult

_PROVIDER_CHOICES = get_args(ProviderName)
_SOURCE_CHOICES = get_args(AuthSource)
_LOGIN_SOURCE_CHOICES = ("oauthpy", "external")


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oauthpy",
        description="Tiny debugging CLI for local oauthpy runs and provider auth checks.",
        epilog="\n".join(
            [
                "examples:",
                '  oauthpy run --provider codex "summarize this repo"',
                "  oauthpy interactive --provider claude --cwd .",
                "  oauthpy auth status --provider codex --source auto",
            ]
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a one-shot prompt")
    run_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    run_p.add_argument("--source", default="auto", choices=_SOURCE_CHOICES)
    run_p.add_argument("--cwd", default=None)
    run_p.add_argument("--model", default=None)
    run_p.add_argument("--timeout", type=float, default=None)
    run_p.add_argument("--json", action="store_true", help="Emit JSON output")
    run_p.add_argument("prompt")

    auth_p = sub.add_parser("auth", help="Auth helpers")
    auth_sub = auth_p.add_subparsers(dest="auth_command", required=True)

    status_p = auth_sub.add_parser("status", help="Show auth status")
    status_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    status_p.add_argument("--source", default="auto", choices=_SOURCE_CHOICES)
    status_p.add_argument("--json", action="store_true")

    login_p = auth_sub.add_parser("login", help="Run the provider's login flow")
    login_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    login_p.add_argument("--source", default="oauthpy", choices=_LOGIN_SOURCE_CHOICES)

    avail_p = sub.add_parser("available", help="Check if a provider is ready")
    avail_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)

    interactive_p = sub.add_parser("interactive", help="Start an interactive debug shell")
    _add_interactive_args(interactive_p)

    chat_p = sub.add_parser("chat", help="Alias for `interactive` chat mode")
    _add_interactive_args(chat_p)

    return parser


def _add_interactive_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    parser.add_argument("--source", default="auto", choices=_SOURCE_CHOICES)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--show-events", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "auth":
            if args.auth_command == "status":
                return _cmd_auth_status(args)
            if args.auth_command == "login":
                return _cmd_auth_login(args)
        if args.command == "available":
            return _cmd_available(args)
        if args.command in {"interactive", "chat"}:
            return _cmd_interactive(args)
    except OauthPyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover


def _cmd_run(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider), auth_source=cast(AuthSource, args.source))
    result = client.run(
        args.prompt,
        cwd=args.cwd,
        model=args.model,
        timeout=args.timeout,
    )
    if args.json:
        payload = {
            "provider": result.provider,
            "transport": result.transport,
            "model": result.model,
            "text": result.text,
            "elapsed_s": result.elapsed_s,
            "events": [
                {"kind": e.kind.value, "text": e.text, "timestamp": e.timestamp}
                for e in result.events
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(result.text)
    return 0


def _cmd_auth_status(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider), auth_source=cast(AuthSource, args.source))
    status = client.auth_status()
    if args.json:
        payload = {
            "provider": status.provider,
            "installed": status.installed,
            "authenticated": status.authenticated,
            "mode": status.mode,
            "details": status.details,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"provider={status.provider} installed={status.installed} "
            f"authenticated={status.authenticated} mode={status.mode}"
        )
        for k, v in status.details.items():
            print(f"  {k}: {v}")
    return 0 if status.authenticated else 1


def _cmd_auth_login(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider), auth_source=cast(AuthSource, args.source))
    client.login()
    return 0


def _cmd_available(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider))
    ready = client.available()
    print("yes" if ready else "no")
    return 0 if ready else 1


@dataclass
class _InteractiveState:
    provider: ProviderName
    source: AuthSource
    cwd: str | None = None
    model: str | None = None
    timeout: float | None = None
    show_events: bool = False
    transcript: list[tuple[str, str]] = field(default_factory=list)

    def client(self) -> Client:
        return Client(self.provider, auth_source=self.source)

    def prompt(self) -> str:
        return f"oauthpy[{self.provider}:{self.source}]> "


def _cmd_interactive(args: argparse.Namespace) -> int:
    state = _InteractiveState(
        provider=cast(ProviderName, args.provider),
        source=cast(AuthSource, args.source),
        cwd=args.cwd,
        model=args.model,
        timeout=args.timeout,
        show_events=bool(args.show_events),
    )
    print(
        "oauthpy interactive. Type /help for commands; /exit to quit.",
        file=sys.stderr,
    )
    while True:
        try:
            user_text = input(state.prompt())
        except EOFError:
            print("", file=sys.stderr)
            return 0
        except KeyboardInterrupt:
            print("^C", file=sys.stderr)
            continue
        if not user_text.strip():
            continue
        try:
            should_exit = _handle_interactive_line(state, user_text)
        except KeyboardInterrupt:
            print("^C", file=sys.stderr)
            continue
        except OauthPyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue
        if should_exit:
            return 0


def _handle_interactive_line(state: _InteractiveState, user_text: str) -> bool:
    stripped = user_text.strip()
    if stripped.startswith("/"):
        command, _, rest = stripped[1:].partition(" ")
        return _handle_slash_command(state, command.lower(), rest.strip())
    _run_chat_turn(state, stripped)
    return False


def _handle_slash_command(state: _InteractiveState, command: str, rest: str) -> bool:
    if command in {"exit", "quit"}:
        return True
    if command == "help":
        _print_interactive_help()
        return False
    if command == "status":
        _print_status(state.client().auth_status(), file=sys.stderr)
        return False
    if command == "available":
        ready = state.client().available()
        print("yes" if ready else "no", file=sys.stderr)
        return False
    if command == "login":
        source = _login_source_for_interactive(state, rest)
        if source is None:
            return False
        Client(state.provider, auth_source=source).login()
        print(f"login completed provider={state.provider} source={source}", file=sys.stderr)
        return False
    if command == "provider":
        if rest not in _PROVIDER_CHOICES:
            print(f"usage: /provider {'|'.join(_PROVIDER_CHOICES)}", file=sys.stderr)
            return False
        state.provider = cast(ProviderName, rest)
        state.transcript.clear()
        print(f"provider set to {state.provider}; transcript cleared", file=sys.stderr)
        return False
    if command == "source":
        if rest not in _SOURCE_CHOICES:
            print(f"usage: /source {'|'.join(_SOURCE_CHOICES)}", file=sys.stderr)
            return False
        state.source = cast(AuthSource, rest)
        state.transcript.clear()
        print(f"source set to {state.source}; transcript cleared", file=sys.stderr)
        return False
    if command == "cwd":
        if not rest:
            print(f"cwd={state.cwd or '.'}", file=sys.stderr)
        else:
            state.cwd = None if rest == "clear" else rest
            print(f"cwd={state.cwd or '.'}", file=sys.stderr)
        return False
    if command == "model":
        if not rest:
            print(f"model={state.model or '<default>'}", file=sys.stderr)
        else:
            state.model = None if rest == "clear" else rest
            print(f"model={state.model or '<default>'}", file=sys.stderr)
        return False
    if command == "timeout":
        _set_timeout(state, rest)
        return False
    if command == "events":
        _set_show_events(state, rest)
        return False
    if command == "clear":
        state.transcript.clear()
        print("transcript cleared", file=sys.stderr)
        return False
    if command == "run":
        if not rest:
            print("usage: /run PROMPT", file=sys.stderr)
        else:
            _run_one_shot(state, rest)
        return False
    if command == "stream":
        if not rest:
            print("usage: /stream PROMPT", file=sys.stderr)
        else:
            _stream_one_shot(state, rest)
        return False
    if command == "chat":
        if not rest:
            print("usage: /chat PROMPT", file=sys.stderr)
        else:
            _run_chat_turn(state, rest)
        return False
    print(f"unknown command: /{command}; type /help", file=sys.stderr)
    return False


def _print_status(status: AuthStatus, *, file: TextIO) -> None:
    print(
        f"provider={status.provider} installed={status.installed} "
        f"authenticated={status.authenticated} mode={status.mode}",
        file=file,
    )
    for key, value in status.details.items():
        print(f"  {key}: {value}", file=file)


def _print_interactive_help() -> None:
    print(
        "\n".join(
            [
                "Commands:",
                "  /help",
                "  /exit | /quit",
                "  /status",
                "  /available",
                "  /login [oauthpy|external]",
                "  /provider codex|claude",
                "  /source auto|oauthpy|external",
                "  /cwd PATH | /cwd clear",
                "  /model NAME | /model clear",
                "  /timeout SECONDS | /timeout clear",
                "  /events on|off",
                "  /clear",
                "  /run PROMPT",
                "  /stream PROMPT",
                "  /chat PROMPT",
                "Plain text sends a transcript-aware chat turn.",
            ]
        ),
        file=sys.stderr,
    )


def _login_source_for_interactive(state: _InteractiveState, rest: str) -> AuthSource | None:
    source = rest or ("oauthpy" if state.source == "auto" else state.source)
    if source not in _LOGIN_SOURCE_CHOICES:
        print(f"usage: /login {'|'.join(_LOGIN_SOURCE_CHOICES)}", file=sys.stderr)
        return None
    return cast(AuthSource, source)


def _set_timeout(state: _InteractiveState, rest: str) -> None:
    if not rest:
        timeout = state.timeout if state.timeout is not None else "<none>"
        print(f"timeout={timeout}", file=sys.stderr)
        return
    if rest == "clear":
        state.timeout = None
        print("timeout=<none>", file=sys.stderr)
        return
    try:
        timeout = float(rest)
    except ValueError:
        print("usage: /timeout SECONDS | /timeout clear", file=sys.stderr)
        return
    if timeout <= 0:
        print("timeout must be positive", file=sys.stderr)
        return
    state.timeout = timeout
    print(f"timeout={state.timeout}", file=sys.stderr)


def _set_show_events(state: _InteractiveState, rest: str) -> None:
    if rest == "on":
        state.show_events = True
    elif rest == "off":
        state.show_events = False
    else:
        print("usage: /events on|off", file=sys.stderr)
        return
    print(f"events={'on' if state.show_events else 'off'}", file=sys.stderr)


def _run_one_shot(state: _InteractiveState, prompt: str) -> None:
    result = state.client().run(
        prompt,
        cwd=state.cwd,
        model=state.model,
        timeout=state.timeout,
    )
    _print_run_result(state, result)


def _stream_one_shot(state: _InteractiveState, prompt: str) -> None:
    for event in state.client().stream_sync(
        prompt,
        cwd=state.cwd,
        model=state.model,
        timeout=state.timeout,
    ):
        text = f" {event.text}" if event.text else ""
        print(f"[{event.kind.value}]{text}")


def _run_chat_turn(state: _InteractiveState, user_text: str) -> None:
    prompt = _chat_prompt(state.transcript, user_text)
    result = state.client().run(
        prompt,
        cwd=state.cwd,
        model=state.model,
        timeout=state.timeout,
    )
    _print_run_result(state, result)
    state.transcript.append(("user", user_text))
    state.transcript.append(("assistant", result.text))


def _print_run_result(state: _InteractiveState, result: RunResult) -> None:
    if state.show_events:
        for event in result.events:
            if event.kind.value not in {"message", "done"}:
                print(f"[{event.kind.value}] {event.text or ''}", file=sys.stderr)
    print(f"{state.provider}> {result.text}")


def _chat_prompt(transcript: list[tuple[str, str]], user_text: str) -> str:
    if not transcript:
        return user_text
    parts = [
        "Continue this local oauthpy chat session.",
        "The transcript is maintained by oauthpy and may not match provider-native session state.",
        "",
        "Transcript:",
    ]
    for role, text in transcript:
        parts.append(f"{role}: {text}")
    parts.extend(["", f"user: {user_text}", "assistant:"])
    return "\n".join(parts)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
