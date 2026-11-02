"""The tool-calling loop.

``BaseAgent`` holds one conversation and the provider-independent flow:
input check -> model call -> run requested tools -> repeat -> output check
(see emporio.guardrails). Subclasses only translate to a provider's API:
``Agent`` uses the Anthropic SDK directly; ``emporio.compat.CompatAgent`` speaks
the OpenAI-compatible format used by free providers (Gemini, Groq, Cerebras...).
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

DEFAULT_MODEL = "claude-haiku-5-5"  # cheap and fast; see docs/model_choice.md
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
    usage: dict[str, int] = field(default_factory=dict)  # tokens summed over the turn's API calls

    def add_usage(self, **tokens: int | None) -> None:
        for name, value in tokens.items():
            self.usage[name] = self.usage.get(name, 0) + (value or 0)


@dataclass
class Step:
    """One model response, in provider-neutral form."""
    text: str
    tool_requests: list[tuple[str, str, dict | None]]  # (call id, tool name, arguments or None if unparsable)
    refused: bool = False


class ProviderError(RuntimeError):
    """An API failure from a provider; the turn was rolled back and can be retried."""


class BaseAgent:
    provider_errors: tuple[type[Exception], ...] = ()

    def __init__(self, tools: StoreTools | None = None):
        self.tools = tools or StoreTools()
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
        except self.provider_errors:
            del self.messages[checkpoint:]
            self.known_amounts = known
            raise

    # Provider adapters implement these -------------------------------------------

    def _call(self, reply: Reply) -> Step:
        """Call the model with self.messages, append its response to self.messages, return it."""
        raise NotImplementedError

    def _add_tool_results(self, results: list[tuple[str, str, dict]]) -> None:
        raise NotImplementedError

    def _add_note(self, text: str) -> None:
        """Operator note for the model inside the conversation (not shown to the customer)."""
        self.messages.append({"role": "system", "content": text})

    # Provider-independent flow ------------------------------------------------------

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
            self._add_note(guardrails.INJECTION_REMINDER)

        tool_rounds = 0
        retried = False
        while True:
            step = self._call(reply)
            if step.refused:
                return self._give_up(reply)

            if step.tool_requests:
                # Layer 2 lives inside the tools themselves.
                self._add_tool_results(self._run_tools(step.tool_requests, reply))
                tool_rounds += 1
                if tool_rounds > MAX_TOOL_ROUNDS:
                    return self._give_up(reply)
                continue

            # Layer 3: output.
            problems = guardrails.check_output(step.text, self.known_amounts)
            if not problems:
                reply.text = step.text or FALLBACK_REPLY
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
                self._add_note(correction)

    def _run_tools(self, requests: list[tuple[str, str, dict | None]], reply: Reply) -> list[tuple[str, str, dict]]:
        results = []
        for call_id, name, arguments in requests:
            if arguments is None:
                result = {"error": "invalid_arguments", "message": "Arguments were not valid JSON."}
            else:
                result = self.tools.call(name, arguments)
            reply.tool_calls.append(ToolCall(name, arguments or {}, result))
            self.known_amounts |= guardrails.numbers_in_result(result)
            results.append((call_id, name, result))
        return results

    def _give_up(self, reply: Reply) -> Reply:
        # Close the turn with a plain assistant message so the history stays valid.
        self.messages.append({"role": "assistant", "content": FALLBACK_REPLY})
        reply.text = FALLBACK_REPLY
        return reply


class Agent(BaseAgent):
    """Anthropic Messages API, called directly through the official SDK."""

    provider_errors = (anthropic.APIError,)

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        tools: StoreTools | None = None,
        model: str | None = None,
        effort: str | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("EMPORIO_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("EMPORIO_EFFORT", DEFAULT_EFFORT)
        super().__init__(tools)

    def _call(self, reply: Reply) -> Step:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            tools=TOOL_SCHEMAS,
            messages=self.messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},  # system prompt + tools are static
        )
        usage = getattr(response, "usage", None)
        reply.add_usage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
        )
        if response.stop_reason == "refusal":
            return Step(text="", tool_requests=[], refused=True)
        # The full content (thinking, text, tool_use) goes back into the history, as the API requires.
        self.messages.append({"role": "assistant", "content": response.content})
        requests = []
        if response.stop_reason == "tool_use":
            requests = [(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return Step(text=text, tool_requests=requests)

    def _add_tool_results(self, results: list[tuple[str, str, dict]]) -> None:
        # All results in one user message, so the model keeps making parallel calls.
        self.messages.append({"role": "user", "content": [
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": json.dumps(result, ensure_ascii=False),
                # Only malformed calls are errors; "not found" or "verification failed" are answers.
                "is_error": result.get("error") in ("unknown_tool", "invalid_arguments"),
            }
            for call_id, _, result in results
        ]})


def _is_outage(exc: Exception) -> bool:
    """Errors worth retrying elsewhere: overload, rate limit, server error, network. Not bad keys or bad requests."""
    status = getattr(exc, "status_code", None)
    return status is None or status == 429 or status >= 500


class FallbackAgent:
    """Primary model first; if it is down or rate-limited, the same turn goes down a chain of
    fallback models (e.g. Gemini, then Groq) until one answers.

    Providers keep history in different formats, so a fallback is seeded with the conversation
    so far as plain text, and its answer is written back into the primary's history. All share
    one StoreTools, so tool-side limits (lockout, tickets) hold across providers.
    """

    def __init__(self, primary: BaseAgent, fallback_factories: list):
        self.primary = primary
        self.fallback_factories = fallback_factories  # built lazily: no key needed until an outage
        self.provider_errors = primary.provider_errors
        self.turns: list[tuple[str, str]] = []

    @property
    def messages(self) -> list[dict]:
        return self.primary.messages

    def reset(self) -> None:
        self.primary.reset()
        self.turns = []

    def send(self, text: str) -> Reply:
        try:
            reply = self.primary.send(text)
        except self.primary.provider_errors as exc:
            if not _is_outage(exc):
                raise
            reply = self._send_to_fallbacks(text, reason=type(exc).__name__)
            if reply is None:
                # Every fallback is down too (or not configured): surface the primary's error, which callers handle.
                raise
            # Keep the primary's history complete for the next turn.
            self.primary.messages += [{"role": "user", "content": text}, {"role": "assistant", "content": reply.text}]
        self.turns.append((text, reply.text))
        return reply

    def _send_to_fallbacks(self, text: str, reason: str) -> Reply | None:
        """Try each fallback in order; None if all of them fail."""
        failed = [f"primary failed ({reason})"]
        for factory in self.fallback_factories:
            try:
                fallback = factory(self.primary.tools)
                for user, answer in self.turns:
                    fallback.messages += [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]
                fallback.known_amounts |= self.primary.known_amounts
                reply = fallback.send(text)
            except Exception as exc:  # down, rate-limited, missing key or package: try the next one
                failed.append(f"{getattr(factory, 'model', 'fallback')} failed ({type(exc).__name__})")
                continue
            self.primary.known_amounts |= fallback.known_amounts
            reply.flags.insert(0, f"fallback: {', '.join(failed)}; answered by {fallback.model}")
            return reply
        return None


class _Factory:
    """Builds a fallback agent on demand, sharing the primary's tools."""

    def __init__(self, model: str):
        self.model = model

    def __call__(self, tools: StoreTools) -> BaseAgent:
        return _single_agent(self.model, tools)


def make_agent(model: str | None = None, tools: StoreTools | None = None) -> BaseAgent | FallbackAgent:
    """'claude-...' -> Anthropic; 'provider:model' (e.g. 'gemini:gemini-3.5-flash') -> free provider.

    With no explicit model, EMPORIO_MODEL is used, and EMPORIO_FALLBACK_MODEL adds a fallback chain:
    a comma-separated list tried in order, e.g. "gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b".
    """
    chain = [] if model else [m.strip() for m in os.environ.get("EMPORIO_FALLBACK_MODEL", "").split(",") if m.strip()]
    agent = _single_agent(model or os.environ.get("EMPORIO_MODEL", DEFAULT_MODEL), tools)
    if chain:
        return FallbackAgent(agent, [_Factory(m) for m in chain])
    return agent


def _single_agent(model: str, tools: StoreTools | None) -> BaseAgent:
    if ":" in model:
        from emporio.compat import CompatAgent  # optional dependency (openai package)
        provider, provider_model = model.split(":", 1)
        return CompatAgent(provider, provider_model, tools=tools)
    return Agent(model=model, tools=tools)
