from __future__ import annotations
import json, os, subprocess, sys, time

def main():
    if "--" not in sys.argv or len(sys.argv) < 5: return 2
    _, kind, limit, log, *rest = sys.argv; sep=rest.index("--"); command=rest[sep+1:]
    if kind == "lean": limit = min(float(limit), 20.0)
    else: limit = min(float(limit), 30.0)
    started=time.time(); timeout=False; code=None
    env={k:v for k,v in os.environ.items() if k.lower() not in {"http_proxy","https_proxy","all_proxy"}}; env["NO_PROXY"]="*"
    try:
        p=subprocess.run(command,env=env,timeout=limit); code=p.returncode
    except FileNotFoundError: code=127
    except subprocess.TimeoutExpired: timeout=True; code=124
    record={"tool":kind,"command":command,"start_timestamp":started,"end_timestamp":time.time(),"wall_seconds":time.time()-started,"exit_code":code,"timeout":timeout}
    with open(log,"a",encoding="utf-8") as f: f.write(json.dumps(record)+"\n")
    return code if code is not None else 1
if __name__=="__main__": raise SystemExit(main())
