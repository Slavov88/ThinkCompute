from __future__ import annotations

import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def inside(root: Path, raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("target must be a relative path without traversal")
    resolved = (root / p).resolve(strict=True)
    if resolved == root or root not in resolved.parents:
        raise ValueError("target escaped worker root")
    if any(part.is_symlink() for part in (resolved, *resolved.parents)):
        raise ValueError("symlink target rejected")
    return resolved


def blocked(*_args, **_kwargs):
    raise OSError("network disabled for mathematical tool")


def run_script(executable: str, script: Path) -> list[str]:
    runner = ("import runpy,socket,sys; "
              "blocked=lambda *a,**k: (_ for _ in ()).throw(OSError('network disabled')); "
              "socket.socket=type('BlockedSocket',(),{'__init__':blocked}); "
              "socket.create_connection=blocked; socket.getaddrinfo=blocked; "
              "sys.argv=[sys.argv[1]]; runpy.run_path(sys.argv[1],run_name='__main__')")
    return [executable, "-c", runner, str(script)]


def main() -> int:
    if len(sys.argv) != 6:
        print("usage: tc_tool.py KIND ROOT TARGET TIMEOUT LOG", file=sys.stderr)
        return 2
    kind, root_text, target, timeout_text, log_text = sys.argv[1:]
    if kind not in {"python", "sage", "sat", "lean"}:
        return 2
    root = Path(root_text).resolve()
    try:
        script = inside(root, target)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if kind in {"python", "sat"}:
        command = run_script(sys.executable, script)
    elif kind == "sage":
        sage = shutil.which("sage")
        if not sage:
            print("sage executable unavailable", file=sys.stderr)
            return 127
        command = [sage, "-python", "-c", run_script("python", script)[2], str(script)]
    else:
        lean = shutil.which("lean")
        if not lean:
            print("lean executable unavailable", file=sys.stderr)
            return 127
        command = [lean, str(script)]
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    env["NO_PROXY"] = "*"
    started = time.time()
    timed_out = False
    out = err = ""
    code = None
    try:
        proc = subprocess.Popen(command, cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        try:
            out, err = proc.communicate(timeout=float(timeout_text))
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            out, err = exc.stdout or "", exc.stderr or ""
            proc.kill()
            extra_out, extra_err = proc.communicate()
            out += extra_out or ""
            err += extra_err or ""
            code = proc.returncode
    except FileNotFoundError as exc:
        err = str(exc)
        code = 127
    ended = time.time()
    stdout_raw = out.encode()
    stderr_raw = err.encode()
    output_capped = len(stdout_raw) > MAX_OUTPUT_BYTES or len(stderr_raw) > MAX_OUTPUT_BYTES
    if output_capped:
        out = stdout_raw[:MAX_OUTPUT_BYTES].decode(errors="replace")
        err = stderr_raw[:MAX_OUTPUT_BYTES].decode(errors="replace") + "\noutput cap exceeded\n"
        code = 125
    record = {"tool": kind, "target": target, "command": command,
              "start_timestamp": started, "end_timestamp": ended,
              "wall_seconds": ended-started, "exit_code": code,
              "timeout": timed_out,
              "stdout_bytes": len(out.encode()), "stderr_bytes": len(err.encode()),
              "output_capped": output_capped}
    Path(log_text).parent.mkdir(parents=True, exist_ok=True)
    with Path(log_text).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print(out, end="")
    print(err, file=sys.stderr, end="")
    return code if code is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
