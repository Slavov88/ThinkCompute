from __future__ import annotations

from pathlib import Path

from .benchmark import Problem
from .budgets import Budget
from .logging import EventLogger
from .verifier import verify


class IndependentFinalVerifier:
    """Broker ``FinalVerifier``: terminal check by the harness, never by the model.

    Rebuilds the immutable theorem from the problem file, substitutes the candidate proof
    body, and accepts only if Lean compiles it and no disallowed construct is present.
    """

    def __init__(self, problem: Problem, scratch: Path, logger: EventLogger | None = None,
                 lean: str | None = None, lean_process_limit: float = 60.0):
        self.problem = problem
        self.scratch = Path(scratch)
        self.logger = logger or EventLogger(self.scratch / "final-verifier.events.jsonl")
        self.lean = lean
        self.budget = Budget(exploration_limit=0.0, deterministic_process_limit=0.0,
                             lean_call_limit=20, lean_process_limit=lean_process_limit,
                             session_limit=600.0, neural_cycle_limit=None)

    def verify(self, payload: str) -> dict:
        result = verify(self.problem, payload, scratch=self.scratch, logger=self.logger,
                        budget=self.budget, lean=self.lean)
        result["final_verification_calls"] = 1
        return result
