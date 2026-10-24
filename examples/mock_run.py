"""Run one brokered session end to end with a scripted model and mock workers.

No model, Lean install or Docker is needed:  python examples/mock_run.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.broker import BrokerConfig, run_broker
from harness.docker_worker import MockToolWorker
from harness.model_mock import ScriptedMockModel


def action(kind, payload="", useful=False):
    return json.dumps({"action": kind, "summary": "step", "compute_useful": useful,
                       "payload": payload, "state_update": "state", "confidence": 0.5})


class Lean:
    def run(self, payload, *, timeout):
        return {"status": "ok", "verified": True, "exit_code": 0, "wall_seconds": 0.1}


class Final:
    def verify(self, payload):
        return {"verified": True, "statement_hash_match": True, "lean_calls": 1, "lean_seconds": 0.1}


model = ScriptedMockModel([action("THINK", useful=True),
                           action("PYTHON", "print(2 + 2)"),
                           action("LEAN", "by\n  exact True.intro")])
with tempfile.TemporaryDirectory() as scratch:
    result = run_broker(theorem="theorem target : True := by sorry", policy="A2",
                        model=model, tool_worker=MockToolWorker({"python": 0.2}),
                        lean_worker=Lean(), final_verifier=Final(), scratch=Path(scratch),
                        config=BrokerConfig(n_limit=4, t_limit_seconds=10, v_intermediate_limit=6))
print(json.dumps({k: result[k] for k in sorted(result) if k != "rounds"}, indent=2, default=str))
print("actions:", [r.get("action") for r in result["rounds"]])
