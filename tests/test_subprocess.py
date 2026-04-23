"""Tests for :mod:`oauthpy._subprocess`.

We use the current Python interpreter as the "binary" so these tests work on
Windows, Linux, and macOS without requiring extra tooling.
"""

from __future__ import annotations

import sys

import pytest

from oauthpy import CommandExecutionError, TimeoutExceededError
from oauthpy._subprocess import run, stream_lines, which


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


def test_which_finds_python() -> None:
    assert which(sys.executable) is not None or which("python") is not None
