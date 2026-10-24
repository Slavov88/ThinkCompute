from dataclasses import dataclass

@dataclass
class Budget:
    exploration_limit: float = 60.0
    deterministic_process_limit: float = 30.0
    lean_call_limit: int = 20
    # Full Mathlib imports are intentionally allowed a bounded startup window.
    # This is an infrastructure timeout, separate from the V invocation count.
    lean_process_limit: float = 60.0
    session_limit: float = 600.0
    neural_cycle_limit: int | None = 4
    exploration_seconds: float = 0.0
    lean_calls: int = 0
    lean_seconds: float = 0.0

    def charge_exploration(self, seconds: float):
        self.exploration_seconds += seconds
        if self.exploration_seconds > self.exploration_limit: raise TimeoutError("exploration budget exceeded")

    def charge_lean(self, seconds: float):
        self.lean_calls += 1; self.lean_seconds += seconds
        if self.lean_calls > self.lean_call_limit: raise TimeoutError("Lean call budget exceeded")
