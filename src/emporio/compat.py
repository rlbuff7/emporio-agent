"""Adapter for free LLM providers that expose an OpenAI-compatible chat API.

Used to compare models from other providers with the same tools, prompt and
guardrails (see docs/model_choice.md). Select one with
``EMPORIO_MODEL=<provider>:<model>``, e.g. ``gemini:gemini-2.5-flash``.
Requires ``pip install -e ".[free]"`` and the provider's key in .env.
"""

import json
import os
import time
from dataclasses import dataclass

import openai

from emporio.agent import BaseAgent, ProviderError, Reply, Step
from emporio.tool_schemas import TOOL_SCHEMAS
from emporio.tools import StoreTools

MAX_TOKENS = 4096


@dataclass(frozen=True)
class Provider:
    base_url: str
    key_env: str
    min_seconds_between_calls: float  # stay under the free tier's requests-per-minute limit


PROVIDERS = {
    # Gemini free tier, per model (AI Studio, 2026-11-02): Flash 5 req/min and 20 req/day; Flash Lite 15 req/min.
    "gemini": Provider("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", 13.0),
    "groq": Provider("https://api.groq.com/openai/v1", "GROQ_API_KEY", 2.5),
    "cerebras": Provider("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", 2.5),
    "mistral": Provider("https://api.mistral.ai/v1", "MISTRAL_API_KEY", 1.5),
    "openrouter": Provider("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", 4.0),
}

# Free-tier limits are per model; these models allow more requests per minute than their provider's default.
MODEL_MIN_SECONDS = {"gemini-3.1-flash-lite": 4.5}

# Last request time per provider and model, shared by every agent in the process (evals create many).
_last_call: dict[str, float] = {}

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }
    for tool in TOOL_SCHEMAS
]


class CompatAgent(BaseAgent):
    provider_errors = (openai.APIError,)

    def __init__(self, provider: str, model: str, tools: StoreTools | None = None, client=None):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Options: {', '.join(PROVIDERS)}")
        self.provider_name = provider
        self.provider = PROVIDERS[provider]
        self.model = model
        api_key = os.environ.get(self.provider.key_env)
        if client is None and not api_key:
            raise ProviderError(f"Set {self.provider.key_env} in .env to use {provider}.")
        # Few retries and a firm timeout: free tiers return 503/429 often, and a stuck call should fail fast.
        self.client = client or openai.OpenAI(
            api_key=api_key, base_url=self.provider.base_url, max_retries=2, timeout=90
        )
        super().__init__(tools)

    def _add_note(self, text: str) -> None:
        # Not every provider accepts system messages mid-conversation; a tagged user note works everywhere.
        self.messages.append({"role": "user", "content": f"[Store system note, not from the customer] {text}"})

    def _call(self, reply: Reply) -> Step:
        key = f"{self.provider_name}:{self.model}"
        spacing = MODEL_MIN_SECONDS.get(self.model, self.provider.min_seconds_between_calls)
        wait = spacing - (time.monotonic() - _last_call.get(key, float("-inf")))
        if wait > 0:
            time.sleep(wait)
        _last_call[key] = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system}, *self.messages],
            tools=OPENAI_TOOLS,
            max_tokens=MAX_TOKENS,
        )
        if response.usage:
            reply.add_usage(input_tokens=response.usage.prompt_tokens,
                            output_tokens=response.usage.completion_tokens)
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            return Step(text="", tool_requests=[], refused=True)
        message = choice.message
        calls = message.tool_calls or []
        assistant = {"role": "assistant", "content": message.content or ""}
        if calls:
            assistant["tool_calls"] = [_dump_call(c) for c in calls]
        self.messages.append(assistant)
        return Step(
            text=(message.content or "").strip(),
            tool_requests=[(c.id, c.function.name, _parse_arguments(c.function.arguments)) for c in calls],
        )

    def _add_tool_results(self, results: list[tuple[str, str, dict]]) -> None:
        for call_id, _, result in results:
            self.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False),
            })


def _dump_call(call) -> dict:
    """The tool call exactly as the provider sent it. Gemini 3 adds a thought_signature in
    extra_content and rejects the next request (HTTP 400) if it is not sent back."""
    if hasattr(call, "model_dump"):
        return call.model_dump(exclude_none=True)
    return {"id": call.id, "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"}}


def _parse_arguments(raw: str | None) -> dict | None:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
