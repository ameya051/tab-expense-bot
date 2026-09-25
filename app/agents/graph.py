"""LangGraph expense-parsing graph with a Postgres checkpointer."""

import logging

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg import Connection
from psycopg.rows import dict_row

from app.agents.prompts import format_intent_prompt
from app.agents.providers import build_chat_model
from app.agents.state import AgentState
from app.config import settings
from app.schemas import IntentEnvelope, UnknownIntent

logger = logging.getLogger(__name__)

CHECKPOINT_SCHEMA = "expense_bot_checkpoints"


def _make_parse_node(model: BaseChatModel):
    """Create the parse node bound to an LLM with structured output."""
    structured = model.with_structured_output(IntentEnvelope)

    async def parse_intent(state: AgentState) -> dict:
        """Classify the latest user message into a ParsedIntent."""
        user_text = str(state["messages"][-1].content)
        messages = format_intent_prompt(user_text, state["preferred_currency"])
        try:
            envelope = await structured.ainvoke(messages)
            intent = envelope.intent_union
        except Exception as exc:  # noqa: BLE001 - graceful fallback is intentional
            logger.warning("Intent parsing failed, falling back to unknown: %s", exc)
            intent = UnknownIntent(intent="unknown")
        return {"parsed_intent": intent}

    return parse_intent


def _postgres_dsn() -> str:
    """Convert the SQLAlchemy async DSN into a plain psycopg DSN.

    Pins the connection's search_path to a dedicated schema so LangGraph's
    checkpoint tables (checkpoints / checkpoint_blobs / checkpoint_writes)
    live in their own namespace, isolated from the bot's own tables.
    """
    dsn = settings.database_url.replace("+asyncpg", "", 1)
    return f"{dsn}?options=-csearch_path%3D{CHECKPOINT_SCHEMA}"


def _create_postgres_checkpointer() -> PostgresSaver:
    """Create and set up a PostgresSaver from a sync call site.

    Uses a synchronous psycopg connection so the checkpointer can be built
    lazily inside the async runner without blocking an event loop or calling
    ``asyncio.run()``. Checkpoint tables live in the dedicated
    ``expense_bot_checkpoints`` schema via the pinned search_path.
    """
    conn = Connection.connect(
        _postgres_dsn(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        saver = PostgresSaver(conn)
        saver.setup()
    except BaseException:
        conn.close()
        raise
    return saver


def build_graph(
    model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    use_postgres_checkpointer: bool = False,
):
    """Compile the expense-parsing graph.

    ``model`` and ``checkpointer`` are injection points for tests. When
    ``use_postgres_checkpointer`` is set, a Postgres checkpointer is built
    from ``settings.database_url``; if Postgres is unreachable the graph
    degrades to an in-memory checkpointer instead of raising.
    """
    if model is None:
        model = build_chat_model()
    if checkpointer is None and use_postgres_checkpointer:
        try:
            checkpointer = _create_postgres_checkpointer()
        except Exception as exc:  # noqa: BLE001 - graceful degradation by design
            logger.warning("Postgres checkpointer unavailable (%s); using MemorySaver", exc)
            checkpointer = MemorySaver()

    builder = StateGraph(AgentState)
    builder.add_node("parse_intent", _make_parse_node(model))
    builder.add_edge(START, "parse_intent")
    builder.add_edge("parse_intent", END)
    return builder.compile(checkpointer=checkpointer)
