# TASKS.md — Expense Tracker Bot Build Plan

Execution model: each task is self-contained, touches a bounded set of files,
and has explicit acceptance criteria (AC). Tasks are grouped into **waves**;
all tasks inside a wave are independent of each other and MAY be executed in
parallel by separate subagents. Tasks across waves MUST NOT be started before
the previous wave is fully accepted.

Per-task metadata:
- `files:` exact files the task owns — a subagent must not modify files owned
  by another task in the same wave
- `depends:` tasks that must be accepted first
- `parallel:` whether it can run alongside its wave siblings

Global conventions (apply to every task):
- Python 3.12+, managed with `pip` and a virtual environment (`venv`)
- Configuration is read from `.env` via Pydantic Settings (`app/config.py`)
- Code style: ruff with the repo `pyproject.toml`; no trailing whitespace; no stray `print()`
- Test commands that must pass after every task: `pytest` and `ruff check app/ tests/`
- Format command: `ruff format --check app/ tests/`
- All new modules must expose clean imports via `__init__.py` where appropriate
- Keep existing command handlers, scheduler, and budget-alert side effects untouched

---

## Parallelization map

```
Wave 0:  T01 | T02 | T03 | T04
Wave 1:  T05 | T06 | T07
Wave 2:  T08 | T09 | T10 | T11 | T12
Wave 3:  T13 | T14 | T15
Wave 4:  T16 | T17 | T18
Wave 5:  T19 | T20 | T21
```

Conflict-avoidance rules for parallel waves:
- `app/config.py` is owned by T06 (Wave 1); T13 (Wave 3) extends the same file
  and depends on T06, so the edit is sequential across waves.
- `app/bot/handlers.py` is owned by T14 (Wave 3); no other task modifies it.
- `tests/bot/test_handlers.py` is owned by T15 (Wave 3); T17 (Wave 4) extends it
  and depends on T15.
- `README.md` is owned by T19 (Wave 5); no other task touches it.
- `.env.example` is owned by T20 (Wave 5); no other task touches it.

---

## Wave 0 — Tooling & safety net (4 independent tasks, parallelizable)

### T01: Add pytest and ruff dependencies
- status: done
- `depends:` none · `parallel:` yes
- `files:` `requirements.txt`
- Append `pytest`, `pytest-asyncio`, and `ruff` to `requirements.txt` with
  version constraints compatible with the existing pinned stack (Python 3.12,
  FastAPI, Pydantic v2, SQLAlchemy 2.0).
- AC: `pip install -r requirements.txt` installs the new packages without
  version conflicts; `pytest --version` and `ruff --version` report installed
  versions.

### T02: Create pyproject.toml with pytest and ruff configuration
- status: done
- `depends:` none · `parallel:` yes
- `files:` `pyproject.toml`
- Create `pyproject.toml` with a `[tool.pytest.ini_options]` section enabling
  asyncio mode and a `[tool.ruff]` section targeting `app/` and `tests/`.
- AC: `pytest --version` runs without config errors; `ruff check app/ tests/`
  and `ruff format --check app/ tests/` run without config errors.

### T03: Create tests directory and NLP parser smoke test
- status: done
- `depends:` none · `parallel:` yes
- `files:` `tests/__init__.py`, `tests/conftest.py`, `tests/nlp/test_parser.py`
- Create the `tests/` package, add any shared fixtures to `tests/conftest.py`,
  and write `tests/nlp/test_parser.py` that imports the existing `NLPParser`,
  mocks the Groq client, and asserts the returned intent matches the
  `ParsedIntent` schema for a sample message.
- AC: `pytest tests/nlp/test_parser.py -v` passes; the test asserts intent
  type, amount, category, and currency fields.

### T04: Run ruff on the existing codebase
- status: done
- `depends:` none · `parallel:` yes
- `files:` all `.py` files under `app/`
- Run `ruff check app/` and `ruff format app/`; fix all lint and format
  issues in the existing source. Do not change behavior.
- AC: `ruff check app/` passes; `ruff format --check app/` passes; the bot
  still imports and starts (`python -c "import app.main"`).

---

## Wave 1 — Provider abstraction (3 independent tasks, parallelizable)

### T05: Create swappable LLM provider factory
- status: done
- `depends:` T02 · `parallel:` yes
- `files:` `app/agents/__init__.py`, `app/agents/providers.py`
- Create `app/agents/` package. In `providers.py`, implement `build_chat_model()`
  that returns a LangChain chat model for provider values `groq`, `openai`, and
  `anthropic`, selected from settings (`app.config.settings.llm_provider`).
