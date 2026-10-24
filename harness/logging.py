from __future__ import annotations
import json, time
from pathlib import Path

class EventLogger:
    def __init__(self, path: Path): self.path = path; self.path.parent.mkdir(parents=True, exist_ok=True)
    def event(self, event: dict):
        event = {"timestamp": time.time(), **event}
        with self.path.open("a", encoding="utf-8", newline="\n") as f: f.write(json.dumps(event, sort_keys=True) + "\n")
    def tool(self, tool_type, command, started, ended, returncode, timed_out, stdout, stderr):
        self.event({"event":"tool", "tool_type":tool_type, "command":command, "start_timestamp":started, "end_timestamp":ended, "wall_seconds":ended-started, "exit_code":returncode, "timeout":timed_out, "stdout_bytes":len(stdout.encode()), "stderr_bytes":len(stderr.encode())})
