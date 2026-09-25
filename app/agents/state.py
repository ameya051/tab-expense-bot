"""Shared agent state for the LangGraph expense-parsing graph.

State shape:
- ``messages``: conversation history (LangChain message objects); the
  ``add_messages`` reducer appends new messages instead of replacing them.
- ``user_id``: Telegram user id the graph is serving.
- ``preferred_currency``: user's default currency code (e.g. ``"INR"``).
- ``parsed_intent``: validated intent produced by the parse node, or ``None``
  before parsing has run.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.schemas import ParsedIntent


class AgentState(TypedDict):
    """State flowing through the LangGraph expense agent."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: int
    preferred_currency: str
    parsed_intent: ParsedIntent | None
