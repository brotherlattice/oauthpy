# Development

## Clone + install

```bash
git clone https://github.com/brotherlattice/oauthpy.git
cd oauthpy
python -m pip install -e .[dev]
pre-commit install
```

Python 3.10+ is required. The matrix CI runs against 3.10, 3.11, 3.12, and 3.13 on Ubuntu, Windows, and macOS.

## Run tests

Offline (default):

```bash
pytest
# equivalent to: pytest -m "not live_codex and not live_claude"
```

Offline tests are intentionally hermetic. They must not depend on a real
`codex` binary, a real Claude Code install, network access, existing OAuth
sessions, or writable provider home directories. Provider tests fake the
official local integration boundary instead:

- Codex tests fake `codex login status` and `codex exec --json` JSONL output.
- Claude tests fake `claude auth status --json` and the `claude-agent-sdk`
  message stream.
- Missing-provider tests assert the public behavior: `auth_status()` reports
  unauthenticated diagnostics, `available()` is false, and run/login paths raise
  `ProviderNotInstalledError` when the required local tool is absent.

If you need to verify a real vendor CLI or OAuth session, use the live markers
below rather than weakening offline tests.

Live tests require real provider setups and are opt-in:

```bash
OAUTHPY_LIVE_CODEX=1 pytest -m live_codex
OAUTHPY_LIVE_CLAUDE=1 pytest -m live_claude
```

## Lint and format

```bash
ruff check .
ruff format .
```

Pre-commit runs both plus a handful of standard hooks (trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-merge-conflict, debug-statements).

## Docs

```bash
python -m pip install -e .[docs]
sphinx-build -b html docs docs/_build/html
open docs/_build/html/index.html  # macOS
start docs\_build\html\index.html # Windows
```

The docs site is configured for Read the Docs deployment via `.readthedocs.yaml`.

## Release

The package uses `setuptools-scm` — the version is derived from the newest Git tag, with a `0.1.0` fallback for untagged checkouts.

To cut a release:

1. Update the changelog if you keep one.
2. Tag and push: `git tag vX.Y.Z && git push --tags`.
3. GitHub Actions `pypi-release.yml` runs the test matrix, builds sdist and wheel, smoke-tests both, and publishes to PyPI via OIDC.

## Project layout

```
src/oauthpy/
  __init__.py       # public surface: Client, models, errors, __version__
  client.py         # Client: async core + sync facade
  models.py         # RunResult, Event, AuthStatus, enums
  errors.py         # OauthPyError hierarchy
  auth.py           # AuthBackend Protocol + SubprocessAuthBackend
  cli.py            # debugging CLI (oauthpy run | auth login | auth status | available)
  _subprocess.py    # async subprocess helpers (argv-only, shell=False)
  _redact.py        # secret redaction
  providers/
    base.py         # Provider ABC
    codex.py        # codex exec --json JSONL adapter
    claude.py       # claude-agent-sdk adapter

tests/              # offline unit tests + live/ opt-in smoke tests
docs/               # Sphinx + MyST
examples/           # runnable demos
```
