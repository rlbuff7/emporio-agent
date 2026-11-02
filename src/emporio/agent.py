"""The tool-calling loop, written directly on the Anthropic SDK.

One ``Agent`` holds one conversation. ``send`` runs: input check -> model call ->
execute requested tools -> return results -> repeat, until the model answers in text
-> output check. The full assistant content (including thinking and tool_use blocks)
is appended to the history every time, as the API requires. See emporio.guardrails
for the input and output layers.
"""

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal

import anthropic

from emporio import guardrails
from emporio.prompts import build_system_prompt
from emporio.tool_schemas import TOOL_SCHEMAS
from emporio.tools import StoreTools

DEFAULT_MODEL = "claude-haiku-5-5"  # cheap and fast; set EMPORIO_MODEL=claude-sonnet-5-5 for stronger reasoning
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
    flags: list[str] = field(default_factory=list)  # guardrail events, shown in --debug


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
        self.reset()

    def reset(self) -> None:
        """Start a new conversation. Tool-side limits (e.g. verification lockout) are kept."""
        self.messages: list[dict] = []
        self.known_amounts: set[Decimal] = set()  # amounts the reply may quote

    def send(self, text: str) -> Reply:
        """Answer one customer message. On an API error the history is rolled back, so the turn can be retried."""
        checkpoint = len(self.messages)
        known = set(self.known_amounts)
        try:
            return self._turn(text)
        except anthropic.APIError:
            del self.messages[checkpoint:]
            self.known_amounts = known
            raise

    def _turn(self, text: str) -> Reply:
        reply = Reply(text="")

        # Layer 1: input.
        check = guardrails.check_input(text)
        if check.blocked_reply:
            reply.flags.append("input_blocked: too long")
            reply.text = check.blocked_reply
            return reply
        self.messages.append({"role": "user", "content": text})
        self.known_amounts |= guardrails.numbers_in_text(text)
        if check.suspicious:
            reply.flags.append("input_suspicious: injection reminder added")
            self.messages.append({"role": "system", "content": guardrails.INJECTION_REMINDER})

        tool_rounds = 0
        retried = False
        while True:
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
                return self._give_up(reply)
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                # Layer 2 lives inside the tools themselves.
                self.messages.append({"role": "user", "content": self._run_tools(response.content, reply)})
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    return self._give_up(reply)
                continue

            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            # Layer 3: output.
            problems = guardrails.check_output(answer, self.known_amounts)
            if not problems:
                reply.text = answer or FALLBACK_REPLY
                return reply
            reply.flags.extend(f"output_rejected: {p}" for p in problems)
            if retried:
                return self._give_up(reply)
            retried = True
            self.messages.pop()  # the rejected reply never reaches the customer
            correction = guardrails.correction_message(problems)
            if self.messages[-1]["role"] == "system":  # injection reminder from this turn
                self.messages[-1]["content"] += "\n\n" + correction
            else:
                self.messages.append({"role": "system", "content": correction})

    def _run_tools(self, content: list, reply: Reply) -> list[dict]:
        """Execute every tool_use block and return all results in one user message."""
        results = []
        for block in content:
            if block.type != "tool_use":
                continue
            arguments = dict(block.input)
            result = self.tools.call(block.name, arguments)
            reply.tool_calls.append(ToolCall(block.name, arguments, result))
            self.known_amounts |= guardrails.numbers_in_result(result)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
                # Only malformed calls are errors; "not found" or "verification failed" are answers.
                "is_error": result.get("error") in ("unknown_tool", "invalid_arguments"),
            })
        return results

    def _give_up(self, reply: Reply) -> Reply:
        # Close the turn with a plain assistant message so the history stays valid.
        self.messages.append({"role": "assistant", "content": FALLBACK_REPLY})
        reply.text = FALLBACK_REPLY
        return reply
