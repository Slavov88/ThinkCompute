from __future__ import annotations
import hashlib, re
from pathlib import Path
from .benchmark import Problem
from .budgets import Budget
from .logging import EventLogger
from .tools import default_lean, run_tool

FORBIDDEN=[r"\bsorry\b",r"\badmit\b",r"\bby\?\b",r"\b(?:exact|simp|aesop|omega|linarith|norm_num)\?\b",r"\baxiom\b",r"\bunsafe\b",r"\b(?:namespace|end|theorem|lemma|def)\b",r":="]

def verify(problem: Problem, candidate: str, *, scratch: Path, logger: EventLogger, budget: Budget, lean=None):
    lean=lean or default_lean()
    target=problem.main_path.read_text(encoding="utf-8")
    frozen=hashlib.sha256(target.encode()).hexdigest()
    hash_match=frozen == problem.main_sha256 == problem.statement_sha256
    bad=[p for p in FORBIDDEN if re.search(p,candidate)]
    combined=target.replace("by\n  sorry",candidate,1)
    if combined == target: return {"verified":False,"reason":"placeholder_not_found","statement_hash_match":hash_match,"axioms":[],"lean_calls":0,"lean_seconds":0.0}
    f=scratch/"Candidate.lean"; f.write_text(combined,encoding="utf-8",newline="\n")
    result=run_tool("lean",[lean,str(f)],cwd=scratch,timeout=budget.lean_process_limit,logger=logger,budget=budget)
    axioms=[]
    ns=re.search(r"\bnamespace\s+(TC_[A-F0-9]+)",target)
    lean_calls=1
    if result.exit_code==0 and not bad and hash_match and ns:
        af=scratch/"AxiomAudit.lean"; af.write_text(combined+f"\n#print axioms {ns.group(1)}.target\n",encoding="utf-8",newline="\n")
        ar=run_tool("lean",[lean,str(af)],cwd=scratch,timeout=budget.lean_process_limit,logger=logger,budget=budget)
        lean_calls += 1
        axioms=re.findall(r"axioms:\s*(.*)",ar.stdout+ar.stderr)
    return {"verified":result.exit_code==0 and not result.timed_out and not bad and hash_match,"reason":"" if result.exit_code==0 else "lean_failure","statement_hash_match":hash_match,"sorry_found":bool(bad),"axioms":axioms,"lean_calls":lean_calls,"lean_seconds":result.wall_seconds}
