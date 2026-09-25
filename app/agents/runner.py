"""Async facade for running the LangGraph expense agent."""

import logging
import threading

from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.schemas import ParsedIntent, UnknownIntent

logger = logging.getLogger(__name__)

_compiled_graph = None
_graph_lock = threading.Lock()


def _get_compiled_graph():
    """Build the compiled graph once, lazily, under a lock."""
    global _compiled_graph

    if _compiled_graph is None:
        with _graph_lock:
            if _compiled_graph is None:
                _compiled_graph = build_graph(use_postgres_checkpointer=True)
    return _compiled_graph


async def run_agent(user_id: int, text: str, default_currency: str = "INR") -> ParsedIntent:
    """Run user text through the LangGraph parse node.

    Builds the agent state, invokes the compiled graph on a per-user thread,
    and returns ``parsed_intent``. Any failure — invalid model output,
    provider errors, missing configuration — falls back to ``UnknownIntent``
    rather than raising, so bot handlers can never crash on a bad parse.
    """
    state = {
        "messages": [HumanMessage(content=text)],
        "user_id": user_id,
        "preferred_currency": default_currency,
        "parsed_intent": None,
    }
    config = {"configurable": {"thread_id": f"user-{user_id}"}}
    try:
        graph = _get_compiled_graph()
        result = await graph.ainvoke(state, config=config)
        intent = result.get("parsed_intent")
        if intent is None:
            logger.warning("Graph returned no parsed_intent for user %s", user_id)
            return UnknownIntent(intent="unknown")
        return intent
    except Exception as exc:  # noqa: BLE001 - fallback is the whole point of the facade
        logger.exception("run_agent failed for user %s: %s", user_id, exc)
        return UnknownIntent(intent="unknown")
