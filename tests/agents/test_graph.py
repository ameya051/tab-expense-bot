"""Tests for the LangGraph parse graph with a structured-output fake model."""

from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.agents.prompts import format_intent_prompt
from app.schemas import (
    DeleteIntent,
    IntentEnvelope,
    LogExpenseIntent,
    QueryIntent,
    UnknownIntent,
)


class FakeStructuredModel:
    """Chat-model double whose structured output returns a canned envelope."""

    def __init__(self, envelope: IntentEnvelope | None = None, error: Exception | None = None):
        self.envelope = envelope
        self.error = error
        self.seen_messages: list | None = None

    def with_structured_output(self, schema):  # noqa: ARG002 - interface parity
        model = self

        class _StructuredRun:
            async def ainvoke(self, messages):
                model.seen_messages = messages
                if model.error is not None:
                    raise model.error
                assert model.envelope is not None
                return model.envelope

        return _StructuredRun()


def _make_graph(envelope=None, error=None):
    model = FakeStructuredModel(envelope=envelope, error=error)
    graph = build_graph(model=model)
    return graph, model


def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "user_id": 1,
        "preferred_currency": "INR",
        "parsed_intent": None,
    }


LOG_ENVELOPE = IntentEnvelope(
    intent_union=LogExpenseIntent(
        intent="log_expense",
        amount=250.0,
        currency="INR",
        category="food",
        date="2026-09-25",
        description="lunch",
    )
)


async def test_graph_returns_log_expense_intent():
    graph, _ = _make_graph(envelope=LOG_ENVELOPE)

    state = await graph.ainvoke(_state("spent 250 on lunch"))

    parsed = state["parsed_intent"]
    assert isinstance(parsed, LogExpenseIntent)
    assert parsed.amount == 250.0
    assert parsed.currency == "INR"
    assert parsed.category == "food"


async def test_graph_returns_query_intent():
    envelope = IntentEnvelope(
        intent_union=QueryIntent(intent="query", period="this_month", group_by="category")
    )
    graph, _ = _make_graph(envelope=envelope)

    state = await graph.ainvoke(_state("how much this month?"))

    parsed = state["parsed_intent"]
    assert isinstance(parsed, QueryIntent)
    assert parsed.period == "this_month"
    assert parsed.group_by == "category"


async def test_graph_returns_delete_intent():
    envelope = IntentEnvelope(intent_union=DeleteIntent(intent="delete", target="last"))
    graph, _ = _make_graph(envelope=envelope)

    state = await graph.ainvoke(_state("delete my last expense"))

    assert state["parsed_intent"] == DeleteIntent(intent="delete", target="last")


async def test_graph_model_error_falls_back_to_unknown():
    graph, _ = _make_graph(error=RuntimeError("provider exploded"))

    state = await graph.ainvoke(_state("anything"))

    assert state["parsed_intent"] == UnknownIntent(intent="unknown")


async def test_graph_malformed_output_falls_back_to_unknown():
    graph, _ = _make_graph(envelope="not an envelope")

    state = await graph.ainvoke(_state("anything"))

    assert state["parsed_intent"] == UnknownIntent(intent="unknown")


def test_format_intent_prompt_injects_date_and_currency():
    messages = format_intent_prompt("spent 250 on lunch", "USD")

    assert messages[0].type == "system"
    assert "Today's date is" in messages[0].content
    assert "USD" in messages[0].content
    assert "log_expense" in messages[0].content
    assert "query" in messages[0].content
    assert "delete" in messages[0].content
    assert "unknown" in messages[0].content
    assert messages[1].type == "human"
    assert messages[1].content == "spent 250 on lunch"


async def test_fake_model_sees_expected_conversation_shape():
    graph, model = _make_graph(envelope=LOG_ENVELOPE)

    await graph.ainvoke(_state("spent 250 on lunch"))

    assert model.seen_messages is not None
    roles = [m.type for m in model.seen_messages]
    assert roles == ["system", "human"]
    assert model.seen_messages[1].content == "spent 250 on lunch"
