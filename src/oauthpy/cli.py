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
from .defaults import (
    CLAUDE_MODEL_ALIASES,
    CLAUDE_REASONING_EFFORTS,
    CODEX_MODEL_EXAMPLES,
    CODEX_REASONING_EFFORTS,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CLAUDE_REASONING_EFFORT,
    DEFAULT_CODEX_REASONING_EFFORT,
    REASONING_EFFORTS,
)
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
    run_p.add_argument("--reasoning-effort", default=None, choices=REASONING_EFFORTS)
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
    parser.add_argument("--reasoning-effort", default=None, choices=REASONING_EFFORTS)
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
        provider_options=_provider_options(
            cast(ProviderName, args.provider), args.reasoning_effort
        ),
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
    reasoning_effort: str | None = None
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
        reasoning_effort=args.reasoning_effort,
        timeout=args.timeout,
        show_events=bool(args.show_events),
    )
    print(
        _interactive_banner(),
        file=sys.stderr,
    )
    while True:
        try:
            user_text = _read_interactive_input(state.prompt())
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
        command = _resolve_slash_command(command.lower())
        if command is None:
            return False
        return _handle_slash_command(state, command.lower(), rest.strip())
    _run_chat_turn(state, stripped)
    return False


def _resolve_slash_command(command: str) -> str | None:
    if command in _slash_commands():
        return command
    matches = [known for known in _slash_commands() if known.startswith(command)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(
            f"ambiguous command: /{command}; matches: "
            + ", ".join(f"/{match}" for match in matches),
            file=sys.stderr,
        )
        return None
    print(f"unknown command: /{command}; type /help", file=sys.stderr)
    return None


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
    if command == "models":
        _print_model_help(state.provider, file=sys.stderr)
        return False
    if command == "efforts":
        _print_effort_help(state.provider, file=sys.stderr)
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
            print(_model_status(state), file=sys.stderr)
        else:
            state.model = None if rest == "clear" else rest
            print(_model_status(state), file=sys.stderr)
        return False
    if command in {"effort", "reasoning"}:
        _set_reasoning_effort(state, rest)
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
                "  /models",
                "  /effort LEVEL | /effort clear",
                "  /reasoning LEVEL | /reasoning clear",
                "  /efforts",
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


def _model_status(state: _InteractiveState) -> str:
    if state.model:
        return f"model={state.model}"
    if state.provider == "claude":
        return f"model=<default {DEFAULT_CLAUDE_MODEL}>"
    return "model=<provider default>"


def _set_reasoning_effort(state: _InteractiveState, rest: str) -> None:
    if not rest:
        print(_reasoning_status(state), file=sys.stderr)
        return
    if rest == "clear":
        state.reasoning_effort = None
        print(_reasoning_status(state), file=sys.stderr)
        return
    valid = CLAUDE_REASONING_EFFORTS if state.provider == "claude" else CODEX_REASONING_EFFORTS
    if rest not in valid:
        print(f"usage: /effort {'|'.join(valid)} | /effort clear", file=sys.stderr)
        return
    state.reasoning_effort = rest
    print(_reasoning_status(state), file=sys.stderr)


def _reasoning_status(state: _InteractiveState) -> str:
    if state.reasoning_effort:
        return f"reasoning_effort={state.reasoning_effort}"
    if state.provider == "claude":
        return f"reasoning_effort=<default {DEFAULT_CLAUDE_REASONING_EFFORT}>"
    return f"reasoning_effort=<default {DEFAULT_CODEX_REASONING_EFFORT}>"


def _print_model_help(provider: ProviderName, *, file: TextIO) -> None:
    if provider == "claude":
        print(
            "Claude model aliases: "
            + ", ".join(CLAUDE_MODEL_ALIASES)
            + f" (oauthpy default: {DEFAULT_CLAUDE_MODEL})",
            file=file,
        )
        print("Use /model NAME, /model clear, or start with --model NAME.", file=file)
        return
    print(
        "Codex model examples: "
        + ", ".join(CODEX_MODEL_EXAMPLES)
        + " (oauthpy otherwise lets Codex choose its provider default)",
        file=file,
    )
    print("Use /model NAME, /model clear, or start with --model NAME.", file=file)


def _print_effort_help(provider: ProviderName, *, file: TextIO) -> None:
    if provider == "claude":
        print(
            "Claude effort levels: "
            + ", ".join(CLAUDE_REASONING_EFFORTS)
            + f" (oauthpy default: {DEFAULT_CLAUDE_REASONING_EFFORT})",
            file=file,
        )
        return
    print(
        "Codex reasoning efforts: "
        + ", ".join(CODEX_REASONING_EFFORTS)
        + f" (oauthpy default: {DEFAULT_CODEX_REASONING_EFFORT})",
        file=file,
    )


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
        provider_options=_provider_options(state.provider, state.reasoning_effort),
    )
    _print_run_result(state, result)


