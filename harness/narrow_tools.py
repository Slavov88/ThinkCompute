from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from .tools import default_lean
except ImportError:  # direct invocation from a generated Windows wrapper
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from harness.tools import default_lean


KINDS = {"python", "sage", "sat", "lean"}
MAX_OUTPUT_BYTES = 4 * 1024 * 1024

_NO_NETWORK_RUNNER = (
    "import runpy, socket, sys; "
    "blocked=lambda *a,**k: (_ for _ in ()).throw(OSError('network disabled')); "
    "socket.socket=type('BlockedSocket',(),{'__init__':blocked}); "
    "socket.create_connection=blocked; socket.getaddrinfo=blocked; "
    "target=sys.argv[1]; sys.argv=[target]; runpy.run_path(target, run_name='__main__')"
)


def _inside(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("tool input must be a relative path without traversal")
    candidate = (root / raw).resolve(strict=True)
    if candidate == root or root not in candidate.parents:
        raise ValueError("tool input escaped worker root")
    if any(part.is_symlink() for part in [candidate, *candidate.parents]):
        raise ValueError("symlinked tool inputs are not permitted")
    return candidate


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)


def run_narrow(kind: str, target: str, *, root: Path, timeout: float,
               log: Path) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unsupported tool kind: {kind}")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("worker root does not exist")
    file = _inside(root, target)
    if kind == "python":
        command = [sys.executable, "-c", _NO_NETWORK_RUNNER, str(file)]
    elif kind == "sat":
        # SAT/SMT scripts use the installed Python z3 package. This gives a
        # fixed interface even when no standalone z3 executable is installed.
        command = [sys.executable, "-c", _NO_NETWORK_RUNNER, str(file)]
    elif kind == "sage":
        executable = shutil.which("sage")
        if not executable:
            command = ["__missing_sage__", str(file)]
        else:
            command = [executable, "-python", "-c", _NO_NETWORK_RUNNER, str(file)]
    else:
        command = [default_lean(), str(file)]

    started = time.time()
    timed_out = False
    stdout = stderr = ""
    exit_code: int | None = None
    try:
        process = subprocess.Popen(command, cwd=root, env=_env(),
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            exit_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            _kill_tree(process.pid)
            extra_out, extra_err = process.communicate()
            stdout += extra_out or ""
            stderr += extra_err or ""
            exit_code = process.returncode
    except FileNotFoundError as exc:
        stderr = str(exc)
        exit_code = 127
    ended = time.time()
    stdout_raw = stdout.encode("utf-8")
    stderr_raw = stderr.encode("utf-8")
    output_capped = len(stdout_raw) > MAX_OUTPUT_BYTES or len(stderr_raw) > MAX_OUTPUT_BYTES
    if output_capped:
        stdout = stdout_raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr += "\noutput cap exceeded\n"
        exit_code = 125
    record = {
        "tool": kind,
        "target": target,
        "command": command,
        "start_timestamp": started,
        "end_timestamp": ended,
        "wall_seconds": ended - started,
        "exit_code": exit_code,
        "timeout": timed_out,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "output_capped": output_capped,
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return {**record, "stdout": stdout, "stderr": stderr}


def install_windows_wrappers(root: Path, directory: Path, log: Path) -> Path:
    """Install fixed-shape tc_* command wrappers for a worker directory."""
    root = root.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).resolve()
    real_python = Path(sys.executable).resolve()
    for kind, timeout in (("python", 30), ("sage", 30),
                          ("sat", 30), ("lean", 20)):
        wrapper = directory / f"tc_{kind}.cmd"
        wrapper.write_text(
            f'@echo off\n'
            f'if not "%~2"=="" exit /b 2\n'
            f'"{real_python}" "{runner}" {kind} "{root}" "%~1" {timeout} "{log.resolve()}"\n',
            encoding="utf-8", newline="\r\n")
    return directory


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit("usage: narrow_tools.py KIND ROOT TARGET TIMEOUT LOG")
    result = run_narrow(sys.argv[1], sys.argv[3], root=Path(sys.argv[2]),
                        timeout=float(sys.argv[4]), log=Path(sys.argv[5]))
    print(json.dumps({k: v for k, v in result.items()
                      if k not in {"stdout", "stderr"}}, sort_keys=True))
    print(result["stdout"], end="")
    print(result["stderr"], file=sys.stderr, end="")
    raise SystemExit(result["exit_code"] if result["exit_code"] is not None else 1)