- AC: `build_chat_model("groq")` returns a `ChatGroq` instance;
  `build_chat_model("openai")` returns a `ChatOpenAI` instance;
  `build_chat_model("anthropic")` returns a `ChatAnthropic` instance;
  unsupported provider raises `ValueError` with a clear message.

### T06: Add LLM provider settings to config
- status: done
- `depends:` T02 · `parallel:` yes
- `files:` `app/config.py`
- Extend `Settings` with `llm_provider: Literal["groq", "openai", "anthropic"]`,
  `groq_model`, `openai_model`, `anthropic_model`, and optional API key fields
  (`openai_api_key`, `anthropic_api_key`). Keep `groq_api_key` as the Groq key.
- AC: New settings are loadable from `.env`; existing settings and tests still
  pass; `settings.llm_provider` defaults to `"groq"`.

### T07: Add provider factory unit tests
- status: done
- `depends:` T05, T06 · `parallel:` yes
- `files:` `tests/agents/test_providers.py`
- Write unit tests for `build_chat_model()` covering each supported provider
  and the unsupported-provider error path.
- AC: `pytest tests/agents/test_providers.py -v` passes; tests verify the
  returned chat model class for each provider.

---

## Wave 2 — LangGraph graph (5 independent tasks, parallelizable)

### T08: Define AgentState TypedDict
- status: done
- `depends:` T05 · `parallel:` yes
- `files:` `app/agents/state.py`
- Define `AgentState` as a `TypedDict` with keys: `messages` (list of LangChain
  message objects), `user_id` (int), `preferred_currency` (str), and
  `parsed_intent` (`ParsedIntent | None`).
- AC: `app/agents/state.py` imports cleanly; `AgentState` has all required keys
  with correct type annotations.

### T09: Create structured intent-parsing prompt
- status: done
- `depends:` T05 · `parallel:` yes
- `files:` `app/agents/prompts.py`
- Create a chat prompt template (`ChatPromptTemplate`) with a system message
  that instructs the model to parse expenses into the `ParsedIntent` schema,
  and a human message placeholder for the user text. Inject today's date and
  the user's default currency.
- AC: `app/agents/prompts.py` exposes a `INTENT_PROMPT` (or callable) that
  returns a system message and a user message when formatted; the system
  message contains today's date and the default currency.

### T10: Build StateGraph with Postgres checkpointer
- status: done
- `depends:` T06, T08, T09 · `parallel:` yes
- `files:` `app/agents/graph.py`
- Build a `StateGraph` with a single parse node that invokes the chat model
  with structured output into `ParsedIntent`. Compile the graph with a Postgres
  checkpointer configured from `DATABASE_URL` using a dedicated checkpoint
  table/prefix (`expense_bot_checkpoints`).
- AC: Compiled graph accepts `AgentState`; parse node returns a validated
  `ParsedIntent`; checkpointer is configured via `settings.database_url`;
  graph runs end-to-end with `ainvoke`.

### T11: Create async runner facade
- status: done
- `depends:` T10 · `parallel:` yes
- `files:` `app/agents/runner.py`
- Implement `run_agent(user_id: int, text: str, default_currency: str = "INR")
  -> ParsedIntent` that builds `AgentState`, invokes the compiled graph, and
  returns `parsed_intent`. On any failure, return `UnknownIntent`.
- AC: `run_agent` returns a `ParsedIntent`; invalid model output or exceptions
  fall back to `UnknownIntent` without raising.

### T12: Add graph and runner tests
- status: done
- `depends:` T10, T11 · `parallel:` yes
- `files:` `tests/agents/test_graph.py`, `tests/agents/test_runner.py`
- Mock the LLM to produce sample structured outputs; test that the graph parse
  node and `run_agent` return expected `LogExpenseIntent` and `QueryIntent`,
  and that malformed output falls back to `UnknownIntent`.
- AC: `pytest tests/agents/test_graph.py tests/agents/test_runner.py -v`
  passes; all three intent outcomes are covered.

---

## Wave 3 — Side-by-side integration (3 tasks, parallelizable)

### T13: Add NLP_MODE feature flag to config
- status: todo
- `depends:` T06 · `parallel:` yes
- `files:` `app/config.py`
- Add `nlp_mode: Literal["legacy", "langgraph"] = "legacy"` to `Settings`,
  loaded from env var `NLP_MODE`.
- AC: `settings.nlp_mode` defaults to `"legacy"`; setting `NLP_MODE=langgraph`
  loads correctly; existing tests still pass.

