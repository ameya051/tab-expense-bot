"""Tool registry: definitions, OpenAI tool specs, argument validation and execution."""

import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.outputs import Button, Output, TextOut
from app.logging_setup import summarize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    """Who a tool acts for. Never taken from tool arguments, so the LLM can't change it."""

    user_id: int
    pref_currency: str


@dataclass(frozen=True)
class PendingAction:
    """A tool call waiting for the user to tap Yes."""

    token: str
    tool: str
    args: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class ToolResult:
    """Outcome of one tool call.

    data goes back to the LLM as the tool message. outputs are shown to the
    user as they are. final means the outputs already answer the user, so the
    agent may end the turn without another LLM call. wrote means something was
    committed to the database.
    """

    data: dict[str, Any]
    outputs: list[Output] = field(default_factory=list)
    final: bool = False
    wrote: bool = False
    ok: bool = True
    pending: PendingAction | None = None


@dataclass(frozen=True)
class Confirmation:
    """The HTML question to ask before running a tool.

    args, if set, replaces the arguments kept for the Yes button, e.g. to pin
    "the last expense" to a concrete id so a later expense can't take its place.
    """

    question: str
    args: BaseModel | None = None


ToolRun = Callable[[ToolContext, Any], Awaitable[ToolResult]]
# Returns the question to ask before running, or None if no confirmation is
# needed for these arguments.
ConfirmPrompt = Callable[[ToolContext, Any], Awaitable[Confirmation | None]]


@dataclass(frozen=True)
class Tool:
    """An action the agent can take."""

    name: str
    description: str
    args_model: type[BaseModel]
    run: ToolRun
    # True if the tool writes, so a repeat with the same arguments in one turn is refused.
    writes: bool = False
    confirm: ConfirmPrompt | None = None


TOOLS: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    args_model: type[BaseModel],
    *,
    writes: bool = False,
    confirm: ConfirmPrompt | None = None,
) -> Callable[[ToolRun], ToolRun]:
    """Register the decorated coroutine as a tool."""

    def register(run: ToolRun) -> ToolRun:
        if name in TOOLS:
            raise ValueError(f"duplicate tool name {name!r}")
        TOOLS[name] = Tool(name, description, args_model, run, writes, confirm)
        return run

    return register


def _parameters(args_model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for a tool's arguments, minus the model's title and docstring."""
    schema = args_model.model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)
    return schema


def openai_specs() -> list[dict[str, Any]]:
    """Tool definitions in the OpenAI chat-completions "tools" format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": _parameters(t.args_model),
            },
        }
        for t in TOOLS.values()
    ]


def error_result(message: str, **details: Any) -> ToolResult:
    """A failed call; the LLM sees the message and can correct itself."""
    return ToolResult(data={"error": message, **details}, ok=False)


def parse_args(tool_def: Tool, raw_args: str | dict[str, Any] | None) -> BaseModel | ToolResult:
    """Validate raw arguments (a JSON string from the LLM, or a dict).

    Returns the args model, or an error ToolResult explaining what was wrong.
    """
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            return error_result("arguments were not valid JSON")
    if raw_args is None:
        raw_args = {}
    try:
        return tool_def.args_model.model_validate(raw_args)
    except ValidationError as exc:
        problems = [
            {"field": ".".join(str(p) for p in err["loc"]), "problem": err["msg"]}
            for err in exc.errors()
        ]
        return error_result("invalid arguments", problems=problems)


async def run_tool(
    tool_def: Tool, ctx: ToolContext, args: BaseModel, *, confirmed: bool = False
) -> ToolResult:
    """Run a tool with validated arguments, asking for confirmation first if it needs it.

    Unexpected exceptions are logged and turned into an error result, so one
    failing tool doesn't end the whole turn.
    """
    if tool_def.confirm is not None and not confirmed:
        try:
            confirmation = await tool_def.confirm(ctx, args)
        except Exception:  # reported to the LLM instead of failing the turn
            logger.exception("confirm for %s(%s) failed", tool_def.name, summarize(args))
            return error_result("internal error while preparing the confirmation")
        if confirmation is not None:
            pending = PendingAction(
                token=secrets.token_hex(6),
                tool=tool_def.name,
                args=(confirmation.args or args).model_dump(mode="json"),
            )
            logger.info("tool %s needs confirmation token=%s", tool_def.name, pending.token)
            return ToolResult(
                data={"status": "waiting for the user to confirm with the buttons shown"},
                outputs=[
                    TextOut(
                        confirmation.question,
                        buttons=[[
                            Button("✅ Yes", f"agent_confirm:{pending.token}"),
                            Button("✖️ No", f"agent_cancel:{pending.token}"),
                        ]],
                    )
                ],
                final=True,
                pending=pending,
            )

    started = time.perf_counter()
    try:
        result = await tool_def.run(ctx, args)
    except Exception:  # reported to the LLM instead of failing the turn
        logger.exception("tool %s(%s) failed", tool_def.name, summarize(args))
        return error_result("internal error while running the tool; nothing more is known")
    logger.info(
        "tool %s(%s) -> %s (%.0f ms)",
        tool_def.name, summarize(args), summarize(result.data),
        (time.perf_counter() - started) * 1000,
    )
    return result


async def execute_tool(
    name: str,
    raw_args: str | dict[str, Any] | None,
    ctx: ToolContext,
    *,
    confirmed: bool = False,
) -> ToolResult:
    """Look up, validate and run a tool. Used by slash commands and button callbacks."""
    tool_def = TOOLS.get(name)
    if tool_def is None:
        return error_result(f"unknown tool {name!r}")
    args = parse_args(tool_def, raw_args)
    if isinstance(args, ToolResult):
        logger.info("tool %s rejected arguments: %s", name, summarize(args.data))
        return args
    return await run_tool(tool_def, ctx, args, confirmed=confirmed)
