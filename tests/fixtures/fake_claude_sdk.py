"""In-memory fake of the relevant surface of ``claude_agent_sdk``.

Used by tests to exercise :class:`oauthpy.providers.claude.ClaudeProvider`
without importing the real SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaudeAgentOptions:
    cwd: str | None = None
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        self.cwd = kwargs.pop("cwd", None)
        self.model = kwargs.pop("model", None)
        self.env = kwargs.pop("env", {})
        self.allowed_tools = kwargs.pop("allowed_tools", [])
        self.extra = kwargs


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any] | None = None


@dataclass
class AssistantMessage:
    content: list[Any]


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | None = None
    is_error: bool | None = None


@dataclass
class ResultMessage:
    result: str
    usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    is_error: bool = False


def make_query(messages: list[Any]) -> Any:
    """Return a ``query`` callable that yields the given messages once."""

    async def _query(*, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        for m in messages:
            yield m

    return _query


def default_messages(prompt_echo: str = "ok") -> list[Any]:
    return [
        SystemMessage(subtype="init", data={"session_id": "fake-session"}),
        AssistantMessage(content=[TextBlock(text=f"I will: {prompt_echo}")]),
        ResultMessage(result=prompt_echo),
    ]
