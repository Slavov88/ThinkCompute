"""Bounded state passed between independent broker rounds."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .action_schema import Action


MAX_TOTAL_CHARS = 32_000
MAX_ITEM_CHARS = 4_000
MAX_ITEMS = 12


def _clip(value: str, limit: int = MAX_ITEM_CHARS) -> str:
    value = str(value)
    if len(value) <= limit:
        return value
    marker = "\n...[truncated]...\n"
    available = max(0, limit - len(marker))
    left = available // 2
    return value[:left] + marker + value[-(available - left):]


@dataclass
class ResearchState:
    observations: list[str] = field(default_factory=list)
    candidate_lemmas: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)
    proof_plan: str = ""
    lean_feedback: str = ""
    latest_tool_result: str = ""

    def _append(self, target: list[str], value: str) -> None:
        if value:
            target.append(_clip(value))
            del target[:-MAX_ITEMS]

    def apply_action(self, action: Action, *, tool_result: str = "") -> None:
        self._append(self.observations, action.summary)
        self._append(self.observations, action.state_update)
        if action.action.value == "LEAN":
            self.lean_feedback = _clip(tool_result or action.payload)
        elif action.action.value in {"PYTHON", "SMT"}:
            self.latest_tool_result = _clip(tool_result)
        elif action.state_update:
            self.proof_plan = _clip(action.state_update)

    def record_rejection(self, message: str) -> None:
        self._append(self.observations, "Broker rejection: " + message)

    def as_dict(self) -> dict[str, object]:
        return {
            "observations": self.observations[-MAX_ITEMS:],
            "candidate_lemmas": self.candidate_lemmas[-MAX_ITEMS:],
            "counterexamples": self.counterexamples[-MAX_ITEMS:],
            "proof_plan": _clip(self.proof_plan),
            "lean_feedback": _clip(self.lean_feedback),
            "latest_tool_result": _clip(self.latest_tool_result),
        }

    def compact(self) -> str:
        raw = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)
        return _clip(raw, MAX_TOTAL_CHARS)


def prompt_metrics(prompt: str) -> dict[str, int]:
    encoded = prompt.encode("utf-8")
    return {"prompt_chars": len(prompt), "prompt_bytes": len(encoded), "prompt_words": len(prompt.split())}
