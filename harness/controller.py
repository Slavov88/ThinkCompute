"""Harness-owned allocation state for brokered policies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .action_schema import Action, ActionKind, ActionParseError


@dataclass
class ControllerState:
    policy: str
    phase: str = "ANALYZE"
    last_action: str | None = None
    last_lean_success: bool | None = None
    consecutive_failed_lean: int = 0
    deterministic_compute_useful: bool | None = None
    t_remaining: float = 0.0
    v_remaining: int = 0
    n_remaining: int = 0
    deterministic_gate_satisfied: bool = False
    gate_round: int | None = None
    gate_tool: str | None = None
    gate_runtime_seconds: float | None = None

    @property
    def is_structured(self) -> bool:
        return self.policy.startswith("A2") or self.policy.startswith("A3") or self.policy.startswith("A4")

    def allowed_actions(self, *, round_index: int, t_remaining: float,
                        v_remaining: int, n_remaining: int) -> set[ActionKind]:
        self.t_remaining = max(0.0, t_remaining)
        self.v_remaining = max(0, v_remaining)
        self.n_remaining = max(0, n_remaining)
        if self.policy.startswith("A4") and not self.deterministic_gate_satisfied:
            allowed = {ActionKind.PYTHON, ActionKind.SMT}
        elif self.is_structured and round_index == 0:
            allowed = {ActionKind.THINK}
        else:
            allowed = {ActionKind.THINK, ActionKind.PYTHON, ActionKind.SMT, ActionKind.LEAN}
        if (self.policy.startswith("A3") or self.policy.startswith("A4")) and not self.deterministic_gate_satisfied:
            allowed.discard(ActionKind.LEAN)
        if self.t_remaining <= 0:
            allowed -= {ActionKind.PYTHON, ActionKind.SMT}
        if self.v_remaining <= 0:
            allowed.discard(ActionKind.LEAN)
        if self.is_structured and self.consecutive_failed_lean:
            allowed.discard(ActionKind.LEAN)
        return allowed

    def validate_think_plan(self, action: Action, *, round_index: int) -> None:
        if not ((self.policy.startswith("A2") or self.policy.startswith("A3")) and round_index == 0):
            return
        if action.action != ActionKind.THINK:
            raise ActionParseError("structured round 1 requires THINK")
        self.deterministic_compute_useful = action.compute_useful

    def observe(self, action: Action, *, tool_result: dict[str, Any] | None = None,
                round_index: int | None = None) -> None:
        self.last_action = action.action.value
        if action.action == ActionKind.LEAN:
            success = bool(tool_result and (tool_result.get("verified") or
                                             (tool_result.get("status") == "ok" and
                                              tool_result.get("exit_code") == 0)))
            self.last_lean_success = success
            if success:
                self.consecutive_failed_lean = 0
                self.phase = "PROVE"
            else:
                self.consecutive_failed_lean += 1
                self.phase = "REPAIR"
        elif action.action == ActionKind.THINK:
            self.deterministic_compute_useful = action.compute_useful
            self.consecutive_failed_lean = 0
            self.phase = "EXPLORE" if self.deterministic_compute_useful else "PROVE"
        elif action.action in {ActionKind.PYTHON, ActionKind.SMT}:
            self.consecutive_failed_lean = 0
            self.phase = "EXPLORE"
            success = bool(tool_result and tool_result.get("status") == "ok" and
                          tool_result.get("exit_code") == 0 and
                          not tool_result.get("timeout") and
                          not tool_result.get("output_capped"))
            if (self.policy.startswith("A3") or self.policy.startswith("A4")) and success and not self.deterministic_gate_satisfied:
                self.deterministic_gate_satisfied = True
                self.gate_round = round_index + 1 if round_index is not None else None
                self.gate_tool = action.action.value
                self.gate_runtime_seconds = float(tool_result.get("wall_seconds", 0.0) or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "last_action": self.last_action,
            "last_lean_success": self.last_lean_success,
            "consecutive_failed_lean": self.consecutive_failed_lean,
            "deterministic_compute_useful": self.deterministic_compute_useful,
            "T_remaining": self.t_remaining,
            "V_remaining": self.v_remaining,
            "N_remaining": self.n_remaining,
            "deterministic_gate_satisfied": self.deterministic_gate_satisfied,
            "gate_round": self.gate_round,
            "gate_tool": self.gate_tool,
            "gate_runtime_seconds": self.gate_runtime_seconds,
        }
