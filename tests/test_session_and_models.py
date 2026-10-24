from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from harness.codex_events import parse_jsonl
from harness.docker_worker import MockToolWorker
from harness.errors import ModelInvocationError
from harness.model_codex import CodexModelAdapter
from harness.model_openai import OpenAICompatibleModel
from harness.session import _mock_model, check, main, problem_from_file, run_session

ACTION = json.dumps({"action": "THINK", "summary": "s", "compute_useful": False,
                     "payload": "", "state_update": "u", "confidence": 0.5})
TARGET = "namespace TC_AB12\ntheorem target : True := by\n  sorry\nend TC_AB12\n"


class FakeLean:
    def run(self, payload, *, timeout):
        return {"status": "ok", "verified": True, "exit_code": 0, "wall_seconds": 0.1}


class FakeFinal:
    def verify(self, payload):
        return {"verified": True, "statement_hash_match": True, "lean_calls": 1, "lean_seconds": 0.1}


class Handler(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                             "body": json.loads(self.rfile.read(length))})
        body = json.dumps({"id": "cmpl-1", "choices": [{"message": {"content": ACTION}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
                          if Handler.status == 200 else {"error": "denied"}).encode()
        self.send_response(Handler.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        Handler.seen, Handler.status = [], 200
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_openai_compatible_round_trip(self):
        os.environ["TC_TEST_KEY"] = "secret-value"
        self.addCleanup(os.environ.pop, "TC_TEST_KEY", None)
        model = OpenAICompatibleModel("m1", base_url=self.base, api_key_env="TC_TEST_KEY", temperature=0.0)
        with tempfile.TemporaryDirectory() as d:
            response = model.invoke_once("PROMPT", scratch=Path(d), timeout=5)
        self.assertEqual(response.raw_text, ACTION)
        self.assertEqual(response.provider_usage["completion_tokens"], 4)
        seen = Handler.seen[0]
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer secret-value")
        self.assertEqual(seen["body"]["model"], "m1")
        self.assertEqual(seen["body"]["messages"][-1]["content"], "PROMPT")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        self.assertNotIn("secret-value", json.dumps(response.provider_usage))

    def test_openai_compatible_errors_are_raised_not_retried(self):
        os.environ["TC_TEST_KEY"] = "k"
        self.addCleanup(os.environ.pop, "TC_TEST_KEY", None)
        Handler.status = 401
        model = OpenAICompatibleModel("m1", base_url=self.base, api_key_env="TC_TEST_KEY")
        with self.assertRaises(ModelInvocationError):
            model.invoke_once("p", scratch=Path("."), timeout=5)
        self.assertEqual(len(Handler.seen), 1)
        os.environ.pop("TC_TEST_KEY")
        with self.assertRaises(ModelInvocationError):
            model.invoke_once("p", scratch=Path("."), timeout=5)

    def test_codex_command_is_fresh_readonly_and_schema_bound(self):
        command = CodexModelAdapter("some-model", reasoning_effort="low").build_command(
            Path("s"), Path("s/final.json"), Path("schema.json"))
        self.assertEqual(command[:2], ["codex", "exec"])
        for flag in ("--ephemeral", "--json", "--output-schema"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertNotIn("resume", command)
        self.assertEqual(command[-1], "-")

    def test_codex_missing_cli_is_an_invocation_error(self):
        adapter = CodexModelAdapter("m", command="definitely-not-a-real-codex-binary")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ModelInvocationError):
                adapter.invoke_once("p", scratch=Path(d), timeout=5)

    def test_codex_event_parser(self):
        raw = "\n".join(json.dumps(e) for e in (
            {"type": "thread.started", "thread_id": "t1"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "proof"}},
            {"type": "turn.completed", "usage": {"output_tokens": 3}}))
        parsed = parse_jsonl(raw)
        self.assertEqual((parsed.session_id, parsed.codex_turns, parsed.final_text), ("t1", 1, "proof"))
        self.assertEqual(parsed.usage["output_tokens"], 3)


class SessionTests(unittest.TestCase):
    def make_problem(self, d):
        main = Path(d) / "Main.lean"
        main.write_text(TARGET)
        return problem_from_file(main)

    def test_run_session_writes_ledger_and_events(self):
        with tempfile.TemporaryDirectory() as d:
            result = run_session(problem=self.make_problem(d), policy="A2", model=_mock_model(),
                                 out_dir=Path(d) / "out", n=4, tool_worker=MockToolWorker(),
                                 lean_worker=FakeLean(), final_verifier=FakeFinal(), model_name="mock")
            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["verified"])
            saved = json.loads(Path(result["result_path"]).read_text())
            self.assertEqual((saved["model"], saved["policy"], saved["termination_reason"]),
                             ("mock", "A2", "lean_verified"))
            events = Path(result["events_path"]).read_text().splitlines()
            self.assertGreaterEqual(len(events), 2)

    def test_failed_session_still_writes_a_record(self):
        class Boom:
            def invoke_once(self, prompt, *, scratch, timeout):
                raise ModelInvocationError("endpoint down")
        with tempfile.TemporaryDirectory() as d:
            result = run_session(problem=self.make_problem(d), policy="A2", model=Boom(),
                                 out_dir=Path(d) / "out", tool_worker=MockToolWorker(),
                                 lean_worker=FakeLean(), final_verifier=FakeFinal())
            self.assertEqual(result["status"], "infrastructure_error")
            self.assertIn("endpoint down", json.loads(Path(result["result_path"]).read_text())["error"])

    def test_unknown_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                run_session(problem=self.make_problem(d), policy="B7", model=_mock_model(), out_dir=d)

    def test_problem_from_file_hashes_match_verifier_expectation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            problem = self.make_problem(d)
            expected = hashlib.sha256(problem.main_path.read_text().encode()).hexdigest()
            self.assertEqual(problem.main_sha256, expected)
            self.assertEqual(problem.statement_sha256, expected)

    def test_check_reports_components_and_cli_requires_model(self):
        report = check()
        self.assertIn("lean", report)
        self.assertTrue(report["sympy"][0] and report["z3"][0])
        with self.assertRaises(SystemExit):
            main(["--lean-file", "x.lean"])


if __name__ == "__main__":
    unittest.main()
