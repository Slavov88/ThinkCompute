"""Externally controlled Think <-> Compute broker."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .action_schema import ActionKind, ActionParseError, parse_action
from .controller import ControllerState
from .logging import EventLogger
from .research_state import ResearchState, prompt_metrics


@dataclass(frozen=True)
class BrokerConfig:
    n_limit: int = 4
    t_limit_seconds: float = 60.0
    deterministic_call_limit_seconds: float = 30.0
    v_intermediate_limit: int = 6
    lean_process_limit_seconds: float = 60.0
    max_session_seconds: float = 600.0


@dataclass
class RoundResponse:
    raw_text: str
    provider_usage: dict[str, Any] = field(default_factory=dict)
    model_turns: int | None = None
    session_id: str | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int | None = None
    wall_seconds: float | None = None


class RoundModel(Protocol):
    def invoke_once(self, prompt: str, *, scratch: Path, timeout: float) -> RoundResponse: ...


class ToolWorker(Protocol):
    def run(self, kind: str, payload: str, *, timeout: float) -> dict[str, Any]: ...


class LeanWorker(Protocol):
    def run(self, payload: str, *, timeout: float) -> dict[str, Any]: ...


class FinalVerifier(Protocol):
    def verify(self, payload: str) -> dict[str, Any]: ...


def _action_prompt(theorem: str, policy: str, state: ResearchState,
                   controller: ControllerState, allowed: set[ActionKind],
                   *, round_index: int, config: BrokerConfig,
                   t_used: float, v_used: int, n_used: int) -> str:
    allowed_text = ", ".join(kind.value for kind in sorted(allowed, key=lambda item: item.value))
    plan = " Round 1 THINK must set compute_useful and explain the plan in summary/state_update; payload may be empty." if (policy.startswith("A2") or policy.startswith("A3")) and round_index == 0 else ""
    if policy.startswith("A4") and round_index == 0:
        plan = " Round 1 must be PYTHON or SMT; summary/state_update must include the mathematical analysis motivating the experiment."
    gate = " A3 requires one successfully executed PYTHON or SMT action before LEAN becomes available; compute_useful=false does not bypass this gate." if policy.startswith("A3") and not controller.deterministic_gate_satisfied else ""
    if policy.startswith("A4") and not controller.deterministic_gate_satisfied:
        gate = " A4 requires a successfully executed PYTHON or SMT action before LEAN becomes available."
    return (
        "You are one round of a brokered Lean mathematics solver. Return exactly one flat JSON "
        "object matching the supplied action schema: action, summary, compute_useful, payload, state_update, confidence. Do not call shell, filesystem, web, "
        "browser, Docker, or any tool. The broker executes only requested mathematical actions. "
        "Do not return FINAL or declare success.\n"
        f"Policy: {policy}; phase: {controller.phase}; allowed_actions = [{allowed_text}].{plan}{gate}\n"
        f"Round: {round_index + 1}/{config.n_limit}; N used: {n_used}; "
        f"T used/limit: {t_used:.6f}/{config.t_limit_seconds}; V used/limit: {v_used}/{config.v_intermediate_limit}.\n"
        "The theorem statement is immutable; do not use sorry, admit, unsafe axioms, or "
        "source lookup. A LEAN payload must be your current complete proof body. "
        "If Lean fails, use its diagnostics after one THINK/PYTHON/SMT action. "
        "If Lean succeeds, the harness ends the run.\n\n"
        "THEOREM:\n" + theorem + "\n\nCONTROLLER STATE:\n" + json.dumps(controller.as_dict(), sort_keys=True) +
        "\n\nCOMPACT RESEARCH STATE:\n" + state.compact()
    )


def _compact_result(result: dict[str, Any]) -> str:
    keep = {key: result[key] for key in
            ("status", "exit_code", "stdout", "stderr", "timeout", "output_capped",
             "wall_seconds", "verified", "reason") if key in result}
    for key in ("stdout", "stderr"):
        if key in keep and isinstance(keep[key], str):
            keep[key] = keep[key][:4_000]
    return json.dumps(keep, sort_keys=True)


def run_broker(*, theorem: str, policy: str, model: RoundModel,
               tool_worker: ToolWorker, lean_worker: LeanWorker,
               final_verifier: FinalVerifier, scratch: Path,
               config: BrokerConfig = BrokerConfig(), logger: EventLogger | None = None,
               run_id: str | None = None) -> dict[str, Any]:
    run_id = run_id or "broker-" + uuid.uuid4().hex
    state = ResearchState()
    controller = ControllerState(policy=policy)
    started = time.perf_counter()
    t_used = 0.0
    v_used = 0
    rounds = 0
    latest_candidate: str | None = None
    terminal_result: dict[str, Any] | None = None
    terminal_calls = 0
    terminal_lean_calls: int | None = None
    terminal_lean_seconds: float | None = None
    verified = False
    round_records: list[dict[str, Any]] = []
    protocol_errors = 0
    invalid_action_requests = 0
    termination = "n_exhausted"

    def emit(event: dict[str, Any]) -> None:
        if logger:
            logger.event({"run_id": run_id, **event})

    while rounds < config.n_limit:
        if time.perf_counter() - started >= config.max_session_seconds:
            termination = "session_timeout"
            break
        allowed = controller.allowed_actions(round_index=rounds,
                                             t_remaining=config.t_limit_seconds - t_used,
                                             v_remaining=config.v_intermediate_limit - v_used,
                                             n_remaining=config.n_limit - rounds)
        prompt = _action_prompt(theorem, policy, state, controller, allowed,
                                round_index=rounds, config=config, t_used=t_used,
                                v_used=v_used, n_used=rounds)
        record: dict[str, Any] = {"round": rounds + 1, "allowed_actions": sorted(kind.value for kind in allowed),
                                  "controller_state_before": controller.as_dict(), **prompt_metrics(prompt)}
        remaining_session = max(0.01, config.max_session_seconds - (time.perf_counter() - started))
        response = model.invoke_once(prompt, scratch=scratch, timeout=remaining_session)
        rounds += 1
        record.update({"raw_model_output": response.raw_text,
                       "provider_usage": response.provider_usage,
                       "model_turns": response.model_turns,
                       "session_id": response.session_id,
                       "raw_model_stdout": response.raw_stdout,
                       "raw_model_stderr": response.raw_stderr,
                       "model_exit_code": response.exit_code,
                       "model_wall_seconds": response.wall_seconds})
        try:
            action = parse_action(response.raw_text, policy=policy, allowed_actions=allowed)
            controller.validate_think_plan(action, round_index=rounds - 1)
            record["action"] = action.action.value
        except ActionParseError as exc:
            message = str(exc)
            if "unavailable" in message or "not allowed by policy" in message:
                invalid_action_requests += 1
            else:
                protocol_errors += 1
            feedback = ("Your previous response did not satisfy the required flat action schema. "
                        "No mathematical action was executed. Respond with exactly the fields "
                        "action, summary, compute_useful, payload, state_update, confidence. " + message)
            state.record_rejection(feedback)
            record.update({"action": None, "parse_error": message,
                           "protocol_errors": protocol_errors,
                           "invalid_action_requests": invalid_action_requests,
                           "feedback": feedback, "state_after": state.compact()})
            round_records.append(record)
            emit({"event": "broker_round", **record})
            if rounds >= config.n_limit:
                termination = "n_exhausted"
                break
            continue

        tool_result: dict[str, Any] | None = None
        if action.action in {ActionKind.PYTHON, ActionKind.SMT}:
            remaining_t = config.t_limit_seconds - t_used
            timeout = min(config.deterministic_call_limit_seconds, remaining_t)
            try:
                tool_result = tool_worker.run(action.action.value.lower(), action.payload, timeout=timeout)
            except ValueError as exc:
                tool_result = {"status": "rejected_by_validator", "reason": str(exc),
                               "wall_seconds": 0.0, "exit_code": None}
            t_used += max(0.0, float(tool_result.get("wall_seconds", 0.0)))
            state.apply_action(action, tool_result=_compact_result(tool_result))
        elif action.action == ActionKind.LEAN:
            latest_candidate = action.payload
            try:
                tool_result = lean_worker.run(action.payload, timeout=config.lean_process_limit_seconds)
            except ValueError as exc:
                tool_result = {"status": "rejected_by_validator", "reason": str(exc),
                               "wall_seconds": 0.0, "exit_code": None, "verified": False}
            v_used += 1
            state.apply_action(action, tool_result=_compact_result(tool_result))
            controller.observe(action, tool_result=tool_result, round_index=rounds - 1)
            if tool_result.get("verified", False) or (tool_result.get("status") == "ok" and
                                                       tool_result.get("exit_code") == 0):
                verified = True
                termination = "lean_verified"
        else:
            state.apply_action(action)
            controller.observe(action)

        if action.action in {ActionKind.PYTHON, ActionKind.SMT}:
            controller.observe(action, tool_result=tool_result, round_index=rounds - 1)
        if tool_result is not None:
            record["tool_result"] = tool_result
        record["controller_state_after"] = controller.as_dict()
        record["latest_candidate_present"] = latest_candidate is not None
        record["state_after"] = state.compact()
        round_records.append(record)
        emit({"event": "broker_round", **record})
        if verified:
            break

    if rounds >= config.n_limit and not verified and latest_candidate is not None:
        terminal_result = final_verifier.verify(latest_candidate)
        terminal_calls = 1
        terminal_lean_calls = terminal_result.get("lean_calls")
        terminal_lean_seconds = terminal_result.get("lean_seconds")
        if terminal_result.get("verified"):
            verified = True
            termination = "terminal_verified"
        else:
            termination = "n_exhausted"

    return {
        "run_id": run_id, "policy": policy,
        "n_limit": config.n_limit, "n_actual": rounds,
        "t_limit_seconds": config.t_limit_seconds, "t_actual_seconds": t_used,
        "v_intermediate_limit": config.v_intermediate_limit, "v_intermediate_actual": v_used,
        "protocol_errors": protocol_errors, "invalid_action_requests": invalid_action_requests,
        "terminal_verification_calls": terminal_calls,
        "terminal_lean_calls": terminal_lean_calls,
        "terminal_lean_seconds": terminal_lean_seconds,
        "terminal_verification": terminal_result,
        "latest_candidate": latest_candidate,
        "controller_state": controller.as_dict(),
        "total_wall_seconds": time.perf_counter() - started,
        "termination_reason": termination, "verified": verified,
        "deterministic_gate_satisfied": controller.deterministic_gate_satisfied,
        "gate_round": controller.gate_round,
        "gate_tool": controller.gate_tool,
        "gate_runtime_seconds": controller.gate_runtime_seconds,
        "rounds": round_records,
    }
