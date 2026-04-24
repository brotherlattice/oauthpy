"""Tests for :mod:`oauthpy._subprocess`.

We use the current Python interpreter as the "binary" so these tests work on
Windows, Linux, and macOS without requiring extra tooling.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from oauthpy import CommandExecutionError, TimeoutExceededError
from oauthpy._subprocess import run, stream_lines, which


@pytest.fixture(autouse=True)
def _disable_fd_capture_for_async_subprocesses(capfd: pytest.CaptureFixture[str]):
    """Avoid pytest fd-capture deadlocks with asyncio subprocess watchers."""

    with capfd.disabled():
        yield


async def test_run_captures_stdout() -> None:
    result = await run([sys.executable, "-c", "print('hello from subprocess')"])
    assert result.returncode == 0
    assert "hello from subprocess" in result.stdout


async def test_run_captures_nonzero_returncode() -> None:
    result = await run([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert result.returncode == 7


async def test_run_raises_on_missing_binary() -> None:
    with pytest.raises(CommandExecutionError):
        await run(["__definitely_not_a_real_binary_xyz__"])


async def test_run_raises_on_empty_argv() -> None:
    with pytest.raises(CommandExecutionError):
        await run([])


async def test_run_timeout() -> None:
    with pytest.raises(TimeoutExceededError):
        await run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.2,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group assertion")
async def test_run_timeout_kills_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid), encoding='utf-8')\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(TimeoutExceededError):
        await run([sys.executable, "-c", script], timeout=0.2)
    await asyncio.sleep(0.2)
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_stream_lines_yields_each_line() -> None:
    script = "import sys, time\n" "for i in range(3):\n" "    print(f'line {i}', flush=True)\n"
    lines = []
    async for line in stream_lines([sys.executable, "-c", script]):
        lines.append(line)
    assert lines == ["line 0", "line 1", "line 2"]


async def test_stream_lines_handles_windows_style_paths() -> None:
    # A path containing backslashes should pass through unchanged.
    script = r"print(r'C:\Users\alice\project')"
    lines = []
    async for line in stream_lines([sys.executable, "-c", script]):
        lines.append(line)
    assert lines == ["C:\\Users\\alice\\project"]


async def test_stream_lines_raises_on_nonzero_exit() -> None:
    script = "import sys; print('hi'); sys.exit(3)"
    lines = []
    with pytest.raises(CommandExecutionError):
        async for line in stream_lines([sys.executable, "-c", script]):
            lines.append(line)
    assert lines == ["hi"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group assertion")
async def test_stream_lines_early_close_kills_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "stream-child.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid), encoding='utf-8')\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    lines = []
    async for line in stream_lines([sys.executable, "-c", script]):
        lines.append(line)
        break
    assert lines == ["ready"]
    await asyncio.sleep(0.2)
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_which_finds_python() -> None:
    assert which(sys.executable) is not None or which("python") is not None
