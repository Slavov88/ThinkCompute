"""Model adapter for the Codex CLI: one fresh, non-resumable process per broker round."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .broker import RoundResponse
from .codex_events import parse_jsonl
from .errors import ModelInvocationError

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "config" / "action_schema.json"


class CodexModelAdapter:
    def __init__(self, model_id: str, *, reasoning_effort: str = "low", command: str = "codex",
                 approval: str = "never"):
        self.model_id = model_id
        self.reasoning_effort = reasoning_effort
        self.command = command
        self.approval = approval

    def build_command(self, scratch: Path, final_path: Path, schema_path: Path) -> list[str]:
        """One read-only, ephemeral, schema-bound invocation; no resume or persistent session."""
        return [
            self.command, "exec", "--json", "--ephemeral", "--ignore-user-config",
            "--skip-git-repo-check", "--model", self.model_id,
            "-c", f'model_reasoning_effort="{self.reasoning_effort}"',
            "-c", f'approval_policy="{self.approval}"',
            "--sandbox", "read-only", "--cd", str(scratch),
            "--output-last-message", str(final_path),
            "--ignore-rules", "--disable", "memories", "--disable", "apps", "--disable", "plugins",
            "--output-schema", str(schema_path), "-",
        ]

    def invoke_once(self, prompt: str, *, scratch: Path, timeout: float,
                    schema_path: Path | None = None) -> RoundResponse:
        scratch = Path(scratch).resolve()
        final_path = scratch / "broker-final.json"
        command = self.build_command(scratch, final_path, Path(schema_path or DEFAULT_SCHEMA))
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
        started = time.perf_counter()
        try:
            process = subprocess.Popen(command, cwd=scratch, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise ModelInvocationError(f"Codex CLI not found: {self.command}") from exc
        try:
            out, err = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise ModelInvocationError(f"Codex round exceeded {timeout} seconds") from exc
        if process.returncode != 0:
            raise ModelInvocationError(f"Codex exited {process.returncode}: {err.strip()[:300]}")
        elapsed = time.perf_counter() - started
        events = parse_jsonl(out)
        raw = (final_path.read_text(encoding="utf-8", errors="replace")
               if final_path.exists() else (events.final_text or out))
        usage = dict(events.usage)
        usage.update({"process_exit_code": process.returncode, "stderr_bytes": len(err.encode()),
                      "wall_seconds": elapsed})
        return RoundResponse(raw_text=raw, provider_usage=usage, model_turns=events.codex_turns,
                             session_id=events.session_id, raw_stdout=out, raw_stderr=err,
                             exit_code=process.returncode, wall_seconds=elapsed)
