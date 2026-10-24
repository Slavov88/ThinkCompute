from __future__ import annotations
import hashlib, json, shutil, tempfile, time, unittest, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from harness.benchmark import Benchmark
from harness.budgets import Budget
from harness.logging import EventLogger
from harness.sandbox import Sandbox
from harness.tools import python_tool, sage_tool, sat_tool
from harness.verifier import verify
from harness.narrow_tools import install_windows_wrappers, run_narrow

import subprocess


def _lean_works():
    exe=shutil.which("lean")
    if not exe: return False
    try: return subprocess.run([exe,"--version"],capture_output=True,timeout=20).returncode==0
    except Exception: return False


HAS_LEAN=_lean_works()


class HarnessTests(unittest.TestCase):
    def test_benchmark_hash_and_split_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"bench"; ids=[("P1","dev"),("P2","other")]; entries=[]
            for pid,split in ids:
                (root/"problems"/pid).mkdir(parents=True); (root/"problems"/pid/"Main.lean").write_text("m"); (root/"problems"/pid/"problem.md").write_text("s")
                entries.append({"id":pid,"split":split,"main_sha256":"m","statement_sha256":"s"})
            (root/"manifest.json").write_text(json.dumps({"problems":entries}))
            b=Benchmark(root); self.assertEqual(len(b.problems),2); self.assertEqual(len(b.split("dev")),1)
            self.assertEqual(b.select("P1",allow_split="dev").problem_id,"P1")
            with self.assertRaises(PermissionError): b.select("P2",allow_split="dev")
            with self.assertRaises(ValueError): Benchmark(root,expected_manifest_sha256="0"*64)
            Benchmark(root,expected_manifest_sha256=b.manifest_sha256)

    def test_sandbox_is_fresh_and_removed(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            with Sandbox(base) as s: path=s.path; self.assertTrue(path.is_dir())
            self.assertFalse(path.exists())

    def test_tool_timeout_and_accounting(self):
        with tempfile.TemporaryDirectory() as d:
            log=EventLogger(Path(d)/"events.jsonl"); b=Budget()
            r=python_tool("print('ok')",cwd=Path(d),timeout=5,logger=log,budget=b)
            self.assertEqual(r.exit_code,0); self.assertEqual(r.stdout.strip(),"ok"); self.assertGreaterEqual(b.exploration_seconds,0)
            r=python_tool("import time; time.sleep(2)",cwd=Path(d),timeout=.1,logger=log,budget=Budget())
            self.assertTrue(r.timed_out)
            sr=sage_tool("print(1)",cwd=Path(d),timeout=1,logger=log,budget=Budget())
            self.assertTrue(sr.exit_code is None or sr.exit_code != 0)
            zr=sat_tool("",cwd=Path(d),timeout=5,logger=log,budget=Budget())
            self.assertEqual(zr.exit_code,0)
            events=Path(d)/"events.jsonl"; self.assertEqual(len(events.read_text().splitlines()),4)

    @unittest.skipUnless(HAS_LEAN,"lean not installed")
    def test_verifier_valid_sorry_modified_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); main=d/"Main.lean"; text="namespace TC_TEST\ntheorem target : True := by\n  sorry\nend TC_TEST\n"; main.write_text(text)
            from harness.benchmark import Problem
            h=hashlib.sha256(text.encode()).hexdigest(); p=Problem("TC-TEST","dev",main,d/"problem.md",h,h)
            with Sandbox(d/"scratch") as s:
                self.assertTrue(verify(p,"by\n  exact True.intro",scratch=s.path,logger=EventLogger(d/"v.jsonl"),budget=Budget())["verified"])
                self.assertFalse(verify(p,"by\n  sorry",scratch=s.path,logger=EventLogger(d/"s.jsonl"),budget=Budget())["verified"])
                self.assertFalse(verify(p,"not Lean",scratch=s.path,logger=EventLogger(d/"m.jsonl"),budget=Budget())["verified"])
            main.write_text(text.replace(": True",": False"));
            with Sandbox(d/"scratch2") as s:
                result=verify(p,"by\n  exact False.intro",scratch=s.path,logger=EventLogger(d/"h.jsonl"),budget=Budget())
                self.assertFalse(result["statement_hash_match"]); self.assertFalse(result["verified"])

    @unittest.skipUnless(HAS_LEAN and sys.platform=="win32","needs lean and Windows wrappers")
    def test_narrow_worker_tools_and_path_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"worker"; root.mkdir(); log=root/"tools.jsonl"
            (root/"py.py").write_text("print('python-ok')\n")
            (root/"sat.py").write_text("import z3; print(z3.Solver())\n")
            (root/"Main.lean").write_text("theorem target : True := by\n  trivial\n")
            py=run_narrow("python","py.py",root=root,timeout=5,log=log)
            sat=run_narrow("sat","sat.py",root=root,timeout=5,log=log)
            lean=run_narrow("lean","Main.lean",root=root,timeout=10,log=log)
            self.assertEqual(py["exit_code"],0); self.assertEqual(sat["exit_code"],0); self.assertEqual(lean["exit_code"],0)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()),3)
            bindir=install_windows_wrappers(root,root/"bin",log)
            wrapped=__import__("subprocess").run([str(bindir/"tc_python.cmd"),"py.py"],cwd=root,capture_output=True,text=True)
            self.assertEqual(wrapped.returncode,0); self.assertIn("python-ok",wrapped.stdout)
            with self.assertRaises(ValueError): run_narrow("python",str(Path.cwd()/"outside.py"),root=root,timeout=1,log=log)
            with self.assertRaises(ValueError): run_narrow("python","..\\private\\answer.py",root=root,timeout=1,log=log)
            for name in ("private-area", "source-copy", "other-problem"):
                outside=Path(d)/name; outside.mkdir(); (outside/"x.py").write_text("print('blocked')\n")
                with self.assertRaises(ValueError): run_narrow("python",str(outside/"x.py"),root=root,timeout=1,log=log)

    def test_narrow_tool_network_and_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); log=root/"tools.jsonl"
            (root/"net.py").write_text("import urllib.request; urllib.request.urlopen('http://127.0.0.1:9', timeout=1)\n")
            (root/"slow.py").write_text("import time; time.sleep(2)\n")
            net=run_narrow("python","net.py",root=root,timeout=3,log=log)
            slow=run_narrow("python","slow.py",root=root,timeout=.1,log=log)
            self.assertNotEqual(net["exit_code"],0); self.assertIn("network disabled",net["stderr"])
            self.assertTrue(slow["timeout"])

if __name__=="__main__": unittest.main()
