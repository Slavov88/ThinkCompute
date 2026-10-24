from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from harness.broker import BrokerConfig, run_broker
from harness.docker_worker import DockerToolWorker, MockToolWorker
from harness.model_mock import ScriptedMockModel
from harness.packaging import package_one_problem
from harness.research_state import MAX_TOTAL_CHARS, ResearchState


def response(action: str, payload: str = "", compute_useful: bool = False) -> str:
    return json.dumps({
        "summary": "bounded mock analysis",
        "action": action,
        "compute_useful": compute_useful,
        "payload": payload,
        "state_update": "bounded mock state",
        "confidence": 0.5,
    })


def think_plan(useful: bool) -> str:
    return response("THINK", compute_useful=useful)


class MockLean:
    def __init__(self, verified=True):
        self.calls = []
        self.verified = verified

    def run(self, payload, *, timeout):
        self.calls.append((payload, timeout))
        return {"status": "ok" if self.verified else "failed", "verified": self.verified,
                "exit_code": 0 if self.verified else 1, "wall_seconds": 0.25}


class MockFinal:
    def __init__(self, verified=True):
        self.calls = []
        self.verified = verified

    def verify(self, payload):
        self.calls.append(payload)
        return {"verified": self.verified, "statement_hash_match": True,
                "lean_calls": 1, "lean_seconds": 0.1}


def run_script(responses, *, config=None, durations=None, lean=None, final=None, policy="A2", tool_worker=None):
    model = ScriptedMockModel(responses)
    tools = tool_worker or MockToolWorker(durations)
    lean = lean or MockLean()
    final = final or MockFinal()
    with tempfile.TemporaryDirectory() as raw:
        result = run_broker(theorem="theorem target : True := by sorry", policy=policy,
                            model=model, tool_worker=tools, lean_worker=lean,
                            final_verifier=final, scratch=Path(raw),
                            config=config or BrokerConfig())
    return result, model, tools, lean, final


