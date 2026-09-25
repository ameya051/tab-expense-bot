"""Tests for the run_agent facade with a stubbed compiled graph."""

import pytest

import app.agents.runner as runner_module
from app.agents.runner import run_agent
from app.schemas import DeleteIntent, LogExpenseIntent, QueryIntent, UnknownIntent

LOG_INTENT = LogExpenseIntent(
    intent="log_expense",
    amount=120.0,
    currency="INR",
    category="transport",
    date="2026-09-25",
)


class StubGraph:
    """Compiled-graph double recording ainvoke calls."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[tuple[dict, dict]] = []

    async def ainvoke(self, state: dict, config: dict | None = None):
        self.calls.append((state, config))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def _reset_singleton():
    runner_module._compiled_graph = None
    yield
    runner_module._compiled_graph = None


def _install(stub: StubGraph, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "_compiled_graph", stub)


async def test_run_agent_returns_parsed_log_intent(monkeypatch: pytest.MonkeyPatch):
    stub = StubGraph(result={"parsed_intent": LOG_INTENT})
    _install(stub, monkeypatch)

    intent = await run_agent(user_id=42, text="took an auto for 120", default_currency="INR")

    assert intent == LOG_INTENT
    state, config = stub.calls[0]
    assert state["user_id"] == 42
    assert state["preferred_currency"] == "INR"
    assert state["messages"][0].content == "took an auto for 120"
    assert config == {"configurable": {"thread_id": "user-42"}}


async def test_run_agent_returns_query_intent(monkeypatch: pytest.MonkeyPatch):
    query = QueryIntent(intent="query", period="this_week")
    _install(StubGraph(result={"parsed_intent": query}), monkeypatch)

    intent = await run_agent(42, "weekly spend", "INR")

    assert intent == query


async def test_run_agent_returns_delete_intent(monkeypatch: pytest.MonkeyPatch):
    _install(StubGraph(result={"parsed_intent": DeleteIntent(intent="delete")}), monkeypatch)

    intent = await run_agent(42, "delete last")

    assert intent == DeleteIntent(intent="delete")


async def test_run_agent_graph_exception_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch):
    _install(StubGraph(error=RuntimeError("boom")), monkeypatch)

    intent = await run_agent(42, "anything")

    assert intent == UnknownIntent(intent="unknown")


async def test_run_agent_missing_intent_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch):
    _install(StubGraph(result={}), monkeypatch)

    intent = await run_agent(42, "anything")

    assert intent == UnknownIntent(intent="unknown")


async def test_run_agent_builds_graph_once(monkeypatch: pytest.MonkeyPatch):
    stub = StubGraph(result={"parsed_intent": UnknownIntent(intent="unknown")})

    def fake_build_graph(**kwargs):
        assert kwargs["use_postgres_checkpointer"] is True
        return stub

    monkeypatch.setattr(runner_module, "_compiled_graph", None)
    monkeypatch.setattr(runner_module, "build_graph", fake_build_graph)

    first = await run_agent(1, "one")
    second = await run_agent(2, "two")

    assert first == second == UnknownIntent(intent="unknown")
    assert len(stub.calls) == 2
