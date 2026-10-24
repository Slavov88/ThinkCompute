"""Run one brokered session from the command line.

    python -m harness.session --check
    python -m harness.session --lean-file Main.lean --model openai:gpt-4o --policy A2
    python -m harness.session --benchmark DIR --problem-id ID --model codex:MODEL --policy A4

Every session writes ``<out>/<run_id>.json`` (the ledger and per-round trace) and
``<out>/<run_id>.events.jsonl`` (the broker and tool event log). A failed session still
writes its result file with ``status: infrastructure_error``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .benchmark import Benchmark, Problem
from .broker import BrokerConfig, run_broker
from .final_verifier import IndependentFinalVerifier
from .logging import EventLogger
from .model_mock import ScriptedMockModel
from .native_worker import NativeRestrictedToolWorker
from .tools import default_lean

POLICY_RE = re.compile(r"^A[0-4](_[A-Za-z_]+)?$")


def problem_from_file(lean_file: str | Path, statement_file: str | Path | None = None) -> Problem:
    """A single ``Main.lean`` as a problem. Its hashes are taken from the file itself."""
    main = Path(lean_file).resolve()
    digest = hashlib.sha256(main.read_text(encoding="utf-8").encode()).hexdigest()
    statement = Path(statement_file).resolve() if statement_file else main
    name = main.parent.name if main.name == "Main.lean" else main.stem
    return Problem(name, "adhoc", main, statement, digest, digest)


def theorem_text(problem: Problem) -> str:
    main_text = problem.main_path.read_text(encoding="utf-8")
    if problem.statement_path == problem.main_path:
        return main_text
    return problem.statement_path.read_text(encoding="utf-8") + "\n\nFormal Lean target:\n" + main_text


def run_session(*, problem: Problem, policy: str, model, out_dir: str | Path,
                n: int = 8, t: float = 10.0, v: int = 6,
                deterministic_call_limit: float = 5.0, lean_process_limit: float = 60.0,
                max_session_seconds: float = 600.0, lean: str | None = None,
                tool_worker=None, lean_worker=None, final_verifier=None,
                model_name: str = "", run_id: str | None = None) -> dict:
    """Assemble workers and the independent verifier, run the broker, write the record."""
    if not POLICY_RE.match(policy):
        raise ValueError(f"unknown policy {policy!r}; use A0 to A4")
    out_dir = Path(out_dir)
    run_id = run_id or "session-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    scratch = out_dir / run_id / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    logger = EventLogger(out_dir / f"{run_id}.events.jsonl")
    started = time.perf_counter()
    try:
        main_text = problem.main_path.read_text(encoding="utf-8")
        if tool_worker is None:
            tool_worker = NativeRestrictedToolWorker(scratch_root=scratch, target_text=main_text,
                                                     lean_executable=lean, logger=logger)
            lean_worker = lean_worker or tool_worker.as_lean_worker()
        if final_verifier is None:
            final_verifier = IndependentFinalVerifier(problem, scratch, logger, lean=lean,
                                                      lean_process_limit=lean_process_limit)
        result = run_broker(
            theorem=theorem_text(problem), policy=policy, model=model, tool_worker=tool_worker,
            lean_worker=lean_worker, final_verifier=final_verifier, scratch=scratch,
            config=BrokerConfig(n_limit=n, t_limit_seconds=t,
                                deterministic_call_limit_seconds=deterministic_call_limit,
                                v_intermediate_limit=v, lean_process_limit_seconds=lean_process_limit,
                                max_session_seconds=max_session_seconds),
            logger=logger, run_id=run_id)
        result["status"] = "complete"
    except Exception as exc:
        result = {"run_id": run_id, "status": "infrastructure_error", "error": repr(exc),
                  "verified": False}
    result.update({"problem_id": problem.problem_id, "split": problem.split, "model": model_name,
                   "policy": policy, "events_path": str(logger.path)})
    result["session_wall_seconds"] = time.perf_counter() - started
    result_path = out_dir / f"{run_id}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
                           encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def _mock_model() -> ScriptedMockModel:
    """Smoke-test model: THINK, then the trivial proof. Only suits a policy that starts with THINK."""
    def act(kind, payload=""):
        return json.dumps({"action": kind, "summary": "mock", "compute_useful": False,
                           "payload": payload, "state_update": "mock", "confidence": 1.0})
    return ScriptedMockModel([act("THINK"), act("LEAN", "by\n  exact True.intro")])


def build_model(spec: str, args):
    kind, _, name = spec.partition(":")
    if kind == "mock":
        return _mock_model()
    if kind == "codex" and name:
        from .model_codex import CodexModelAdapter
        return CodexModelAdapter(name, reasoning_effort=args.reasoning_effort, command=args.codex_command)
    if kind == "openai" and name:
        from .model_openai import OpenAICompatibleModel
        return OpenAICompatibleModel(name, base_url=args.base_url, api_key_env=args.api_key_env,
                                     temperature=args.temperature)
    raise SystemExit(f"unknown --model {spec!r}; use mock, codex:<model> or openai:<model>")


def check(lean: str | None = None) -> dict[str, tuple[bool, str]]:
    """Report which parts of a real run are available. Never prints credentials."""
    import os
    report: dict[str, tuple[bool, str]] = {"python": (sys.version_info >= (3, 10), sys.version.split()[0])}
    for module in ("sympy", "z3"):
        try:
            imported = __import__(module)
            report[module] = (True, getattr(imported, "__version__", None) or imported.get_version_string())
        except ImportError:
            report[module] = (False, "not installed (pip install -r requirements.txt)")
    exe = lean or default_lean()
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        report["lean"] = (done.returncode == 0, (done.stdout or done.stderr).strip()[:80])
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["lean"] = (False, f"{exe}: {exc.__class__.__name__}")
    report["codex_cli"] = (shutil.which("codex") is not None, "codex on PATH" if shutil.which("codex") else "not on PATH")
    report["OPENAI_API_KEY"] = ("OPENAI_API_KEY" in os.environ, "set" if "OPENAI_API_KEY" in os.environ else "not set")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m harness.session", description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true", help="report available components and exit")
    src = p.add_argument_group("problem (one of)")
    src.add_argument("--lean-file", help="a Main.lean with a `by` / `sorry` placeholder")
    src.add_argument("--statement-file", help="optional problem statement shown to the model")
    src.add_argument("--benchmark", help="directory with manifest.json and problems/<id>/")
    src.add_argument("--problem-id")
    src.add_argument("--allow-split", help="refuse problems outside this split")
    p.add_argument("--policy", default="A2", help="A0 to A4 (default A2)")
    p.add_argument("--model", help="mock | codex:<model> | openai:<model>")
    p.add_argument("--n", type=int, default=8, help="neural rounds (N)")
    p.add_argument("--t", type=float, default=10.0, help="deterministic compute seconds (T)")
    p.add_argument("--v", type=int, default=6, help="intermediate Lean attempts (V)")
    p.add_argument("--deterministic-call-limit", type=float, default=5.0)
    p.add_argument("--lean-process-limit", type=float, default=60.0)
    p.add_argument("--max-session-seconds", type=float, default=600.0)
    p.add_argument("--lean", help="path to the lean executable")
    p.add_argument("--out", default="results/sessions")
    p.add_argument("--base-url", help="OpenAI-compatible base URL (default https://api.openai.com/v1)")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--temperature", type=float)
    p.add_argument("--codex-command", default="codex")
    p.add_argument("--reasoning-effort", default="low")
    args = p.parse_args(argv)

    if args.check:
        report = check(args.lean)
        for name, (ok, detail) in report.items():
            print(f"{'ok  ' if ok else 'MISS'} {name}: {detail}")
        return 0 if all(report[k][0] for k in ("python", "sympy", "z3", "lean")) else 1
    if not args.model:
        p.error("--model is required")
    if args.lean_file:
        problem = problem_from_file(args.lean_file, args.statement_file)
    elif args.benchmark and args.problem_id:
        problem = Benchmark(Path(args.benchmark)).select(args.problem_id, allow_split=args.allow_split)
    else:
        p.error("give --lean-file, or --benchmark with --problem-id")
    result = run_session(problem=problem, policy=args.policy, model=build_model(args.model, args),
                         out_dir=args.out, n=args.n, t=args.t, v=args.v,
                         deterministic_call_limit=args.deterministic_call_limit,
                         lean_process_limit=args.lean_process_limit,
                         max_session_seconds=args.max_session_seconds, lean=args.lean,
                         model_name=args.model)
    print(json.dumps({"run_id": result["run_id"], "status": result["status"],
                      "verified": result.get("verified", False),
                      "termination_reason": result.get("termination_reason"),
                      "n_actual": result.get("n_actual"), "t_actual_seconds": result.get("t_actual_seconds"),
                      "v_intermediate_actual": result.get("v_intermediate_actual"),
                      "result": result["result_path"], "events": result["events_path"],
                      **({"error": result["error"]} if "error" in result else {})}, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