class BrokerTests(unittest.TestCase):
    def test_one_problem_packager_is_split_restricted_and_allowlisted(self):
        from harness.benchmark import Problem
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            main = root / "Main.lean"; statement = root / "problem.md"
            main.write_text("theorem target : True := by sorry\n")
            statement.write_text("A mock theorem.\n")
            problem = Problem("TC-MOCK", "dev", main, statement, "m", "s")
            out = package_one_problem(problem, root / "package", expected_id="TC-MOCK", allowed_split="dev")
            self.assertEqual(sorted(path.name for path in out.iterdir()), ["Main.lean", "problem.md"])
            with self.assertRaises(PermissionError):
                package_one_problem(Problem("TC-H", "other", main, statement, "m", "s"), root / "other", allowed_split="dev")

    def test_failed_lean_continues_and_terminal_verification_is_separate(self):
        result, model, tools, lean, final = run_script(
            [think_plan(True), response("PYTHON", "print(2+2)"),
             response("LEAN", "by exact False.intro"), response("THINK")],
            durations={"python": 0.5}, lean=MockLean(verified=False))
        self.assertEqual(result["n_actual"], 4)
        self.assertAlmostEqual(result["t_actual_seconds"], 0.5)
        self.assertEqual(result["v_intermediate_actual"], 1)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(len(lean.calls), 1)
        self.assertEqual(len(final.calls), 1)
        self.assertEqual(result["terminal_verification_calls"], 1)
        self.assertEqual(result["terminal_lean_calls"], 1)
        self.assertTrue(result["verified"])
        self.assertEqual(len(model.calls), 4)
        self.assertIn("LEAN", result["rounds"][2]["allowed_actions"])
        self.assertNotIn("LEAN", result["rounds"][3]["allowed_actions"])

    def test_successful_lean_terminates_early(self):
        result, model, tools, lean, final = run_script(
            [think_plan(False), response("LEAN", "by exact True.intro"), response("THINK")],
            lean=MockLean(verified=True), config=BrokerConfig(n_limit=3))
        self.assertEqual(result["n_actual"], 2)
        self.assertEqual(result["v_intermediate_actual"], 1)
        self.assertEqual(result["termination_reason"], "lean_verified")
        self.assertTrue(result["verified"])
        self.assertEqual(result["terminal_verification_calls"], 0)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(final.calls), 0)

    def test_think_consumes_n_but_not_t(self):
        result, *_ = run_script([think_plan(False), think_plan(False)],
                                config=BrokerConfig(n_limit=2))
        self.assertEqual(result["n_actual"], 2)
        self.assertEqual(result["t_actual_seconds"], 0.0)
        self.assertEqual(result["termination_reason"], "n_exhausted")

    def test_smt_charges_measured_runtime(self):
        result, _, tools, *_ = run_script([think_plan(True), response("SMT")],
                                          config=BrokerConfig(n_limit=2), durations={"smt": 0.7})
        self.assertAlmostEqual(result["t_actual_seconds"], 0.7)
        self.assertEqual(tools.calls[0]["kind"], "smt")

    def test_lean_consumes_v_not_t_and_v_exhaustion_is_safe(self):
        result, _, tools, lean, final = run_script(
            [think_plan(False), response("LEAN", "bad intermediate")],
            config=BrokerConfig(v_intermediate_limit=0, n_limit=2))
        self.assertEqual(result["v_intermediate_actual"], 0)
        self.assertEqual(result["t_actual_seconds"], 0.0)
        self.assertEqual(len(lean.calls), 0)
        self.assertEqual(len(final.calls), 0)
        self.assertEqual(len(tools.calls), 0)
        self.assertNotIn("LEAN", result["rounds"][1]["allowed_actions"])

    def test_final_action_is_rejected(self):
        result, _, tools, *_ = run_script([response("FINAL", "by exact True.intro")],
                                           config=BrokerConfig(n_limit=1))
        self.assertEqual(result["termination_reason"], "n_exhausted")
        self.assertEqual(result["invalid_action_requests"], 0)
        self.assertEqual(result["protocol_errors"], 1)
        self.assertEqual(len(tools.calls), 0)

    def test_t_exhaustion_disables_later_compute(self):
        result, _, tools, *_ = run_script(
            [think_plan(True), response("PYTHON", "first"), think_plan(True)],
            config=BrokerConfig(n_limit=3, t_limit_seconds=1.0), durations={"python": 1.0})
        self.assertEqual(result["t_actual_seconds"], 1.0)
        self.assertEqual(len(tools.calls), 1)
        self.assertNotIn("PYTHON", result["rounds"][2]["allowed_actions"])

    def test_malformed_and_policy_forbidden_actions_are_rejected(self):
        result, _, tools, *_ = run_script(["not json"], config=BrokerConfig(n_limit=1))
        self.assertEqual(result["termination_reason"], "n_exhausted")
        self.assertEqual(result["protocol_errors"], 1)
        self.assertEqual(len(tools.calls), 0)
        result, _, tools, *_ = run_script([response("PYTHON", "x")], policy="A0",
                                           config=BrokerConfig(n_limit=1))
        self.assertEqual(result["termination_reason"], "n_exhausted")
        self.assertEqual(result["invalid_action_requests"], 1)
        self.assertEqual(len(tools.calls), 0)

    def test_malformed_output_is_recoverable_and_feedback_is_forwarded(self):
        result, model, tools, *_ = run_script(["not json", think_plan(False)],
                                               config=BrokerConfig(n_limit=2))
        self.assertEqual(result["n_actual"], 2)
        self.assertEqual(result["protocol_errors"], 1)
        self.assertEqual(result["termination_reason"], "n_exhausted")
        self.assertEqual(result["rounds"][1]["action"], "THINK")
        self.assertIn("did not satisfy the required flat action schema", model.calls[1]["prompt"])
        self.assertEqual(len(tools.calls), 0)

    def test_unavailable_action_is_recoverable_without_tool_execution(self):
        result, model, tools, *_ = run_script([response("LEAN", "candidate"), think_plan(False)],
                                               config=BrokerConfig(n_limit=2))
        self.assertEqual(result["n_actual"], 2)
        self.assertEqual(result["invalid_action_requests"], 1)
        self.assertEqual(result["protocol_errors"], 0)
        self.assertEqual(result["rounds"][1]["action"], "THINK")
        self.assertEqual(len(tools.calls), 0)

    def test_obsolete_nested_schema_is_rejected(self):
        obsolete = json.dumps({
            "analysis_summary": "old wire format",
            "action": "THINK",
            "payload": json.dumps({"deterministic_compute_useful": False}),
            "state_update": "old",
            "confidence": 0.5,
        })
        result, *_ = run_script([obsolete], config=BrokerConfig(n_limit=1))
        self.assertEqual(result["protocol_errors"], 1)
        self.assertEqual(result["n_actual"], 1)

    def test_bounded_state_and_prompt_metrics(self):
        state = ResearchState()
        state.observations.extend(["x" * 10_000] * 100)
        self.assertLessEqual(len(state.compact()), MAX_TOTAL_CHARS)
        result, *_ = run_script([think_plan(False)], config=BrokerConfig(n_limit=1))
        self.assertIn("prompt_chars", result["rounds"][0])
        self.assertIn("prompt_bytes", result["rounds"][0])

    def test_docker_command_has_outer_boundary_and_no_broad_mounts(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw) / "worker"
            worker = DockerToolWorker(image="thinkcompute-tools:pinned", scratch_root=base)
            command = worker.build_command(input_dir=base / "input", scratch_dir=base / "scratch",
                                           kind="smt", target="request.py", timeout=3)
            self.assertIn("--network", command)
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop", command)
            mounts = [value for index, value in enumerate(command) if index and command[index - 1] == "--mount"]
            self.assertEqual(len(mounts), 2)
            self.assertIn("/opt/thinkcompute/tc_tool.py", command)
            with self.assertRaises(ValueError):
                worker.build_command(input_dir=Path(raw) / "elsewhere", scratch_dir=base / "scratch",
                                     kind="python", target="request.py", timeout=3)

    def test_a3_requires_successful_deterministic_gate_before_lean(self):
        result, model, tools, lean, final = run_script(
            [think_plan(False), response("PYTHON", "print(2+2)"), response("LEAN", "bad")],
            config=BrokerConfig(n_limit=3, t_limit_seconds=10), policy="A3_forced_compute",
            durations={"python": 0.5}, lean=MockLean(verified=False))
        self.assertEqual(result["n_actual"], 3)
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result["t_actual_seconds"], 0.5)
        self.assertEqual(result["deterministic_gate_satisfied"], True)
        self.assertEqual(result["gate_tool"], "PYTHON")
        self.assertEqual(len(lean.calls), 1)
        self.assertEqual(result["rounds"][1]["action"], "PYTHON")
        self.assertNotIn("LEAN", result["rounds"][1]["allowed_actions"])

    def test_a3_failed_compute_keeps_lean_unavailable(self):
        class FailedTools(MockToolWorker):
            def run(self, kind, payload, *, timeout):
                self.calls.append({"kind": kind})
                return {"status": "failed", "exit_code": 1, "timeout": False,
                        "output_capped": False, "wall_seconds": 0.0}
        model = ScriptedMockModel([think_plan(True), response("PYTHON", "bad"), response("LEAN", "bad")])
        tools = FailedTools()
        with tempfile.TemporaryDirectory() as raw:
            result = run_broker(theorem="theorem target : True := by sorry", policy="A3_forced_compute",
                                model=model, tool_worker=tools, lean_worker=MockLean(verified=False),
                                final_verifier=MockFinal(), scratch=Path(raw),
                                config=BrokerConfig(n_limit=3, t_limit_seconds=10))
        self.assertFalse(result["deterministic_gate_satisfied"])
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result["invalid_action_requests"], 1)

    def test_a3_gate_has_no_neural_bonus_and_t_exhaustion_blocks_compute(self):
        result, model, tools, *_ = run_script(
            [think_plan(True), response("PYTHON", "print(1)")],
            config=BrokerConfig(n_limit=2, t_limit_seconds=0), policy="A3_forced_compute")
        self.assertEqual(result["n_actual"], 2)
        self.assertFalse(result["deterministic_gate_satisfied"])
        self.assertEqual(len(tools.calls), 0)
        self.assertNotIn("PYTHON", result["rounds"][1]["allowed_actions"])

    def test_a3_failed_lean_requires_nonlean_repair_round(self):
        result, model, tools, lean, final = run_script(
            [think_plan(True), response("PYTHON", "print(1)"),
             response("LEAN", "bad"), response("LEAN", "still bad")],
            config=BrokerConfig(n_limit=4, t_limit_seconds=10), policy="A3_forced_compute",
            lean=MockLean(verified=False))
        self.assertEqual(result["n_actual"], 4)
        self.assertEqual(len(lean.calls), 1)
        self.assertEqual(result["invalid_action_requests"], 1)
        self.assertNotIn("LEAN", result["rounds"][3]["allowed_actions"])

    def test_a4_compute_first_gate_then_lean(self):
        result, model, tools, lean, final = run_script(
            [response("PYTHON", "print(2+2)"), response("LEAN", "by exact True.intro")],
            config=BrokerConfig(n_limit=2, t_limit_seconds=10), policy="A4_INTEGRATED_COMPUTE",
            durations={"python": 0.25}, lean=MockLean(verified=True))
        self.assertEqual(result["n_actual"], 2)
        self.assertEqual([item["action"] for item in result["rounds"]], ["PYTHON", "LEAN"])
        self.assertTrue(result["deterministic_gate_satisfied"])
        self.assertEqual(result["gate_round"], 1)
        self.assertEqual(result["gate_tool"], "PYTHON")
        self.assertAlmostEqual(result["t_actual_seconds"], 0.25)
        self.assertEqual(result["v_intermediate_actual"], 1)
        self.assertTrue(result["verified"])

    def test_a4_failed_compute_cannot_jump_to_lean(self):
        class FailedTools(MockToolWorker):
            def run(self, kind, payload, *, timeout):
                self.calls.append({"kind": kind})
                return {"status": "failed", "exit_code": 1, "timeout": False,
                        "output_capped": False, "wall_seconds": 0.0}
        result, model, tools, lean, final = run_script(
            [response("PYTHON", "bad"), response("LEAN", "bad")],
            config=BrokerConfig(n_limit=2, t_limit_seconds=10), policy="A4_INTEGRATED_COMPUTE",
            lean=MockLean(verified=False), tool_worker=FailedTools())
        self.assertEqual(result["n_actual"], 2)
        self.assertFalse(result["deterministic_gate_satisfied"])
        self.assertEqual(len(lean.calls), 0)
        self.assertEqual(result["invalid_action_requests"], 1)
        self.assertNotIn("LEAN", result["rounds"][1]["allowed_actions"])


if __name__ == "__main__":
    unittest.main()
