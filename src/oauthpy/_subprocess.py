"""Cross-platform asyncio subprocess helpers.

Every call site in oauthpy goes through this module. We enforce:

* argv lists only (``shell=False``);
* UTF-8 decoding with ``errors="replace"`` (Windows cmd + PowerShell default
  encodings are a mess, and a codec error in the middle of a JSONL stream
  would otherwise crash the parser);
* a single timeout knob that kills the process tree on expiry.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from ._redact import redact_argv
from .errors import CommandExecutionError, TimeoutExceededError


@dataclass(frozen=True)
class CompletedProcess:
    """Result of a non-streaming subprocess call."""

    returncode: int
    stdout: str
    stderr: str


def which(binary: str) -> str | None:
    """Return the absolute path to ``binary`` on PATH, or ``None``."""

    return shutil.which(binary)


def _merge_env(extra: Mapping[str, str | None] | None) -> Mapping[str, str] | None:
    """Merge ``extra`` into ``os.environ`` without mutating the current process."""

    if extra is None:
        return None
    merged = dict(os.environ)
    for key, value in extra.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _process_group_kwargs() -> dict[str, object]:
    """Return subprocess kwargs that isolate children into a killable tree root."""

    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _resolve_argv0(argv: list[str]) -> list[str]:
    """Resolve ``argv[0]`` to an absolute path via :func:`shutil.which`.

    This matters on Windows, where npm installs CLIs as ``*.CMD`` / ``*.BAT``
    shims. ``asyncio.create_subprocess_exec`` with ``shell=False`` only finds
    those when given the full path with the extension. On POSIX this is a
    no-op for binaries already on PATH.

    If ``argv[0]`` is already an absolute path or we cannot resolve it, the
    argv is returned unchanged and the underlying ``FileNotFoundError`` is
    surfaced to the caller.
    """

    if not argv:
        return argv
    first = argv[0]
    if os.path.isabs(first):
        return argv
    resolved = shutil.which(first)
    if resolved is None:
        return argv
    return [resolved, *argv[1:]]


async def run(
    argv: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str | None] | None = None,
    timeout: float | None = None,
    stdin: str | None = None,
) -> CompletedProcess:
    """Run a subprocess to completion and return its captured output.

    Never uses a shell. ``argv[0]`` must be an existing binary on PATH.
    ``timeout`` is the wall-clock budget in seconds; on expiry the process is
    terminated (SIGTERM on POSIX, ``terminate()`` on Windows) and a
    :class:`TimeoutExceededError` is raised.
    """

    if not argv:
        raise CommandExecutionError("empty argv")
    resolved = _resolve_argv0(argv)
    try:
        proc = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=_merge_env(env),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_process_group_kwargs(),
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(
            f"binary not found: {argv[0]!r}",
            returncode=None,
        ) from exc
    except OSError as exc:
        raise CommandExecutionError(
            f"failed to start {redact_argv(resolved)!r}: {exc}",
            returncode=None,
        ) from exc

    stdin_bytes = stdin.encode("utf-8") if stdin is not None else None
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        await _kill(proc)
        raise TimeoutExceededError(
            f"command timed out after {timeout}s: {redact_argv(resolved)!r}"
        ) from exc

    return CompletedProcess(
        returncode=proc.returncode or 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )


async def run_interactive(
    argv: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str | None] | None = None,
    timeout: float | None = None,
) -> CompletedProcess:
    """Run a subprocess with inherited stdio for browser/device login flows."""

    if not argv:
        raise CommandExecutionError("empty argv")
    resolved = _resolve_argv0(argv)
    try:
        proc = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=_merge_env(env),
            **_process_group_kwargs(),
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(
            f"binary not found: {argv[0]!r}",
            returncode=None,
        ) from exc
    except OSError as exc:
        raise CommandExecutionError(
            f"failed to start {redact_argv(resolved)!r}: {exc}",
            returncode=None,
        ) from exc

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill(proc)
        raise TimeoutExceededError(
            f"command timed out after {timeout}s: {redact_argv(resolved)!r}"
        ) from exc
    return CompletedProcess(returncode=proc.returncode or 0, stdout="", stderr="")


async def stream_lines(
    argv: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str | None] | None = None,
    timeout: float | None = None,
) -> AsyncIterator[str]:
    """Run a subprocess and yield stdout lines as they arrive.

    The trailing newline is stripped. When the process exits with a non-zero
    code, a :class:`CommandExecutionError` is raised *after* the iterator is
    exhausted so callers see the full stream first.

    ``timeout`` applies to the whole run (not per-line).
    """

    if not argv:
        raise CommandExecutionError("empty argv")
    resolved = _resolve_argv0(argv)
    try:
        proc = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=_merge_env(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_process_group_kwargs(),
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(
            f"binary not found: {argv[0]!r}",
            returncode=None,
        ) from exc
    except OSError as exc:
        raise CommandExecutionError(
            f"failed to start {redact_argv(resolved)!r}: {exc}",
            returncode=None,
        ) from exc

    assert proc.stdout is not None
    assert proc.stderr is not None

    # Drain stderr concurrently with stdout so the pipe cannot deadlock.
    # Bound at ~64 KiB — more than enough for a diagnostic tail, and prevents
    # a chatty subprocess from ballooning memory over a long run.
    stderr_limit = 64 * 1024

    async def _read_stderr() -> bytes:
        chunks: bytearray = bytearray()
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(8192)
            if not chunk:
                break
            remaining = stderr_limit - len(chunks)
            if remaining <= 0:
                continue
            chunks.extend(chunk[:remaining])
        return bytes(chunks)

    stderr_task = asyncio.create_task(_read_stderr())

    async def _iterate() -> AsyncIterator[str]:
        while True:
            line = await proc.stdout.readline()  # type: ignore[union-attr]
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip("\r\n")

    async def _guarded() -> AsyncIterator[str]:
        killed = False
        completed = False
        try:
            if timeout is None:
                async for line in _iterate():
                    yield line
                completed = True
            else:
                deadline = asyncio.get_event_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        killed = True
                        await _kill(proc)
                        raise TimeoutExceededError(
                            f"command timed out after {timeout}s: {redact_argv(resolved)!r}"
                        )
                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(),  # type: ignore[union-attr]
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError as exc:
                        killed = True
                        await _kill(proc)
                        raise TimeoutExceededError(
                            f"command timed out after {timeout}s: {redact_argv(resolved)!r}"
                        ) from exc
                    if not line:
                        completed = True
                        break
                    yield line.decode("utf-8", errors="replace").rstrip("\r\n")
        finally:
            if completed:
                await proc.wait()
            elif proc.returncode is None:
                killed = True
                await _kill(proc)
                await proc.wait()
            stderr_bytes = await stderr_task
            if proc.returncode not in (0, None) and not killed:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                raise CommandExecutionError(
                    f"{argv[0]!r} exited with code {proc.returncode}",
                    returncode=proc.returncode,
                    stderr=stderr_text,
                )

    async for line in _guarded():
        yield line


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Best-effort terminate the whole process tree rooted at ``proc``."""

    if proc.returncode is not None:
        return
    if os.name == "nt":
        await _kill_windows(proc)
    else:
        await _kill_posix(proc)


async def _kill_posix(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            await proc.wait()
        except ProcessLookupError:  # pragma: no cover - race on exit
            return


async def _kill_windows(proc: asyncio.subprocess.Process) -> None:
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(proc.pid),
            "/T",
            "/F",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=5.0)
    except (FileNotFoundError, OSError, asyncio.TimeoutError):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
    try:
        await proc.wait()
    except ProcessLookupError:  # pragma: no cover - race on exit
        return


__all__ = ["CompletedProcess", "run", "run_interactive", "stream_lines", "which"]
