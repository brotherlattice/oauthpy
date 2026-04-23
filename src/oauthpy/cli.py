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
from .models import ProviderName

_PROVIDER_CHOICES = get_args(ProviderName)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oauthpy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a one-shot prompt")
    run_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    run_p.add_argument("--cwd", default=None)
    run_p.add_argument("--model", default=None)
    run_p.add_argument("--timeout", type=float, default=None)
    run_p.add_argument("--json", action="store_true", help="Emit JSON output")
    run_p.add_argument("prompt")

    auth_p = sub.add_parser("auth", help="Auth helpers")
    auth_sub = auth_p.add_subparsers(dest="auth_command", required=True)

    status_p = auth_sub.add_parser("status", help="Show auth status")
    status_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)
    status_p.add_argument("--json", action="store_true")

    login_p = auth_sub.add_parser("login", help="Run the provider's login flow")
    login_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)

    avail_p = sub.add_parser("available", help="Check if a provider is ready")
    avail_p.add_argument("--provider", required=True, choices=_PROVIDER_CHOICES)

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
    except OauthPyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover


def _cmd_run(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider))
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
    client = Client(cast(ProviderName, args.provider))
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
    client = Client(cast(ProviderName, args.provider))
    client.login()
    return 0


def _cmd_available(args: argparse.Namespace) -> int:
    client = Client(cast(ProviderName, args.provider))
    ready = client.available()
    print("yes" if ready else "no")
    return 0 if ready else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
