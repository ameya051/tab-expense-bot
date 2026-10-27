# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Telegram expense-tracking bot. Users type or speak things like "spent 500 on groceries" or "set my food budget to 5000"; an LLM agent (via OpenRouter, tool calling) picks and chains tools, Pydantic validates their arguments, and async SQLAlchemy stores the result in PostgreSQL. Also: charts, budgets with alerts, recurring expenses, multi-currency with live FX, CSV export.

## Commands

The dev setup is docker-compose (Postgres 16 + the app with `uvicorn --reload`). `DATABASE_URL` in `.env` points at host `db`, which only resolves inside the compose network, so run DB-related commands in the `app` container.

```bash
docker compose up -d                                   # start db + app
docker compose logs -f app                             # follow logs
docker compose exec -T app alembic upgrade head        # apply migrations
docker compose exec -T app python scripts/webhook_info.py   # what webhook Telegram has registered
docker compose exec -T db psql -U postgres -d expense_bot   # SQL shell
docker compose restart app                             # reload doesn't always notice edits through the Windows bind mount
```

Without Docker: `uvicorn app.main:app --reload` from a venv with `MODE=polling` (no public HTTPS needed) and a reachable `DATABASE_URL`.

- **Tests:** there is no test suite. To check logic, run one-off scripts with `docker compose exec -T app python - <<'EOF' ... EOF`. For date logic, patch `local_today` in the module under test. To test the agent loop without the network, replace `AgentRunner.client` with a stub whose `chat.completions.create` returns scripted messages (`content`, `tool_calls`).
- **Lint:** ruff is used ad hoc (it isn't pinned in requirements): `ruff check app`. Broad `except Exception` blocks carry `# noqa: BLE001` with a reason.

## Architecture

### Request path

Telegram → `POST /webhook` ([app/main.py](app/main.py)): the secret header is checked with `compare_digest`, the update goes into `bot_app.update_queue`, and the endpoint **returns 200 immediately**. Processing inline caused Telegram to redeliver updates during slow AI calls, which logged duplicate expenses. python-telegram-bot (PTB 21, `concurrent_updates(True)`) then runs handlers, in a separate asyncio task per update.

### Handler order ([app/bot/setup.py](app/bot/setup.py))

Order matters:
- **group -2:** `_log_incoming_update`. It sets the per-update log context and never stops processing.
- **group -1:** group-chat refusal. Raises `ApplicationHandlerStop` for anything that isn't a private chat.
- **group 0:**
  - the onboarding `ConversationHandler` (`/start`, `/settings`, `/skip`)
  - commands, and the button callbacks (`cancel_recurring:`, `agent_confirm:`/`agent_cancel:`)
  - voice
  - free text (the agent), which is the catch-all and **must stay last**

Data handlers use the `_PRIVATE` filter (`ChatType.PRIVATE & UpdateType.MESSAGE`). Edited messages have `update.message=None` and would crash the handlers.

### Layers
- **Handlers** ([app/bot/handlers/](app/bot/handlers/), [onboarding.py](app/bot/onboarding.py)) and [render.py](app/bot/render.py) are the only code that knows Telegram.
- **Tools** ([app/agent/tools/](app/agent/tools/)) are the application layer between handlers and services. They validate arguments, call services, and return a `ToolResult`: `data` for the LLM and `outputs` (`TextOut`/`PhotoOut`/`DocumentOut` from [outputs.py](app/agent/outputs.py)) for the user. They never import `telegram`.
- **Services** ([app/services/](app/services/)) take an `AsyncSession` and contain the business rules and queries. The scheduler calls them directly.
- Slash commands ([commands.py](app/bot/handlers/commands.py)) are thin shortcuts that call `execute_tool` directly, without the LLM.
- `AgentRunner` and `VoiceTranscriber` are built once and stored in `context.bot_data`, not module globals.

### Agent contract

[app/agent/runner.py](app/agent/runner.py) is a hand-rolled tool-calling loop over the `openai` SDK (no agent framework):
- Each step sends the system prompt ([prompt.py](app/agent/prompt.py)), recent history and the tool specs. Tool calls run sequentially and their `data` goes back as `tool` messages.
- If every result in a step is `final` (its outputs already answer the user), the turn ends without another LLM call, so "spent 200 on lunch" costs one call. Results the LLM must phrase (`get_spending`, `show=false` lookups, errors) get a closing call.
- At most `AGENT_MAX_STEPS` LLM calls; the last one uses `tool_choice="none"`.
- A tool registered with `writes=True` is not run twice with the same arguments in one turn.
- A tool with a `confirm` prompt (`change_currency`) isn't run by the LLM. It returns Yes/No buttons, the `PendingAction` is kept in `user_data`, and `agent_confirm_callback` runs it.
- `run()` never raises. It flags `ai_failed`/`failed` and keeps the outputs produced so far.

Tools are registered with `@tool(...)` in [registry.py](app/agent/registry.py), and their argument models live in [app/schemas.py](app/schemas.py). Argument validation errors go back to the LLM so it can correct itself. **Adding a capability means an args model, a tool, and (if needed) a line in the prompt.** `user_id` and the currency come from `ToolContext`, never from tool arguments. Each tool opens its own session; never hold one across an LLM call.

Date ranges use the shared `DateRangeArgs`: a named `period` resolved by `expense_service.resolve_date_range`, or explicit `start_date`/`end_date`. See `resolve_range`/`period_label` in [tools/reports.py](app/agent/tools/reports.py).

Memory ([memory.py](app/agent/memory.py)) is the last `AGENT_HISTORY_TURNS` turns in PTB `user_data`, dropped after `AGENT_HISTORY_TTL_MINUTES` idle. It is in-process only. Turns for one user are serialised with a per-user lock in [handlers/agent.py](app/bot/handlers/agent.py).

Retries are owned by `retry_ai_call` ([app/nlp/ai.py](app/nlp/ai.py)), which uses exponential backoff and doesn't retry permanent errors. The OpenAI clients are created with `max_retries=0` so the SDK's retries don't multiply.

### Money invariants
- Every stored amount (`expenses.amount`, `recurring_expenses.amount`, `budgets.monthly_limit`) is in the **user's `preferred_currency`**. Recurring expenses and budgets have **no currency column**.
- A foreign-currency expense is converted at log time. The original is kept in `original_amount` / `original_currency`.
- **Never store an unconverted amount.** If conversion fails or the currency isn't in `SUPPORTED_CURRENCIES` (Frankfurter/ECB), refuse to save.
- `user_service.change_currency` rewrites all of a user's amounts in one transaction. It fetches every FX rate before writing, and restores exact originals when switching back.
- Money is `NUMERIC(10,2)`. `schemas.MAX_AMOUNT` and `is_storable_amount` mirror that column. CHECK constraints require amounts > 0, so conversions clamp to at least 0.01.

### Time

Use `app.timeutils.today()` / `now()`, never `date.today()`. Everything shares one configured `TIMEZONE`: "today", month boundaries for budgets and summaries, the date in the LLM prompt, and the midnight scheduler. Weeks start on Monday.

### Replies
- Most replies use `parse_mode="HTML"`, so **escape all user- or LLM-derived text with `esc()`** ([app/formatting.py](app/formatting.py)). `TextOut.html` is HTML; the agent's final reply is plain text and is escaped before sending.
- Use `reply_chunked` for anything that can exceed 4096 characters.
- After an expense is committed, later failures (recurring setup, budget checks, the AI failing on a later step) must add a warning line rather than "something went wrong". Otherwise users retry and log the expense twice. `_turn_outputs` in [handlers/agent.py](app/bot/handlers/agent.py) applies this to whole turns using `AgentTurn.wrote`.

### Charts

[app/reports/charts.py](app/reports/charts.py) uses the matplotlib `Figure` API, because `pyplot` global state isn't thread-safe. Always call it through `render_chart()` in [app/reports/render.py](app/reports/render.py), which runs it in a threadpool and logs it.

### Recurring scheduler

[app/scheduler.py](app/scheduler.py) is an asyncio task started in the FastAPI lifespan:
- It runs once at startup to catch up, then every local midnight.
- It runs under a Postgres advisory lock, so only one worker processes recurring expenses.
- `recurring_service.log_occurrence` inserts the expense and advances `next_run_date` in one `SELECT … FOR UPDATE` transaction. It only proceeds if `next_run_date` still equals the expected date.
- `frequency` (`daily` / `weekly` / `monthly`) drives `recurring_service.compute_next_run_date`. `day_of_month` is set only for monthly rows; it keeps the anchor day so month-end clamping doesn't drift (31st → Feb 28 → Mar 31).

### Lifespan

The webhook is deliberately **not** deleted on shutdown. On a rolling deploy, the old process would remove the webhook the new process just registered.

### Database
- Postgres **15+** is required: the budgets unique index uses `NULLS NOT DISTINCT`, where `category IS NULL` means the user's total budget.
- `set_budget` and `upsert_user` rely on `ON CONFLICT` upserts.
- Migrations in [alembic/versions/](alembic/versions/) are hand-written. They are numbered `00N_description.py`, with string revision IDs chained through `down_revision`.

### Logging ([app/logging_setup.py](app/logging_setup.py))
- `configure_logging()` is called from `main.py`. Every line carries a context tag (`upd=… user=…`, or `job=recurring` for the scheduler), set with `set_log_context`.
- Every async service function is wrapped with `@log_call`, which logs its arguments, a result summary and timing. Add it to new service functions.
- Outgoing messages are logged by `LoggingBot` ([app/bot/logging_bot.py](app/bot/logging_bot.py)), which is passed to `ApplicationBuilder().bot(...)`.
- `httpx`/`httpcore` are kept at WARNING because Telegram request URLs contain the bot token.
- `LOG_SQL=true` logs raw SQL through the root handler. Don't use `echo=True`; it adds a second handler and every line prints twice.
- Logs intentionally include message text and amounts at INFO.

### Config

[app/config.py](app/config.py) uses pydantic-settings and is validated at import time. In `MODE=webhook`, `WEBHOOK_BASE_URL` and a `WEBHOOK_SECRET` matching `[A-Za-z0-9_-]{1,256}` are required. An invalid `TIMEZONE` stops startup.

## Docs in the repo

- `expense-tracker-handover.md` is the original pre-code design doc. It names Claude as the NLP layer; the code uses OpenRouter (`OPENROUTER_MODEL`).
- `INTERVIEW_GUIDE.md` is a long-form walkthrough of the architecture and design decisions.
