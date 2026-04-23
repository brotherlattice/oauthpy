"""Provider adapters for oauthpy.

Each provider implements :class:`oauthpy.providers.base.Provider` and is
instantiated by :class:`oauthpy.client.Client` when a caller selects it.
"""

from __future__ import annotations

from .base import Provider

__all__ = ["Provider"]
