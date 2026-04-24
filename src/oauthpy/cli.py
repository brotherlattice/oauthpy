"""Tiny debugging CLI.

Usage:

.. code-block:: bash

    oauthpy run --provider codex "summarize this repo"
    oauthpy auth login --provider claude
    oauthpy auth status --provider codex
    oauthpy available --provider codex

The CLI is deliberately thin — the library is the product.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import cast, get_args

from .client import Client
from .errors import OauthPyError
from .models import AuthSource, ProviderName

_PROVIDER_CHOICES = get_args(ProviderName)
_SOURCE_CHOICES = get_args(AuthSource)
_LOGIN_SOURCE_CHOICES = ("oauthpy", "external")


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oauthpy", description=__doc__)
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

    chat_p = sub.add_parser("chat", help="Experimental local in-memory chat")
    chat_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    chat_p.add_argument("--source", default="auto", choices=_SOURCE_CHOICES)
    chat_p.add_argument("--cwd", default=None)
    chat_p.add_argument("--model", default=None)
    chat_p.add_argument("--timeout", type=float, default=None)
    chat_p.add_argument("--show-events", action="store_true")

    return parser


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
        if args.command == "chat":
            return _cmd_chat(args)
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


def _cmd_chat(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider), auth_source=cast(AuthSource, args.source))
    transcript: list[tuple[str, str]] = []
    print(
        "oauthpy experimental chat. Commands: /help, /status, /clear, /exit.",
        file=sys.stderr,
    )
    while True:
        try:
            user_text = input("you> ")
        except EOFError:
            print("", file=sys.stderr)
            return 0
        if not user_text.strip():
            continue
        command = user_text.strip().lower()
        if command in {"/exit", "/quit"}:
            return 0
        if command == "/help":
            print("Commands: /help, /status, /clear, /exit", file=sys.stderr)
            continue
        if command == "/clear":
            transcript.clear()
            print("cleared", file=sys.stderr)
            continue
        if command == "/status":
            status = client.auth_status()
            print(
                f"provider={status.provider} installed={status.installed} "
                f"authenticated={status.authenticated} mode={status.mode}",
                file=sys.stderr,
            )
            for key, value in status.details.items():
                print(f"  {key}: {value}", file=sys.stderr)
            continue

        prompt = _chat_prompt(transcript, user_text)
        result = client.run(
            prompt,
            cwd=args.cwd,
            model=args.model,
            timeout=args.timeout,
        )
        if args.show_events:
            for event in result.events:
                if event.kind.value not in {"message", "done"}:
                    print(f"[{event.kind.value}] {event.text or ''}", file=sys.stderr)
        print(f"{args.provider}> {result.text}")
        transcript.append(("user", user_text))
        transcript.append(("assistant", result.text))
    return 0


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
