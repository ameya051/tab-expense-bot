"""The agent loop: the LLM picks tools, we run them, until it has an answer."""

import json
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from openai import AsyncOpenAI

import app.agent.tools  # noqa: F401 (registers every tool)
from app.agent.outputs import Output
from app.agent.prompt import build_system_prompt
from app.agent.registry import (
    TOOLS,
    PendingAction,
    ToolContext,
    ToolResult,
    error_result,
    openai_specs,
    parse_args,
    run_tool,
)
from app.config import settings
from app.logging_setup import summarize
from app.nlp.ai import AI_TIMEOUT_SECONDS, AIUnavailableError, retry_ai_call

logger = logging.getLogger(__name__)

# Tool calls allowed per user message, across all steps.
MAX_TOOL_CALLS = 8
# Longest tool result sent back to the LLM (and kept in history).
MAX_TOOL_RESULT_CHARS = 6000


@dataclass
class AgentTurn:
    """What one user message produced."""

    outputs: list[Output] = field(default_factory=list)
    final_text: str | None = None
    wrote: bool = False
    # Messages to keep in history: the user message, assistant tool calls,
    # tool results and the final reply.
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending: list[PendingAction] = field(default_factory=list)
    ai_failed: bool = False
    failed: bool = False


class AgentRunner:
    """Runs the tool-calling loop against OpenRouter's chat-completions API."""

    def __init__(self, api_key: str, model: str) -> None:
        # max_retries=0: retry_ai_call owns retries (the SDK's own would multiply them)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self.model = model
        self.tool_specs = openai_specs()

    async def run(
        self, text: str, ctx: ToolContext, history: list[dict[str, Any]]
    ) -> AgentTurn:
        """Handle one user message. Never raises; failures are flagged on the turn."""
        turn = AgentTurn(messages=[{"role": "user", "content": text}])
        system = {"role": "system", "content": build_system_prompt(ctx)}
        # Messages up to here form complete steps (every tool call has its result).
        complete = len(turn.messages)
        done: dict[tuple[str, str], dict] = {}
        calls_used = 0
        logger.info("agent turn model=%s history=%d text=%s",
                    self.model, len(history), summarize(text))

        try:
            for step in range(1, settings.agent_max_steps + 1):
                # On the last step the LLM must answer, not call more tools.
                last_step = step == settings.agent_max_steps
                message = await retry_ai_call(
                    partial(self._complete, [system, *history, *turn.messages], step, last_step),
                    label="AI",
                )
                calls = message.tool_calls or []
                if not calls:
                    turn.final_text = (message.content or "").strip() or None
                    turn.messages.append({"role": "assistant", "content": message.content or ""})
                    complete = len(turn.messages)
                    break

                turn.messages.append(_assistant_message(message.content, calls))
                needs_reply = False
                for call in calls:
                    if calls_used >= MAX_TOOL_CALLS:
                        result = error_result("tool call limit for this message reached")
                    else:
                        calls_used += 1
                        result = await self._run_call(call.function.name,
                                                      call.function.arguments, ctx, done)
                    turn.outputs.extend(result.outputs)
                    turn.wrote = turn.wrote or result.wrote
                    if result.pending is not None:
                        turn.pending.append(result.pending)
                    needs_reply = needs_reply or not result.final
                    turn.messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": _dump(result.data)}
                    )
                complete = len(turn.messages)

                # Every tool already showed the user its result: that is the
                # reply, so skip another LLM call ("spent 200 on lunch" costs one).
                if not needs_reply:
                    break
        except AIUnavailableError:
            logger.warning("agent stopped: AI unavailable")
            turn.ai_failed = True
        except Exception:
            logger.exception("agent loop failed")
            turn.failed = True

        if complete < len(turn.messages):
            # Drop a step cut short, so history never has a call without its result.
            del turn.messages[complete:]
        if turn.ai_failed or turn.failed:
            turn.messages.append({"role": "assistant", "content": "(reply interrupted)"})
        logger.info(
            "agent turn done outputs=%d wrote=%s final=%s",
            len(turn.outputs), turn.wrote, summarize(turn.final_text),
        )
        return turn

    async def _complete(self, messages: list[dict[str, Any]], step: int, last_step: bool):
        """One chat-completions call (no temperature — gpt-5.6 models reject it)."""
        started = time.perf_counter()
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tool_specs,
            tool_choice="none" if last_step else "auto",
        )
        message = completion.choices[0].message
        logger.info(
            "LLM step %d (%.2f s): tools=%s text=%s",
            step,
            time.perf_counter() - started,
            [c.function.name for c in message.tool_calls or []],
            summarize(message.content),
        )
        return message

    async def _run_call(
        self,
        name: str,
        raw_args: str,
        ctx: ToolContext,
        done: dict[tuple[str, str], dict],
    ) -> ToolResult:
        """Validate and run one tool call, refusing repeats of a write in the same turn."""
        tool_def = TOOLS.get(name)
        if tool_def is None:
            return error_result(f"unknown tool {name!r}")
        args = parse_args(tool_def, raw_args)
        if isinstance(args, ToolResult):
            logger.info("tool %s rejected arguments: %s", name, summarize(args.data))
            return args

        key = (name, args.model_dump_json())
        if tool_def.writes and key in done:
            logger.warning("duplicate %s call skipped: %s", name, summarize(args))
            return ToolResult(
                data={"error": "already done earlier in this message; not repeated",
                      "earlier_result": done[key]},
                final=True,
            )
        result = await run_tool(tool_def, ctx, args)
        if tool_def.writes and result.ok:
            done[key] = result.data
        return result


def _assistant_message(content: str | None, calls) -> dict[str, Any]:
    """The assistant's tool-call message, as plain data for history."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in calls
        ],
    }


def _dump(data: dict[str, Any]) -> str:
    text = json.dumps(data, default=str, ensure_ascii=False)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "…(truncated)"
    return text
