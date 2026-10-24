from __future__ import annotations
import json, os, shutil, subprocess, time
from dataclasses import dataclass
from pathlib import Path
from .budgets import Budget
from .logging import EventLogger

@dataclass
class ToolResult:
    tool_type: str; command: list[str]; stdout: str; stderr: str; exit_code: int|None; timed_out: bool; wall_seconds: float

def _verifier_config():
    path = Path(__file__).parents[1] / "config" / "verifier_environment.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _lake_derived_lean_path():
    cfg = _verifier_config()
    root = Path(cfg["project_root"]) if cfg.get("project_root") else None
    if root is None or not root.is_dir():
        return None
    entries = []
    packages = root / ".lake" / "packages"
    if packages.is_dir():
        for package in sorted(packages.iterdir(), key=lambda p: p.name.lower()):
            candidate = package / ".lake" / "build" / "lib" / "lean"
            if candidate.is_dir():
                entries.append(str(candidate))
    project_lib = root / ".lake" / "build" / "lib" / "lean"
    if project_lib.is_dir():
        entries.append(str(project_lib))
    toolchain = os.environ.get("THINKCOMPUTE_LEAN_TOOLCHAIN")
    toolchain_lib = Path(toolchain) / "lib" / "lean" if toolchain else None
    if toolchain_lib is not None and toolchain_lib.is_dir():
        entries.append(str(toolchain_lib))
    return os.pathsep.join(entries) if entries else None

def default_lean():
    configured=os.environ.get("THINKCOMPUTE_LEAN")
    if configured: return configured
    cfg = _verifier_config()
    configured = cfg.get("lean_executable")
    if configured and os.path.isfile(configured): return configured
    return shutil.which("lean") or "lean"

def run_tool(tool_type, command, *, cwd, timeout, logger: EventLogger, budget: Budget|None=None):
    started=time.time(); timed_out=False; stdout=stderr=""; code=None
    env={k:v for k,v in os.environ.items() if k not in {"HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"}}
    env["NO_PROXY"]="*"; env["no_proxy"]="*"
    if tool_type == "lean":
        lean_path = os.environ.get("THINKCOMPUTE_LEAN_PATH") or _lake_derived_lean_path()
        if lean_path:
            env["LEAN_PATH"] = lean_path
    try:
        cp=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,errors="replace",timeout=timeout)
        stdout,stderr,code=cp.stdout,cp.stderr,cp.returncode
    except FileNotFoundError as e: stderr=str(e); code=None
    except subprocess.TimeoutExpired as e: timed_out=True; stdout=e.stdout or ""; stderr=e.stderr or ""
    ended=time.time(); result=ToolResult(tool_type,list(command),stdout,stderr,code,timed_out,ended-started)
    if logger: logger.tool(tool_type,list(command),started,ended,code,timed_out,stdout,stderr)
    if budget:
        if tool_type == "lean": budget.charge_lean(result.wall_seconds)
        else: budget.charge_exploration(result.wall_seconds)
    return result

def python_tool(code, *, cwd, timeout, logger, budget=None):
    return run_tool("python", ["python", "-c", code], cwd=cwd, timeout=timeout, logger=logger, budget=budget)

def sage_tool(code, *, cwd, timeout, logger, budget=None):
    return run_tool("sage", ["sage", "-c", code], cwd=cwd, timeout=timeout, logger=logger, budget=budget)

def sat_tool(smt2, *, cwd, timeout, logger, budget=None):
    # Prefer a standalone solver; otherwise use the installed Python z3 package.
    if shutil.which("z3"):
        return run_tool("sat", ["z3", "-in"], cwd=cwd, timeout=timeout, logger=logger, budget=budget)
    return run_tool("sat", ["python", "-c", "import z3; print(z3.get_version_string())"], cwd=cwd, timeout=timeout, logger=logger, budget=budget)

def lean_tool(file, *, cwd, timeout, logger, budget=None, lean="lean"):
    return run_tool("lean", [lean, str(file)], cwd=cwd, timeout=timeout, logger=logger, budget=budget)
