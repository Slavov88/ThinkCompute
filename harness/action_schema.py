"""Strict, broker-facing action protocol.

The model is never given a shell capability. It can only return one of these
actions; the broker decides whether and how to execute the payload.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ActionKind(str, Enum):
    THINK = "THINK"
    PYTHON = "PYTHON"
    SMT = "SMT"
    LEAN = "LEAN"


POLICY_ACTIONS = {
    "A0": {ActionKind.THINK, ActionKind.LEAN},
    "A0_think": {ActionKind.THINK, ActionKind.LEAN},
    "A1": set(ActionKind),
    "A1_free_tools": set(ActionKind),
    "A1_FREE_TOOLS": set(ActionKind),
    "A2": set(ActionKind),
    "A2_structured": set(ActionKind),
    "A2_STRUCTURED": set(ActionKind),
    "A3": set(ActionKind),
    "A3_forced_compute": set(ActionKind),
    "A3_FORCED_COMPUTE": set(ActionKind),
    "A4": set(ActionKind),
    "A4_integrated_compute": set(ActionKind),
    "A4_INTEGRATED_COMPUTE": set(ActionKind),
}
MAX_FIELD_CHARS = 24_000


@dataclass(frozen=True)
class Action:
    summary: str
    action: ActionKind
    compute_useful: bool
    payload: str
    state_update: str
    confidence: float | None


class ActionParseError(ValueError):
    pass


def _bounded(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ActionParseError(f"{field} must be a string")
    if len(value) > MAX_FIELD_CHARS:
        raise ActionParseError(f"{field} exceeds {MAX_FIELD_CHARS} characters")
    return value


def _json_object(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ActionParseError("response must be JSON object text")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionParseError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ActionParseError("response must decode to a JSON object")
    return value


def parse_action(raw: str | dict[str, Any], *, policy: str = "A2",
                 allowed_actions: set[ActionKind] | None = None) -> Action:
    obj = _json_object(raw)
    required = ("action", "summary", "compute_useful", "payload", "state_update", "confidence")
    missing = [key for key in required if key not in obj]
    if missing:
        raise ActionParseError("missing fields: " + ", ".join(missing))
    unexpected = sorted(set(obj) - set(required))
    if unexpected:
        raise ActionParseError("unexpected fields: " + ", ".join(unexpected))
    try:
        kind = ActionKind(str(obj["action"]).upper())
    except ValueError as exc:
        raise ActionParseError("unsupported action") from exc
    if kind not in POLICY_ACTIONS.get(policy, set()):
        raise ActionParseError(f"{kind.value} is not allowed by policy {policy}")
    if allowed_actions is not None and kind not in allowed_actions:
        raise ActionParseError(f"{kind.value} is unavailable in the current controller state")
    compute_useful = obj["compute_useful"]
    if not isinstance(compute_useful, bool):
        raise ActionParseError("compute_useful must be boolean")
    confidence = obj["confidence"]
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ActionParseError("confidence must be numeric or null")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ActionParseError("confidence must be between 0 and 1")
        confidence = float(confidence)
    return Action(
        summary=_bounded(obj["summary"], "summary"),
        action=kind,
        compute_useful=compute_useful,
        payload=_bounded(obj["payload"], "payload"),
        state_update=_bounded(obj["state_update"], "state_update"),
        confidence=confidence,
    )


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "summary", "compute_useful", "payload", "state_update", "confidence"],
        "properties": {
            "action": {"type": "string", "enum": [kind.value for kind in ActionKind]},
            "summary": {"type": "string", "maxLength": MAX_FIELD_CHARS},
            "compute_useful": {"type": "boolean"},
            "payload": {"type": "string", "maxLength": MAX_FIELD_CHARS},
            "state_update": {"type": "string", "maxLength": MAX_FIELD_CHARS},
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        },
    }
