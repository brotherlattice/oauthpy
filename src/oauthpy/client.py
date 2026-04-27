"""Public ``Client`` — async-core API with a thin sync facade.

``Client.run`` and ``Client.auth_status`` / ``Client.login`` return awaitables
when called from inside a running event loop, and block-on-execute when called
from synchronous code. ``Client.stream`` is always an async iterator;
``Client.stream_sync`` adapts it for sync code.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import queue
import threading
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import suppress
from typing import Any, get_args, overload

from .auth import AuthBackend, normalize_auth_source
from .errors import UnsupportedProviderError
from .models import AuthSource, AuthStatus, Event, ProviderName, RunResult
from .providers.base import Provider
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .providers.gemini import GeminiProvider

_PROVIDER_REGISTRY: dict[ProviderName, type[Provider]] = {
    "codex": CodexProvider,
    "claude": ClaudeProvider,
    "gemini": GeminiProvider,
}
_PROVIDERS: tuple[ProviderName, ...] = get_args(ProviderName)


def _in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync code.

    Uses ``asyncio.run`` when no loop is running. Inside an existing loop we
    run the coroutine on a dedicated background thread with its own loop —
    calling ``asyncio.run`` inside a running loop raises, and we want the sync
    facade to "just work" from a REPL too.
    """

    if not _in_event_loop():
        return asyncio.run(coro)

    result_box: list[Any] = []
    error_box: list[BaseException] = []

    def _target() -> None:
        loop = asyncio.new_event_loop()
        try:
            result_box.append(loop.run_until_complete(coro))
        except BaseException as exc:  # pragma: no cover - forwarded
            error_box.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if error_box:
        raise error_box[0]
    return result_box[0]


class Client:
    """Public entrypoint: ``Client("codex")`` or ``Client("claude")``."""

    def __init__(
        self,
        provider: ProviderName,
        *,
        auth_source: AuthSource = "auto",
        oauthpy_home: str | os.PathLike[str] | None = None,
        auth_backend: AuthBackend | None = None,
    ) -> None:
        if provider not in _PROVIDERS:
            raise UnsupportedProviderError(
                f"unknown provider {provider!r}; expected one of {_PROVIDERS}"
            )
        self.provider: ProviderName = provider
        normalized_source = normalize_auth_source(auth_source)
        self._adapter: Provider = _PROVIDER_REGISTRY[provider](
            auth_source=normalized_source,
            oauthpy_home=oauthpy_home,
        )
        # Only materialize a backend when the caller injects one; otherwise
        # route auth calls directly to the adapter and skip a hop.
        self._auth_backend: AuthBackend | None = auth_backend

    @overload
    def available(self) -> bool: ...
    @overload
    def available(self, *, _async: bool = ...) -> Any: ...

    def available(self, *, _async: bool = False) -> Any:
        """Whether the provider is installed and authenticated.

        Synchronous by default. Awaiting the result also works from async
        code: ``await Client("codex").available(_async=True)``.
        """

        coro = self._adapter.available()
        if _async or _in_event_loop():
            return coro
        return _run_sync(coro)

    def auth_status(self) -> AuthStatus | Any:
        """Return a best-effort :class:`AuthStatus` for this provider."""

        if self._auth_backend is not None:
            coro = self._auth_backend.status(self.provider)
        else:
            coro = self._adapter.auth_status()
        if _in_event_loop():
            return coro
        return _run_sync(coro)

    def login(self) -> None | Any:
        """Shell out to the provider's official login flow."""

        if self._auth_backend is not None:
            coro = self._auth_backend.login(self.provider)
        else:
            coro = self._adapter.login()
        if _in_event_loop():
            return coro
        return _run_sync(coro)

    def run(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> RunResult | Any:
        """One-shot prompt execution. Returns a :class:`RunResult`.

        In synchronous code this blocks. Inside a running event loop it returns
        a coroutine so you can ``await`` it.
        """

        coro = self._adapter.run(
            prompt,
            cwd=cwd,
            model=model,
            timeout=timeout,
            env=env,
            provider_options=provider_options,
        )
        if _in_event_loop():
            return coro
        return _run_sync(coro)

    def stream(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        """Stream normalized events. Always an async iterator.

        ``stream`` is async-only: call :meth:`stream_sync` for sync code.
        """

        raw = self._adapter.stream(
            prompt,
            cwd=cwd,
            model=model,
            timeout=timeout,
            env=env,
            provider_options=provider_options,
        )
        # Some provider implementations return a coroutine-that-yields-an-iterator
        # (e.g., Claude SDK shape). Unwrap here so callers always get an
        # AsyncIterator directly.
        if inspect.iscoroutine(raw):

            async def _unwrap() -> AsyncIterator[Event]:
                it = await raw
                async for ev in it:
                    yield ev

            return _unwrap()
        return raw

    def stream_sync(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        provider_options: Mapping[str, Any] | None = None,
    ) -> Iterator[Event]:
        """Synchronous facade over :meth:`stream`.

        Drains the async iterator on a background thread. Closing this iterator
        early signals the worker loop to close the underlying async stream,
        which lets provider subprocesses terminate promptly.
        """

        sentinel = object()
        q: queue.Queue[Any] = queue.Queue()
        stop_requested = threading.Event()

        async def _drain() -> None:
            iterator = self.stream(
                prompt,
                cwd=cwd,
                model=model,
                timeout=timeout,
                env=env,
                provider_options=provider_options,
            ).__aiter__()
            next_task: asyncio.Task[Event] | None = None
            try:
                while not stop_requested.is_set():
                    if next_task is None:
                        next_task = asyncio.create_task(iterator.__anext__())
                    done, _ = await asyncio.wait({next_task}, timeout=0.1)
                    if not done:
                        continue
                    try:
                        q.put(next_task.result())
                    except StopAsyncIteration:
                        return
                    next_task = None
            except BaseException as exc:  # pragma: no cover - forwarded
                if not stop_requested.is_set():
                    q.put(("__error__", exc))
            finally:
                if next_task is not None and not next_task.done():
                    next_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_task
                aclose = getattr(iterator, "aclose", None)
                if aclose is not None:
                    await aclose()
                q.put(sentinel)

        def _target() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_drain())
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()

        try:
            while True:
                item = q.get()
                if item is sentinel:
                    return
                if isinstance(item, tuple) and item[0] == "__error__":
                    raise item[1]
                yield item
        finally:
            stop_requested.set()
            thread.join(timeout=5.0)


__all__ = ["Client"]
