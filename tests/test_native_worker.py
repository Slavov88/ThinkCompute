from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from harness.logging import EventLogger
from harness.native_worker import (
    MAX_OUTPUT_BYTES, NativeRestrictedToolWorker, NativeValidationError,
    validate_lean_candidate, validate_python,
)


import shutil, subprocess

def _lean_works():
    exe=shutil.which("lean")
    if not exe: return False
    try: return subprocess.run([exe,"--version"],capture_output=True,timeout=20).returncode==0
    except Exception: return False

HAS_LEAN=_lean_works()


class NativeWorkerTests(unittest.TestCase):
    def worker(self, root: Path, target: str | None = None):
        return NativeRestrictedToolWorker(scratch_root=root, target_text=target)

    @unittest.skipUnless(HAS_LEAN,"lean not installed")
    def test_python_sympy_z3_and_lean_work(self):
        target = "theorem target : True := by\n  sorry\n"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            logger = EventLogger(root / "events.jsonl")
            worker = NativeRestrictedToolWorker(scratch_root=root, target_text=target, logger=logger)
            arithmetic = worker.run_python("print(2 + 2)", timeout=10)
            sympy = worker.run_python("import sympy as s; print(s.factorint(84))", timeout=20)
            smt = worker.run_smt("import z3; x=z3.Int('x'); s=z3.Solver(); s.add(x == 7); print(s.check()); print(s.model()[x])", timeout=20)
            lean = worker.run_lean("by\n  exact True.intro", timeout=20)
            self.assertEqual(arithmetic["exit_code"], 0)
            self.assertIn("4", arithmetic["stdout"])
            self.assertEqual(sympy["exit_code"], 0)
            self.assertIn("{2: 2, 3: 1, 7: 1}", sympy["stdout"])
            self.assertEqual(smt["exit_code"], 0)
            self.assertIn("sat", smt["stdout"])
            self.assertEqual(lean["exit_code"], 0)
            self.assertEqual(len(logger.path.read_text(encoding="utf-8").splitlines()), 4)

    def test_import_builtins_and_introspection_are_rejected(self):
        rejected = [
            "import os",
            "import pathlib",
            "import subprocess",
            "import socket",
            "import importlib",
            "open('x', 'w')",
            "exec('print(1)')",
            "eval('1+1')",
            "__import__('os')",
            "print(globals())",
            "print((1).__class__)",
        ]
        for code in rejected:
            with self.subTest(code=code):
                with self.assertRaises(NativeValidationError):
                    validate_python(code)

    def test_sensitive_paths_are_rejected_before_process_start(self):
        os.environ["THINKCOMPUTE_DENY_MARKERS"] = "private-vault"
        self.addCleanup(os.environ.pop, "THINKCOMPUTE_DENY_MARKERS", None)
        for code in (
            "print('/srv/private-vault/answers')",
            "print('C:/Users/someone')",
            "print('/mnt/d')",
        ):
            with self.assertRaises(NativeValidationError):
                validate_python(code)
        with self.assertRaises(NativeValidationError):
            validate_lean_candidate("by\n  exact (unsafeCast True)")

    def test_timeout_output_cap_and_fresh_cwd(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            worker = self.worker(root)
            slow = worker.run_python("while True: pass", timeout=0.15)
            self.assertTrue(slow["timeout"])
            capped = worker.run_python(f"print('x' * {MAX_OUTPUT_BYTES + 100})", timeout=10)
            self.assertTrue(capped["output_capped"])
            first = worker.run_python("print(1)", timeout=10)
            second = worker.run_python("print(2)", timeout=10)
            self.assertNotEqual(first["cwd"], second["cwd"])
            self.assertNotIn("private-area", json.dumps(first).lower())

    def test_smt_and_lean_timeout_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            worker = self.worker(root, "theorem target : True := by\n  sorry\n")
            smt = worker.run_smt("while True: pass", timeout=0.15)
            self.assertTrue(smt["timeout"])
            fake = root / "fake-lean.cmd"
            fake.write_text("@echo off\r\npython -c \"import time; time.sleep(2)\"\r\n", encoding="utf-8")
            timed_worker = NativeRestrictedToolWorker(scratch_root=root,
                                                       target_text="theorem target : True := by\n  sorry\n",
                                                       lean_executable=str(fake))
            lean = timed_worker.run_lean("by\n  exact True.intro", timeout=0.15)
            self.assertTrue(lean["timeout"])

    @unittest.skipUnless(HAS_LEAN,"lean not installed")
    def test_lean_worker_adapter_matches_broker_interface(self):
        with tempfile.TemporaryDirectory() as raw:
            worker = self.worker(Path(raw), "theorem target : True := by\n  sorry\n")
            result = worker.as_lean_worker().run("by\n  exact True.intro", timeout=20)
            self.assertTrue(result["verified"])
            self.assertEqual(result["exit_code"], 0)

    def test_unicode_stdout_is_stable_under_worker_encoding(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.worker(Path(raw)).run_python("print('≤ ← ⟨x⟩')", timeout=10)
            self.assertEqual(result["status"], "ok")
            self.assertIn("≤ ← ⟨x⟩", result["stdout"])


if __name__ == "__main__":
    unittest.main()
