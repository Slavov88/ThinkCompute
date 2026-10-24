"""Docker-backed broker worker command builder.

The host broker owns payload validation and the container owns execution. No
benchmark or host root is ever mounted.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_FORBIDDEN_MOUNT_PARTS = ("vault", "reference-proof", "source-repo", "private")


def forbidden_mount_parts() -> tuple[str, ...]:
    """Host path fragments never mounted into a worker; extend with THINKCOMPUTE_DENY_MARKERS (comma-separated)."""
    extra = tuple(m.strip().lower() for m in os.environ.get("THINKCOMPUTE_DENY_MARKERS", "").split(",") if m.strip())
    return DEFAULT_FORBIDDEN_MOUNT_PARTS + extra


def _safe_mount(path: Path, *, writable: bool) -> str:
    path = path.resolve()
    text = str(path).lower()
    if any(part in text for part in forbidden_mount_parts()):
        raise ValueError("forbidden host path in worker mount")
    if path == Path(path.anchor) or len(path.parts) < 3:
        raise ValueError("broad host mount rejected")
    mode = "rw" if writable else "ro"
    return f"type=bind,src={path},dst=/workspace/{'scratch' if writable else 'input'},{mode}"


class DockerToolWorker:
    def __init__(self, *, image: str, scratch_root: Path,
                 cpus: float = 2.0, memory: str = "512m", pids_limit: int = 128):
        self.image = image
        self.scratch_root = scratch_root.resolve()
        self.cpus = cpus
        self.memory = memory
        self.pids_limit = pids_limit

    def _run_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.scratch_root and self.scratch_root not in resolved.parents:
            raise ValueError("worker mount is outside the broker scratch root")
        return resolved

    def build_command(self, *, input_dir: Path, scratch_dir: Path, kind: str,
                      target: str, timeout: float) -> list[str]:
        if kind not in {"python", "smt", "lean"}:
            raise ValueError("unsupported broker tool")
        if Path(target).is_absolute() or ".." in Path(target).parts:
            raise ValueError("tool target traversal")
        input_dir = self._run_path(input_dir)
        scratch_dir = self._run_path(scratch_dir)
        suffix = ".py" if kind in {"python", "smt"} else ".lean"
        container_kind = "sat" if kind == "smt" else kind
        return [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cpus", str(self.cpus), "--memory", self.memory,
            "--pids-limit", str(self.pids_limit), "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", _safe_mount(input_dir, writable=False),
            "--mount", _safe_mount(scratch_dir, writable=True),
            self.image, "python3", "/opt/thinkcompute/tc_tool.py",
            container_kind, "/workspace/input", target, str(timeout),
            "/workspace/scratch/tool-events.jsonl",
        ]

    def run(self, kind: str, payload: str, *, timeout: float) -> dict[str, Any]:
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.scratch_root) as raw:
            call = Path(raw)
            input_dir = call / "input"
            scratch_dir = call / "scratch"
            input_dir.mkdir()
            scratch_dir.mkdir()
            suffix = ".py" if kind in {"python", "smt"} else ".lean"
            (input_dir / ("request" + suffix)).write_text(payload, encoding="utf-8")
            command = self.build_command(input_dir=input_dir, scratch_dir=scratch_dir,
                                         kind=kind, target="request" + suffix, timeout=timeout)
            start = time.perf_counter()
            try:
                cp = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout + 2)
                code, out, err = cp.returncode, cp.stdout, cp.stderr
            except subprocess.TimeoutExpired as exc:
                code, out, err = None, exc.stdout or "", exc.stderr or ""
            return {"status": "ok" if code == 0 else "failed", "exit_code": code,
                    "stdout": out, "stderr": err,
                    "wall_seconds": time.perf_counter() - start, "command": command}


class MockToolWorker:
    def __init__(self, durations: dict[str, float] | None = None):
        self.durations = durations or {}
        self.calls: list[dict[str, Any]] = []

    def run(self, kind: str, payload: str, *, timeout: float) -> dict[str, Any]:
        duration = min(float(self.durations.get(kind, 0.0)), timeout)
        result = {"status": "ok", "exit_code": 0, "stdout": "mock tool result",
                  "stderr": "", "wall_seconds": duration, "timeout": duration >= timeout}
        self.calls.append({"kind": kind, "payload": payload, "timeout": timeout, **result})
        return result
