"""Restricted native worker for Python/SymPy, Z3 and Lean actions.

This is a capability filter and process wrapper, not an OS security boundary.
Run the whole inference process inside a restricted account, VM, or container
when isolation from the host is required.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .tools import default_lean, _lake_derived_lean_path
from .logging import EventLogger


MAX_OUTPUT_BYTES = 4 * 1024 * 1024
ALLOWED_IMPORTS = {"math", "itertools", "functools", "collections", "fractions", "decimal", "statistics", "sympy", "z3"}
SMT_IMPORTS = {"math", "itertools", "functools", "collections", "fractions", "decimal", "statistics", "z3"}
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
    "getattr", "setattr", "delattr", "hasattr", "vars", "dir", "globals",
    "locals", "help", "memoryview", "importlib", "system", "popen", "run",
    "check_output", "check_call", "getenv", "environ", "loads", "dumps",
}
FORBIDDEN_MODULES = {
    "os", "pathlib", "subprocess", "socket", "requests", "urllib", "http",
    "shutil", "glob", "tempfile", "ctypes", "winreg", "multiprocessing",
    "threading", "inspect", "importlib", "pickle", "sys",
}
FORBIDDEN_LEAN = ("unsafe", "axiom", "run_tac", "by run_tac", "IO.", "System.", "process", "filesystem",
                  "#eval", "elab", "macro", "implemented_by", "extern", "dynamic")
DEFAULT_SENSITIVE_MARKERS = ("/mnt/c", "/mnt/d")


def sensitive_markers() -> tuple[str, ...]:
    """Path fragments rejected in worker input; extend with THINKCOMPUTE_DENY_MARKERS (comma-separated)."""
    extra = tuple(m.strip().lower() for m in os.environ.get("THINKCOMPUTE_DENY_MARKERS", "").split(",") if m.strip())
    return DEFAULT_SENSITIVE_MARKERS + extra


class NativeValidationError(ValueError):
    pass


def _check_sensitive_text(text: str) -> None:
    lowered = text.lower().replace("\\", "/")
    if any(marker in lowered for marker in sensitive_markers()):
        raise NativeValidationError("sensitive path marker rejected")
    if "c:/users/" in lowered:
        raise NativeValidationError("host user path rejected")


class _AstGuard(ast.NodeVisitor):
    def __init__(self, allowed_imports: set[str]):
        self.allowed_imports = allowed_imports

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in self.allowed_imports or root in FORBIDDEN_MODULES:
                raise NativeValidationError(f"import not allowlisted: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if node.level or root not in self.allowed_imports or root in FORBIDDEN_MODULES:
            raise NativeValidationError(f"from-import not allowlisted: {node.module}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
            raise NativeValidationError(f"name rejected: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") or node.attr in FORBIDDEN_NAMES:
            raise NativeValidationError(f"attribute rejected: {node.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            _check_sensitive_text(node.value)
        self.generic_visit(node)


def validate_python(code: str, *, for_smt: bool = False) -> ast.Module:
    if not isinstance(code, str) or not code.strip():
        raise NativeValidationError("empty code")
    if len(code.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise NativeValidationError("code size limit exceeded")
    _check_sensitive_text(code)
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise NativeValidationError(f"syntax error: {exc.msg}") from exc
    _AstGuard(SMT_IMPORTS if for_smt else ALLOWED_IMPORTS).visit(tree)
    return tree


def validate_lean_candidate(candidate: str) -> None:
    if not isinstance(candidate, str) or not candidate.strip():
        raise NativeValidationError("empty Lean candidate")
    _check_sensitive_text(candidate)
    lowered = candidate.lower()
    if any(fragment.lower() in lowered for fragment in FORBIDDEN_LEAN):
        raise NativeValidationError("unsafe or execution-oriented Lean construct rejected")
    if "sorry" in lowered or "admit" in lowered:
        raise NativeValidationError("placeholder rejected")


def _minimal_env(call_dir: Path) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    safe_path = [str(Path(sys.executable).resolve().parent),
                 str(Path(system_root) / "System32"), system_root]
    env = {
        "PATH": os.pathsep.join(safe_path),
        "SystemRoot": system_root,
        "WINDIR": os.environ.get("WINDIR", system_root),
        "TEMP": str(call_dir),
        "TMP": str(call_dir),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    return env


def _explicit_math_paths() -> list[str]:
    """Return only package roots for the explicitly approved math libraries."""
    paths: list[str] = []
    for package in ("sympy", "z3"):
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec and spec.submodule_search_locations:
            package_dir = Path(next(iter(spec.submodule_search_locations))).resolve()
            root = str(package_dir.parent)
            if root not in paths:
                paths.append(root)
    return paths


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class NativeRestrictedToolWorker:
    """Broker-compatible native worker."""

    def __init__(self, *, scratch_root: Path, target_text: str | None = None,
                 lean_executable: str | None = None, logger: EventLogger | None = None):
        self.scratch_root = Path(scratch_root).resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.target_text = target_text
        self.lean_executable = lean_executable or default_lean()
        self.logger = logger

    def run(self, kind: str, payload: str, *, timeout: float) -> dict[str, Any]:
        if kind == "python":
            return self.run_python(payload, timeout=timeout)
        if kind in {"smt", "sat"}:
            return self.run_smt(payload, timeout=timeout)
        if kind == "lean":
            return self.run_lean(payload, timeout=timeout)
        raise ValueError(f"unsupported native tool: {kind}")

    def _run_process(self, tool_type: str, command: list[str], *, call_dir: Path,
                     timeout: float) -> dict[str, Any]:
        started_wall = time.time()
        start = time.perf_counter()
        out = err = ""
        code: int | None = None
        timed_out = False
        creationflags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process_env = _minimal_env(call_dir)
        if tool_type == "lean":
            lean_path = os.environ.get("THINKCOMPUTE_LEAN_PATH") or _lake_derived_lean_path()
            if lean_path:
                process_env["LEAN_PATH"] = lean_path
        try:
            process = subprocess.Popen(command, cwd=call_dir, env=process_env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace", creationflags=creationflags)
            try:
                out, err = process.communicate(timeout=max(0.01, timeout))
                code = process.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                out, err = exc.stdout or "", exc.stderr or ""
                _kill_tree(process.pid)
                extra_out, extra_err = process.communicate()
                out += extra_out or ""
                err += extra_err or ""
                code = process.returncode
        except FileNotFoundError as exc:
            err = str(exc)
            code = 127
        elapsed = time.perf_counter() - start
        ended_wall = time.time()
        out_raw, err_raw = out.encode("utf-8"), err.encode("utf-8")
        capped = len(out_raw) > MAX_OUTPUT_BYTES or len(err_raw) > MAX_OUTPUT_BYTES
        if capped:
            out = out_raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            err = err_raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\noutput cap exceeded\n"
            code = 125
        result = {"status": "ok" if code == 0 and not timed_out and not capped else "failed",
                "command": command, "cwd": str(call_dir), "stdout": out, "stderr": err,
                "exit_code": code, "timeout": timed_out, "output_capped": capped,
                "wall_seconds": elapsed, "stdout_bytes": len(out.encode()),
                "stderr_bytes": len(err.encode())}
        if self.logger:
            self.logger.tool(tool_type, command, started_wall, ended_wall, code,
                             timed_out, out, err)
        return result

    def _python_call(self, code: str, *, timeout: float, for_smt: bool) -> dict[str, Any]:
        validate_python(code, for_smt=for_smt)
        with tempfile.TemporaryDirectory(prefix="tc-native-", dir=self.scratch_root) as raw:
            call_dir = Path(raw)
            script = call_dir / "request.py"
            script.write_text(code, encoding="utf-8", newline="\n")
            bootstrap = ("import runpy,sys; sys.path[:0] = "
                         + repr(_explicit_math_paths())
                         + "; runpy.run_path(sys.argv[1], run_name='__main__')")
            return self._run_process("smt" if for_smt else "python",
                                     [sys.executable, "-I", "-X", "utf8", "-B", "-c", bootstrap, str(script)],
                                     call_dir=call_dir, timeout=timeout)

    def run_python(self, code: str, *, timeout: float) -> dict[str, Any]:
        return self._python_call(code, timeout=timeout, for_smt=False)

    def run_smt(self, code: str, *, timeout: float) -> dict[str, Any]:
        return self._python_call(code, timeout=timeout, for_smt=True)

    def run_lean(self, candidate: str, *, timeout: float) -> dict[str, Any]:
        validate_lean_candidate(candidate)
        if not self.target_text:
            return {"status": "failed", "reason": "Lean target not configured", "wall_seconds": 0.0}
        marker = "by\n  sorry"
        if self.target_text.count(marker) != 1:
            return {"status": "failed", "reason": "target placeholder not unique", "wall_seconds": 0.0}
        with tempfile.TemporaryDirectory(prefix="tc-native-lean-", dir=self.scratch_root) as raw:
            call_dir = Path(raw)
            candidate_file = call_dir / "Candidate.lean"
            candidate_file.write_text(self.target_text.replace(marker, candidate, 1),
                                      encoding="utf-8", newline="\n")
            result = self._run_process("lean", [self.lean_executable, str(candidate_file)],
                                       call_dir=call_dir, timeout=timeout)
            result["verified"] = result["status"] == "ok" and result["exit_code"] == 0
            return result

    def as_lean_worker(self) -> "NativeLeanWorker":
        """Expose the broker's LeanWorker interface without ambiguity."""
        return NativeLeanWorker(self)


class NativeLeanWorker:
    """Adapter from ``NativeRestrictedToolWorker.run_lean`` to broker protocol."""

    def __init__(self, worker: NativeRestrictedToolWorker):
        self.worker = worker

    def run(self, payload: str, *, timeout: float) -> dict[str, Any]:
        return self.worker.run_lean(payload, timeout=timeout)
