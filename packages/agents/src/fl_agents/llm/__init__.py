"""LLM access through named roles only (CLAUDE.md rule 7, ADR-004)."""

from fl_agents.llm.errors import (
    BudgetExhausted,
    CassetteMiss,
    LlmError,
    LlmOutputInvalid,
    LlmUnavailable,
    PauseRequired,
    QuotaExhausted,
)
from fl_agents.llm.router import CallCtx, Router

__all__ = [
    "BudgetExhausted",
    "CallCtx",
    "CassetteMiss",
    "LlmError",
    "LlmOutputInvalid",
    "LlmUnavailable",
    "PauseRequired",
    "QuotaExhausted",
    "Router",
]