def _stream_one_shot(state: _InteractiveState, prompt: str) -> None:
    for event in state.client().stream_sync(
        prompt,
        cwd=state.cwd,
        model=state.model,
        timeout=state.timeout,
        provider_options=_provider_options(state.provider, state.reasoning_effort),
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
        provider_options=_provider_options(state.provider, state.reasoning_effort),
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


def _provider_options(
    provider: ProviderName, reasoning_effort: str | None
) -> dict[str, str] | None:
    if reasoning_effort is None:
        return None
    valid = CLAUDE_REASONING_EFFORTS if provider == "claude" else CODEX_REASONING_EFFORTS
    if reasoning_effort not in valid:
        raise OauthPyError(
            f"reasoning effort {reasoning_effort!r} is not valid for {provider}; "
            f"expected one of {', '.join(valid)}"
        )
    return {"reasoning_effort": reasoning_effort}


def _read_interactive_input(message: str) -> str:
    if not sys.stdin.isatty():
        return input(message)
    prompt_toolkit_input = _prompt_toolkit_input()
    if prompt_toolkit_input is not None:
        return prompt_toolkit_input(message)
    readline_input = _readline_input()
    if readline_input is not None:
        return readline_input(message)
    return input(message)


def _interactive_banner() -> str:
    base = "oauthpy interactive. Type /help for commands; /exit to quit."
    backend = _completion_backend()
    if backend == "prompt_toolkit":
        return base + " Press Tab to complete /commands."
    if backend == "readline":
        return base + " Press Tab to complete /commands."
    return base + " Tab completion unavailable; install prompt-toolkit or reinstall oauthpy."


def _completion_backend() -> str:
    if _prompt_toolkit_input() is not None:
        return "prompt_toolkit"
    if _readline_input() is not None:
        return "readline"
    return "none"


def _prompt_toolkit_input():
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.shortcuts import CompleteStyle
    except ImportError:
        return None

    class SlashCompleter(Completer):
        def get_completions(self, document: object, _complete_event: object):
            text = getattr(document, "text_before_cursor", "")
            for value, start_position in _completion_matches(text):
                yield Completion(value, start_position=start_position)

    def _read(message: str) -> str:
        return prompt(
            message,
            completer=SlashCompleter(),
            complete_style=CompleteStyle.MULTI_COLUMN,
            complete_while_typing=True,
        )

    return _read


def _readline_input():
    try:
        import readline
    except ImportError:
        return None

    def _complete(_text: str, state: int) -> str | None:
        line = readline.get_line_buffer()
        end = readline.get_endidx()
        candidates = [value for value, _ in _completion_matches(line[:end])]
        return candidates[state] if state < len(candidates) else None

    def _read(message: str) -> str:
        old_completer = readline.get_completer()
        old_delims = readline.get_completer_delims()
        try:
            readline.set_completer(_complete)
            # Keep "/" as part of the token so completing "/h" replaces it with "/help".
            readline.set_completer_delims(" \t\n")
            readline.parse_and_bind("tab: complete")
            return input(message)
        finally:
            readline.set_completer(old_completer)
            readline.set_completer_delims(old_delims)

    return _read


def _completion_matches(text_before_cursor: str) -> list[tuple[str, int]]:
    if not text_before_cursor.startswith("/"):
        return []
    tree = _completion_tree()
    if " " not in text_before_cursor:
        return [
            (command, -len(text_before_cursor))
            for command in tree
            if command.startswith(text_before_cursor)
        ]

    command, _, rest = text_before_cursor.partition(" ")
    values = tree.get(command)
    if not isinstance(values, tuple):
        return []
    token = rest.rsplit(" ", 1)[-1]
    return [(value, -len(token)) for value in values if value.startswith(token)]


def _completion_tree() -> dict[str, tuple[str, ...] | None]:
    model_values = (*CODEX_MODEL_EXAMPLES, *CLAUDE_MODEL_ALIASES, "clear")
    return {
        "/help": None,
        "/exit": None,
        "/quit": None,
        "/status": None,
        "/available": None,
        "/models": None,
        "/efforts": None,
        "/login": _LOGIN_SOURCE_CHOICES,
        "/provider": _PROVIDER_CHOICES,
        "/source": _SOURCE_CHOICES,
        "/cwd": ("clear",),
        "/model": model_values,
        "/effort": (*REASONING_EFFORTS, "clear"),
        "/reasoning": (*REASONING_EFFORTS, "clear"),
        "/timeout": ("clear",),
        "/events": ("on", "off"),
        "/clear": None,
        "/run": None,
        "/stream": None,
        "/chat": None,
    }


def _slash_commands() -> tuple[str, ...]:
    return tuple(command[1:] for command in _completion_tree())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
