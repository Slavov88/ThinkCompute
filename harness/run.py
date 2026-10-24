from __future__ import annotations
import json, re, time, uuid
from pathlib import Path
from .benchmark import Benchmark
from .budgets import Budget
from .logging import EventLogger
from .sandbox import Sandbox
from .verifier import verify

def proof_body(text: str) -> str:
    blocks=re.findall(r"```(?:lean)?\s*\n?(.*?)```", text, flags=re.S|re.I)
    return (blocks[-1] if blocks else text).strip()

def make_run(problem, model, *, root: Path, adapter, policy="A2_structured", do_verify=True):
    run_id="run-"+uuid.uuid4().hex; started=time.time(); scratch_base=root/"results"/"scratch"; raw=root/"results"/"raw"
    logger=EventLogger(raw/(run_id+".events.jsonl")); budget=Budget(); termination=""; model_result=None; verification={"verified":False}
    with Sandbox(scratch_base) as scratch:
        try:
            text=problem.statement_path.read_text(encoding="utf-8") + "\n\nFormal Lean target:\n" + problem.main_path.read_text(encoding="utf-8")
            model_result=adapter.run(text,scratch=scratch,policy=policy,timeout=budget.session_limit)
            if do_verify and model_result.text:
                verification=verify(problem, proof_body(model_result.text), scratch=scratch, logger=logger, budget=budget)
            termination=model_result.termination_reason
        except Exception as e:
            termination="infrastructure_error:"+type(e).__name__; logger.event({"event":"error","error":repr(e)})
    ended=time.time()
    result={"run_id":run_id,"problem_id":problem.problem_id,"split":problem.split,"model":model,"policy":policy,"neural_round_limit":budget.neural_cycle_limit,"neural_rounds_actual":getattr(model_result,"decision_cycles",None),"input_tokens":None,"output_tokens":None,"reasoning_tokens":None,"total_provider_tokens":None,"T_explore_limit_seconds":budget.exploration_limit,"python_seconds":0.0,"sage_seconds":0.0,"sat_seconds":0.0,"lean_call_limit":budget.lean_call_limit,"lean_calls":budget.lean_calls,"lean_seconds":budget.lean_seconds,"total_wall_seconds":ended-started,"verified":verification.get("verified",False),"termination_reason":termination,"raw_provider_usage":getattr(model_result,"provider_usage",{}) if model_result else {},"model_calls":getattr(model_result,"model_calls",None)}
    (raw/(run_id+".json")).write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    if model_result and model_result.text:
        (raw/(run_id+".transcript.txt")).write_text(model_result.text,encoding="utf-8")
    return result
