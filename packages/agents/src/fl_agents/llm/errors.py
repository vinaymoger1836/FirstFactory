"""Router errors. Budget and quota errors pause a stage (resumable); others fail it."""

from __future__ import annotations

__all__ = [
    "BudgetExhausted",
    "CassetteMiss",
    "LlmError",
    "LlmOutputInvalid",
    "LlmUnavailable",
    "PauseRequired",
    "QuotaExhausted",
]


class LlmError(RuntimeError):
    """Base class for router errors."""


class PauseRequired(LlmError):
    """The stage must pause and wait for the user (budget or provider quota)."""

    kind = "pause"

    def payload(self) -> dict[str, object]:
        return {"kind": self.kind, "message": str(self)}


class BudgetExhausted(PauseRequired):
    """The next call would exceed the stage's token budget (docs/02 §4)."""

    kind = "budget"

    def __init__(self, used: int, budget: int, estimate: int) -> None:
        super().__init__(
            f"The next model call needs about {estimate} tokens; this stage has used "
            f"{used} of its {budget}-token budget."
        )
        self.used, self.budget, self.estimate = used, budget, estimate

    def payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": str(self),
            "used": self.used,
            "budget": self.budget,
            "estimate": self.estimate,
        }


class QuotaExhausted(PauseRequired):
    """The provider kept refusing (429) after retries and fallbacks (docs/02 §7)."""

    kind = "quota"

    def __init__(self, role: str, retry_after_s: float | None) -> None:
        wait = f" Try again in about {round(retry_after_s)} s." if retry_after_s else ""
        super().__init__(f"The model provider's free quota for '{role}' is used up.{wait}")
        self.role, self.retry_after_s = role, retry_after_s

    def payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": str(self),
            "role": self.role,
            "retry_after_s": self.retry_after_s,
        }


class CassetteMiss(LlmError):
    """Replay mode found no recorded response for this exact request."""

    def __init__(self, key: str, role: str) -> None:
        super().__init__(
            f"cassette miss: no recorded '{role}' response for request {key[:12]}… "
            "(the prompt changed since recording; re-record with FL_LLM_MODE=record)"
        )
        self.key = key


class LlmUnavailable(LlmError):
    """No key configured, or the provider failed for non-quota reasons."""


class LlmOutputInvalid(LlmError):
    """The model's output did not match the requested schema."""

    def __init__(self, role: str, errors: str) -> None:
        super().__init__(f"'{role}' output did not match the schema: {errors}")
        self.errors = errors
