"""oauthpy — local, user-operated Python API over coding agents.

Public surface:

.. code-block:: python

    from oauthpy import Client

    client = Client("codex")
    result = client.run("Summarize this repo", cwd=".")

Everything in this top-level namespace is considered stable API for v0.1.
Provider-specific switches live on ``Client(...).run(provider_options=...)``
rather than on the top-level signature.
"""

from __future__ import annotations

from .client import Client
from .errors import (
    AuthRequiredError,
    CommandExecutionError,
    OauthPyError,
    ProtocolError,
    ProviderNotInstalledError,
    TimeoutExceededError,
    UnsupportedProviderError,
)
from .models import (
    AuthMode,
    AuthSource,
    AuthStatus,
    Event,
    EventKind,
    JsonScalar,
    ProviderName,
    RunResult,
    TransportName,
    Usage,
)

try:  # pragma: no cover - generated at build time
    from ._version import version as __version__
except ImportError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "AuthMode",
    "AuthSource",
    "AuthRequiredError",
    "AuthStatus",
    "Client",
    "CommandExecutionError",
    "Event",
    "EventKind",
    "JsonScalar",
    "OauthPyError",
    "ProtocolError",
    "ProviderName",
    "ProviderNotInstalledError",
    "RunResult",
    "TimeoutExceededError",
    "TransportName",
    "UnsupportedProviderError",
    "Usage",
    "__version__",
]
