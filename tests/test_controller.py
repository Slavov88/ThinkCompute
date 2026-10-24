from __future__ import annotations

import json
import unittest

from harness.action_schema import ActionKind, parse_action
from harness.controller import ControllerState


def action(kind: str, payload: str = "", compute_useful: bool = False):
    return parse_action(json.dumps({
        "summary": "mock",
        "action": kind,
        "compute_useful": compute_useful,
        "payload": payload,
        "state_update": "mock",
        "confidence": 0.5,
    }), policy="A2_structured")


def a3_action(kind: str, payload: str = "", compute_useful: bool = False):
    return parse_action(json.dumps({
        "summary": "mock",
        "action": kind,
        "compute_useful": compute_useful,
        "payload": payload,
        "state_update": "mock",
        "confidence": 0.5,
    }), policy="A3_forced_compute")


def a4_action(kind: str, payload: str = "", compute_useful: bool = True):
    return parse_action(json.dumps({
        "summary": "mock integrated analysis",
        "action": kind,
        "compute_useful": compute_useful,
        "payload": payload,
        "state_update": "mock",
        "confidence": 0.5,
    }), policy="A4_INTEGRATED_COMPUTE")


class ControllerTests(unittest.TestCase):
    def test_a2_round_one_only_think_and_requires_plan(self):
        state = ControllerState("A2_structured")
        self.assertEqual(state.allowed_actions(round_index=0, t_remaining=10, v_remaining=6, n_remaining=4),
                         {ActionKind.THINK})
        parsed = action("THINK", compute_useful=True)
        state.validate_think_plan(parsed, round_index=0)
        self.assertTrue(state.deterministic_compute_useful)

    def test_a1_round_one_is_unrestricted(self):
        state = ControllerState("A1_free_tools")
        self.assertEqual(state.allowed_actions(round_index=0, t_remaining=10, v_remaining=6, n_remaining=4),
                         set(ActionKind))

    def test_think_plan_can_disable_compute(self):
        state = ControllerState("A2_structured")
        parsed = action("THINK", compute_useful=False)
        state.validate_think_plan(parsed, round_index=0)
        state.observe(parsed)
        self.assertFalse(state.deterministic_compute_useful)
        self.assertEqual(state.phase, "PROVE")

    def test_failed_lean_cooldown_and_reenable(self):
        state = ControllerState("A2_structured")
        state.observe(action("LEAN", "candidate"), tool_result={"status": "failed"})
        allowed = state.allowed_actions(round_index=1, t_remaining=10, v_remaining=5, n_remaining=3)
        self.assertNotIn(ActionKind.LEAN, allowed)
        state.observe(action("THINK"))
        allowed = state.allowed_actions(round_index=2, t_remaining=10, v_remaining=5, n_remaining=2)
        self.assertIn(ActionKind.LEAN, allowed)

    def test_compute_after_failed_lean_also_reenables_lean(self):
        for kind in ("PYTHON", "SMT"):
            state = ControllerState("A2_structured")
            state.observe(action("LEAN", "candidate"), tool_result={"status": "failed"})
            state.observe(action(kind), tool_result={"status": "ok", "wall_seconds": 0.1})
            self.assertIn(ActionKind.LEAN,
                          state.allowed_actions(round_index=2, t_remaining=9, v_remaining=4, n_remaining=2))

    def test_resource_exhaustion_removes_actions(self):
        state = ControllerState("A2_structured")
        allowed = state.allowed_actions(round_index=1, t_remaining=0, v_remaining=0, n_remaining=3)
        self.assertEqual(allowed, {ActionKind.THINK})

    def test_a3_round_one_and_pre_gate_disallow_lean(self):
        state = ControllerState("A3_forced_compute")
        self.assertEqual(state.allowed_actions(round_index=0, t_remaining=10, v_remaining=6, n_remaining=4),
                         {ActionKind.THINK})
        state.validate_think_plan(a3_action("THINK", compute_useful=False), round_index=0)
        state.observe(a3_action("THINK", compute_useful=False), round_index=0)
        allowed = state.allowed_actions(round_index=1, t_remaining=10, v_remaining=6, n_remaining=3)
        self.assertNotIn(ActionKind.LEAN, allowed)
        self.assertIn(ActionKind.PYTHON, allowed)

    def test_a3_successful_python_and_smt_satisfy_gate(self):
        for kind in ("PYTHON", "SMT"):
            state = ControllerState("A3_forced_compute")
            state.observe(a3_action(kind), tool_result={"status": "ok", "exit_code": 0,
                                                        "timeout": False, "output_capped": False,
                                                        "wall_seconds": 0.25}, round_index=1)
            self.assertTrue(state.deterministic_gate_satisfied)
            self.assertEqual(state.gate_tool, kind)
            self.assertIn(ActionKind.LEAN,
                          state.allowed_actions(round_index=2, t_remaining=9, v_remaining=6, n_remaining=2))

    def test_a3_failed_deterministic_attempt_does_not_satisfy_gate(self):
        state = ControllerState("A3_forced_compute")
        state.observe(a3_action("PYTHON"), tool_result={"status": "failed", "exit_code": 1,
                                                         "timeout": False, "output_capped": False,
                                                         "wall_seconds": 0.0}, round_index=1)
        self.assertFalse(state.deterministic_gate_satisfied)
        self.assertNotIn(ActionKind.LEAN,
                         state.allowed_actions(round_index=2, t_remaining=10, v_remaining=6, n_remaining=2))

    def test_a3_timeout_does_not_satisfy_gate(self):
        state = ControllerState("A3_forced_compute")
        state.observe(a3_action("SMT"), tool_result={"status": "failed", "exit_code": None,
                                                       "timeout": True, "output_capped": False,
                                                       "wall_seconds": 5.0}, round_index=1)
        self.assertFalse(state.deterministic_gate_satisfied)
        self.assertIsNone(state.gate_tool)

    def test_a3_budget_exhaustion_removes_compute_and_keeps_gate_closed(self):
        state = ControllerState("A3_forced_compute")
        allowed = state.allowed_actions(round_index=1, t_remaining=0, v_remaining=6, n_remaining=2)
        self.assertEqual(allowed, {ActionKind.THINK})

    def test_a3_gate_records_actual_round(self):
        state = ControllerState("A3_forced_compute")
        state.observe(a3_action("PYTHON"), tool_result={"status": "ok", "exit_code": 0,
                                                        "timeout": False, "output_capped": False,
                                                        "wall_seconds": 0.125}, round_index=1)
        self.assertEqual(state.gate_round, 2)
        self.assertEqual(state.gate_runtime_seconds, 0.125)

    def test_a4_round_one_and_pre_gate_only_permit_compute(self):
        state = ControllerState("A4_INTEGRATED_COMPUTE")
        self.assertEqual(state.allowed_actions(round_index=0, t_remaining=10, v_remaining=6, n_remaining=8),
                         {ActionKind.PYTHON, ActionKind.SMT})
        state.observe(a4_action("PYTHON"), tool_result={"status": "failed", "exit_code": 1,
                                                          "timeout": False, "output_capped": False,
                                                          "wall_seconds": 0.1}, round_index=0)
        self.assertEqual(state.allowed_actions(round_index=1, t_remaining=9.9, v_remaining=6, n_remaining=7),
                         {ActionKind.PYTHON, ActionKind.SMT})

    def test_a4_successful_compute_opens_lean_and_records_gate(self):
        state = ControllerState("A4_INTEGRATED_COMPUTE")
        state.observe(a4_action("SMT"), tool_result={"status": "ok", "exit_code": 0,
                                                       "timeout": False, "output_capped": False,
                                                       "wall_seconds": 0.25}, round_index=0)
        self.assertTrue(state.deterministic_gate_satisfied)
        self.assertEqual(state.gate_round, 1)
        self.assertEqual(state.gate_tool, "SMT")
        self.assertIn(ActionKind.LEAN, state.allowed_actions(round_index=1, t_remaining=9.75,
                                                             v_remaining=6, n_remaining=7))

    def test_a4_exhaustion_removes_compute_and_lean(self):
        state = ControllerState("A4_INTEGRATED_COMPUTE")
        self.assertEqual(state.allowed_actions(round_index=0, t_remaining=0, v_remaining=0, n_remaining=8), set())


if __name__ == "__main__":
    unittest.main()
