"""Model adapter for any OpenAI-compatible chat completions endpoint (standard library only)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .broker import RoundResponse
from .errors import ModelInvocationError

SYSTEM_PROMPT = ("You are one round of a brokered solver. Reply with exactly one JSON object "
                 "that matches the action schema described in the user message, and nothing else.")


class OpenAICompatibleModel:
    """One fresh, stateless chat completion per broker round.

    The API key is read from the environment variable named by ``api_key_env`` and is never
    stored or logged. Failed calls raise ``ModelInvocationError`` and are not retried.
    """

    def __init__(self, model: str, *, base_url: str | None = None, api_key_env: str = "OPENAI_API_KEY",
                 temperature: float | None = None, extra_body: dict | None = None,
                 system_prompt: str = SYSTEM_PROMPT):
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.extra_body = dict(extra_body or {})
        self.system_prompt = system_prompt

    def invoke_once(self, prompt: str, *, scratch: Path, timeout: float,
                    schema_path: Path | None = None) -> RoundResponse:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ModelInvocationError(f"environment variable {self.api_key_env} is not set")
        body = {"model": self.model,
                "messages": [{"role": "system", "content": self.system_prompt},
                             {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.extra_body)
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                payload = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ModelInvocationError(f"HTTP {exc.code} from {self.base_url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelInvocationError(f"model request failed: {exc!r}") from exc
        elapsed = time.perf_counter() - started
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelInvocationError("response has no choices[0].message.content") from exc
        usage = dict(payload.get("usage") or {})
        usage["wall_seconds"] = elapsed
        return RoundResponse(raw_text=text, provider_usage=usage, model_turns=1,
                             session_id=payload.get("id"), exit_code=0, wall_seconds=elapsed)
