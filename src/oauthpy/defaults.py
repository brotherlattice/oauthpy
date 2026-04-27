"""Provider defaults and CLI helper values."""

from __future__ import annotations

DEFAULT_CODEX_REASONING_EFFORT = "low"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_REASONING_EFFORT = "low"
DEFAULT_GEMINI_MODEL = "auto"

CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
CLAUDE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
REASONING_EFFORTS = tuple(dict.fromkeys((*CODEX_REASONING_EFFORTS, *CLAUDE_REASONING_EFFORTS)))

CODEX_MODEL_EXAMPLES = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "codex-mini-latest",
)
CLAUDE_MODEL_ALIASES = (
    "default",
    "best",
    "sonnet",
    "opus",
    "haiku",
    "sonnet[1m]",
    "opus[1m]",
    "opusplan",
)
GEMINI_MODEL_EXAMPLES = (
    "auto",
    "pro",
    "flash",
    "flash-lite",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)


__all__ = [
    "CLAUDE_MODEL_ALIASES",
    "CLAUDE_REASONING_EFFORTS",
    "CODEX_MODEL_EXAMPLES",
    "CODEX_REASONING_EFFORTS",
    "DEFAULT_CLAUDE_MODEL",
    "DEFAULT_CLAUDE_REASONING_EFFORT",
    "DEFAULT_CODEX_REASONING_EFFORT",
    "DEFAULT_GEMINI_MODEL",
    "GEMINI_MODEL_EXAMPLES",
    "REASONING_EFFORTS",
]
