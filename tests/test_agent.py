"""Agent loop tests with a scripted fake client: no network, no API key."""

import json
from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from emporio.agent import FALLBACK_REPLY, MAX_TOOL_ROUNDS, Agent
from emporio.tools import STORE_TZ, TOOL_NAMES, StoreTools


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(name, arguments, id_="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=arguments, id=id_)


def response(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeClient:
    """Returns scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def store_tools(db_path, tmp_path):
    return StoreTools(db_path=db_path, handoffs_path=tmp_path / "handoffs.jsonl",
                      clock=lambda: datetime(2026, 11, 9, 14, 30, tzinfo=STORE_TZ))


def make_agent(store_tools, *responses):
    return Agent(client=FakeClient(*responses), tools=store_tools, model="test-model", effort="low")


def test_plain_answer(store_tools):
    agent = make_agent(store_tools, response("end_turn", text("Oi! Como posso ajudar?")))
    reply = agent.send("oi")
    assert reply.text == "Oi! Como posso ajudar?"
    assert reply.tool_calls == []


def test_tool_result_comes_from_database_and_is_sent_back(store_tools):
    agent = make_agent(
        store_tools,
        response("tool_use", text("Vou ver."), tool_use("get_product", {"name_or_id": "Takamine GD20"})),
        response("end_turn", text("O Takamine GD20 sai por R$ 2.199,00.")),
    )
    reply = agent.send("Quanto custa o Takamine GD20?")

    assert reply.tool_calls[0].result["final_price_brl"] == 2199.0
    second_request = agent.client.requests[1]["messages"]
    tool_result = second_request[-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"])["product_id"] == 95
    assert tool_result["is_error"] is False


def test_parallel_tool_calls_return_in_one_message(store_tools):
    agent = make_agent(
        store_tools,
        response("tool_use",
                 tool_use("get_current_datetime", {}, "a"),
                 tool_use("get_active_promotions", {}, "b")),
        response("end_turn", text("ok")),
    )
    agent.send("Estão abertos? Tem promoção?")
    results = agent.client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_request_carries_system_prompt_tools_and_settings(store_tools):
    agent = make_agent(store_tools, response("end_turn", text("ok")))
    agent.send("oi")
    request = agent.client.requests[0]
    assert "<manual>" in request["system"] and "Brazilian Portuguese" in request["system"]
    # Every schema sent to the model has a whitelisted implementation, and vice versa.
    assert {t["name"] for t in request["tools"]} == TOOL_NAMES
    assert request["model"] == "test-model"
    assert request["output_config"] == {"effort": "low"}


def test_bad_tool_call_is_reported_as_error_not_crash(store_tools):
    agent = make_agent(
        store_tools,
        response("tool_use", tool_use("delete_everything", {})),
        response("end_turn", text("Desculpa!")),
    )
    assert agent.send("x").text == "Desculpa!"
    assert agent.client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_round_limit_returns_fallback_and_keeps_history_valid(store_tools):
    loops = [response("tool_use", tool_use("get_current_datetime", {}, f"t{i}"))
             for i in range(MAX_TOOL_ROUNDS + 1)]
    agent = make_agent(store_tools, *loops)
    assert agent.send("?").text == FALLBACK_REPLY
    assert agent.messages[-1] == {"role": "assistant", "content": FALLBACK_REPLY}


def test_refusal_returns_fallback(store_tools):
    agent = make_agent(store_tools, response("refusal"))
    assert agent.send("?").text == FALLBACK_REPLY
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_api_error_rolls_back_the_turn(store_tools):
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    agent = make_agent(
        store_tools,
        response("end_turn", text("primeira")),
        response("tool_use", tool_use("get_current_datetime", {})),
        error,
    )
    agent.send("um")
    with pytest.raises(anthropic.APIConnectionError):
        agent.send("dois")
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_history_persists_across_turns_and_reset_clears_it(store_tools):
    agent = make_agent(store_tools, response("end_turn", text("a")), response("end_turn", text("b")))
    agent.send("um")
    agent.send("dois")
    assert len(agent.client.requests[1]["messages"]) == 3
    agent.reset()
    assert agent.messages == []
