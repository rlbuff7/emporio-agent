"""The tool-calling loop, written directly on the Anthropic SDK.

One ``Agent`` holds one conversation. ``send`` runs: model call -> execute requested
tools -> return results -> repeat, until the model answers in text or the round
limit is hit. The full assistant content (including thinking and tool_use blocks)
is appended to the history every time, as the API requires.
"""

import json
import os
from dataclasses import dataclass, field

import anthropic

from emporio.prompts import build_system_prompt
from emporio.tool_schemas import TOOL_SCHEMAS
from emporio.tools import StoreTools

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"  # multi-step tool use in a chat; "low" is cheaper and faster
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 5

FALLBACK_REPLY = (
    "Desculpa, não consegui concluir isso agora. Pode tentar de novo ou reformular? "
    "Se preferir, falo com a equipe pra te ajudar."
)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        tools: StoreTools | None = None,
        model: str | None = None,
        effort: str | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.tools = tools or StoreTools()
        self.model = model or os.environ.get("EMPORIO_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("EMPORIO_EFFORT", DEFAULT_EFFORT)
        self.system = build_system_prompt()
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    def send(self, text: str) -> Reply:
        """Answer one customer message. On an API error the history is rolled back, so the turn can be retried."""
        checkpoint = len(self.messages)
        try:
            return self._turn(text)
        except anthropic.APIError:
            del self.messages[checkpoint:]
            raise

    def _turn(self, text: str) -> Reply:
        self.messages.append({"role": "user", "content": text})
        calls: list[ToolCall] = []
        for _ in range(MAX_TOOL_ROUNDS + 1):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=TOOL_SCHEMAS,
                messages=self.messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # system prompt + tools are static
            )
            if response.stop_reason == "refusal":
                return self._give_up(calls)
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                return Reply(answer or FALLBACK_REPLY, calls)
            self.messages.append({"role": "user", "content": self._run_tools(response.content, calls)})
        return self._give_up(calls)

    def _run_tools(self, content: list, calls: list[ToolCall]) -> list[dict]:
        """Execute every tool_use block and return all results in one user message."""
        results = []
        for block in content:
            if block.type != "tool_use":
                continue
            arguments = dict(block.input)
            result = self.tools.call(block.name, arguments)
            calls.append(ToolCall(block.name, arguments, result))
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
                # Only malformed calls are errors; "not found" or "verification failed" are answers.
                "is_error": result.get("error") in ("unknown_tool", "invalid_arguments"),
            })
        return results

    def _give_up(self, calls: list[ToolCall]) -> Reply:
        # Close the turn with a plain assistant message so the history stays valid.
        self.messages.append({"role": "assistant", "content": FALLBACK_REPLY})
        return Reply(FALLBACK_REPLY, calls)
