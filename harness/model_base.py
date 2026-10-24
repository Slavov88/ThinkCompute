from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class ModelResult:
    text: str = ""; provider_usage: dict = field(default_factory=dict); model_calls: int|None = None; decision_cycles: int|None = None; termination_reason: str = ""

class ModelAdapter(ABC):
    @abstractmethod
    def run(self, problem_text: str, *, scratch, policy: str, timeout: float) -> ModelResult: ...

    def invoke_once(self, prompt: str, *, scratch, timeout: float):
        """Optional broker interface: exactly one fresh model invocation."""
        raise NotImplementedError("adapter does not implement brokered rounds")