### T14: Branch message and voice handlers on NLP_MODE
- status: todo
- `depends:` T11, T13 · `parallel:` yes
- `files:` `app/bot/handlers.py`
- Refactor `message_handler` in `app/bot/handlers.py` so that when
  `settings.nlp_mode == "legacy"` it calls the existing `nlp_parser.parse()`
  path, and when `"langgraph"` it calls `run_agent()`. Both paths then reuse
  the existing `_handle_*` dispatch logic. Apply the same branch to
  `voice_handler` after transcription so voice text is routed through the same
  parse path as free text.
- AC: `legacy` mode calls the existing parser; `langgraph` mode calls
  `run_agent()`; `voice_handler` reuses the same branch; handler import
  succeeds with `NLP_MODE=langgraph`.

### T15: Add handler integration tests for both modes
- status: todo
- `depends:` T03, T14 · `parallel:` yes
- `files:` `tests/bot/test_handlers.py`
- Write parameterized integration tests that send identical sample messages
  through `message_handler` in both `legacy` and `langgraph` modes (LLM
  mocked) and assert equivalent `ParsedIntent` shapes.
- AC: Parameterized tests cover log, query, delete, and unknown intents in
  both modes; `pytest tests/bot/test_handlers.py -v` passes.

---

## Wave 4 — Validation & QA (3 tasks, T18 sequential)

### T16: Run ruff across app and tests
- status: todo
- `depends:` T04, T12, T15 · `parallel:` yes
- `files:` all `.py` files under `app/` and `tests/`
- Run `ruff check app/ tests/` and `ruff format app/ tests/`; fix any issues.
- AC: `ruff check app/ tests/` passes; `ruff format --check app/ tests/`
  passes.

### T17: Add end-to-end intent tests with reply assertions
- status: todo
- `depends:` T15 · `parallel:` yes
- `files:` `tests/bot/test_handlers.py`, `tests/test_end_to_end.py`
- Extend `tests/bot/test_handlers.py` or add `tests/test_end_to_end.py` with
  tests that assert handler replies contain expected emojis, amounts, and
  categories for log, query, delete, and unknown intents.
- AC: Tests verify Telegram reply text contains expected markers for each
  intent; `pytest tests/test_end_to_end.py -v` passes.

### T18: Verify commands and scheduler are unaffected
- status: todo
- `depends:` T16, T17 · `parallel:` no
- `files:` `app/bot/handlers.py`, `app/bot/setup.py`, `app/scheduler.py`,
  `app/services/budget_service.py`
- Manually or via automated checks verify that `/start`, `/summary`, `/report`,
  `/budget`, `/recurring`, `/export`, `/delete`, `/settings`, and the scheduler
  loop still work in both `legacy` and `langgraph` modes. Do not modify these
  files; this task is verification-only.
- AC: A checklist is completed confirming all commands and the scheduler
  function correctly; no regressions are introduced.

---

## Wave 5 — Cleanup & docs (3 tasks, T21 sequential)

### T19: Update README with new mode and provider docs
- status: todo
- `depends:` T18 · `parallel:` yes
- `files:` `README.md`
- Update `README.md` to explain `NLP_MODE`, how to switch between `legacy` and
  `langgraph`, how to configure each LLM provider (`LLM_PROVIDER`,
  `GROQ_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`, API keys), and that the
  Postgres checkpoint table is auto-created by LangGraph.
- AC: README contains a new "LangGraph agent mode" section with env var names,
  provider options, and checkpoint note.

### T20: Add new env vars to .env.example
- status: todo
- `depends:` T18 · `parallel:` yes
- `files:` `.env.example`
- Append `NLP_MODE=legacy`, `LLM_PROVIDER=groq`, `GROQ_MODEL`, `OPENAI_MODEL`,
  `ANTHROPIC_MODEL`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` placeholders to
  `.env.example`.
- AC: `.env.example` contains all new variables; copying it to `.env` allows
  the app to start in both `legacy` and `langgraph` modes.

### T21: Final review and cleanup
- status: todo
- `depends:` T19, T20 · `parallel:` no
- `files:` `app/`, `tests/`
- Do a final pass over source and test files: remove dead code, remove stray
  `print` statements, ensure all tests pass, and ensure ruff passes. Verify no
  unintended changes to command handlers, scheduler, or budget-alert logic.
- AC: `pytest` passes; `ruff check app/ tests/` passes;
  `ruff format --check app/ tests/` passes; no stray debug output remains.
