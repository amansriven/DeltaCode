"""Environment-configured, fail-fast resource budgets for one migration attempt."""

import json
import os
from dataclasses import dataclass

from app.migration_generation.models import (
    GenerationProposal,
    PlanningContext,
    SandboxExecutionResult,
)


class BudgetExceeded(RuntimeError):
    code = "cost_budget_exceeded"


def _bounded_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class GenerationLimits:
    max_context_bytes: int = 100_000
    max_proposal_bytes: int = 250_000
    max_total_check_timeout_ms: int = 600_000
    max_sandbox_duration_ms: int = 600_000

    @classmethod
    def from_env(cls) -> "GenerationLimits":
        return cls(
            max_context_bytes=_bounded_env(
                "GENERATION_MAX_CONTEXT_BYTES", 100_000, minimum=10_000, maximum=1_000_000
            ),
            max_proposal_bytes=_bounded_env(
                "GENERATION_MAX_PROPOSAL_BYTES", 250_000, minimum=10_000, maximum=1_000_000
            ),
            max_total_check_timeout_ms=_bounded_env(
                "GENERATION_MAX_CHECK_TIMEOUT_MS", 600_000, minimum=1_000, maximum=1_200_000
            ),
            max_sandbox_duration_ms=_bounded_env(
                "GENERATION_MAX_SANDBOX_DURATION_MS", 600_000, minimum=1_000, maximum=1_200_000
            ),
        )

    def validate_context(self, context: PlanningContext) -> None:
        size = len(json.dumps(context.model_dump(mode="json"), separators=(",", ":")).encode())
        if size > self.max_context_bytes:
            raise BudgetExceeded("planning context exceeds the configured attempt budget")

    def validate_proposal(self, proposal: GenerationProposal) -> None:
        size = len(json.dumps(proposal.model_dump(mode="json"), separators=(",", ":")).encode())
        if size > self.max_proposal_bytes:
            raise BudgetExceeded("generation proposal exceeds the configured attempt budget")
        timeout = sum(command.timeout_ms for command in proposal.patch.verification_commands)
        if timeout > self.max_total_check_timeout_ms:
            raise BudgetExceeded("verification timeouts exceed the configured attempt budget")

    def validate_execution(self, execution: SandboxExecutionResult) -> None:
        if execution.duration_ms > self.max_sandbox_duration_ms:
            raise BudgetExceeded("sandbox duration exceeds the configured attempt budget")
