"""Fallback from the primary model to a second provider, with fake clients."""

import anthropic
import httpx2
import pytest

from emporio import compat
from emporio.agent import FallbackAgent, make_agent
from emporio.compat import CompatAgent
from tests.test_agent import FakeClient, make_agent as make_primary, response, store_tools, text  # noqa: F401
from tests.test_compat import FakeOpenAI, completion, no_throttle  # noqa: F401

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def overloaded():
    return anthropic.APIConnectionError(request=REQUEST)


def bad_request():
    return anthropic.BadRequestError("bad", response=httpx2.Response(400, request=REQUEST), body=None)


def fallback_with(store_tools, primary_responses, fallback_responses):  # noqa: F811
    primary = make_primary(store_tools, *primary_responses)
    fake = FakeOpenAI(*fallback_responses)
    agent = FallbackAgent(primary, lambda tools: CompatAgent("gemini", "fake-gemini", tools=tools, client=fake))
    return agent, primary, fake


def test_outage_sends_the_turn_to_the_fallback(store_tools):  # noqa: F811
    agent, primary, fake = fallback_with(
        store_tools,
        [response("end_turn", text("Oi! Como posso ajudar?")), overloaded(), response("end_turn", text("De nada!"))],
        [completion("A loja abre às 9h.")],
    )
    agent.send("oi")
    reply = agent.send("que horas abre?")
    assert reply.text == "A loja abre às 9h."
    assert reply.flags[0].startswith("fallback:") and "fake-gemini" in reply.flags[0]

    # The fallback saw the earlier turn as plain text.
    seen = fake.requests[0]["messages"]
    assert [m["content"] for m in seen[1:]] == ["oi", "Oi! Como posso ajudar?", "que horas abre?"]

    # The next turn goes back to the primary, whose history now includes the fallback's answer.
    assert agent.send("obrigado").text == "De nada!"
    last_request = primary.client.requests[-1]["messages"]
    assert {"role": "assistant", "content": "A loja abre às 9h."} in last_request


def test_non_outage_errors_are_not_hidden(store_tools):  # noqa: F811
    agent, _, fake = fallback_with(store_tools, [bad_request()], [completion("nunca")])
    with pytest.raises(anthropic.BadRequestError):
        agent.send("oi")
    assert fake.requests == []


def test_fallback_down_too_raises_the_primary_error(store_tools):  # noqa: F811
    primary = make_primary(store_tools, overloaded())

    def broken_factory(tools):
        raise compat.ProviderError("Set GEMINI_API_KEY")

    with pytest.raises(anthropic.APIConnectionError):
        FallbackAgent(primary, broken_factory).send("oi")
    assert primary.messages == []


def test_fallback_is_configured_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("EMPORIO_FALLBACK_MODEL", "gemini:gemini-3.5-flash")
    assert isinstance(make_agent(), FallbackAgent)
    assert not isinstance(make_agent("claude-haiku-5-5"), FallbackAgent)  # explicit model (evals): no fallback
    monkeypatch.delenv("EMPORIO_FALLBACK_MODEL")
    assert not isinstance(make_agent(), FallbackAgent)
