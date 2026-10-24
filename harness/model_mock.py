from .model_base import ModelAdapter, ModelResult
from .broker import RoundResponse

class MockModelAdapter(ModelAdapter):
    def __init__(self, response="by\n  exact True.intro"): self.response=response
    def run(self, problem_text, *, scratch, policy, timeout):
        return ModelResult(text=self.response, model_calls=0, decision_cycles=0, termination_reason="mock_complete")

    def invoke_once(self, prompt, *, scratch, timeout):
        return RoundResponse(raw_text=self.response, provider_usage={}, model_turns=0, session_id="mock")


class ScriptedMockModel:
    """Deterministic broker-only mock; it never starts a model process."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke_once(self, prompt, *, scratch, timeout):
        self.calls.append({"prompt": prompt, "timeout": timeout})
        if not self.responses:
            raise AssertionError("mock response script exhausted")
        return RoundResponse(raw_text=self.responses.pop(0), provider_usage={}, model_turns=0, session_id="mock")
