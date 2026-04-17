# Telegram Expense Tracker Bot — Project Handover

## Overview
A personal expense tracking bot built on Telegram, using Claude as the NLP layer to parse natural language input into structured expense data.

---

## Final Tech Stack
```
python-telegram-bot  →  FastAPI  →  Groq API  →  PostgreSQL
                                  ↓
                             matplotlib (charts)
```
- **Bot framework:** `python-telegram-bot`
- **Backend:** FastAPI
- **NLP:** Groq API
- **Database:** PostgreSQL
- **Charts:** matplotlib (rendered server-side, sent as PNG)
- **Hosting:** Railway / Fly.io / any VPS

---

## Architecture

### Components
1. **Telegram Bot** — entry point, handles commands and free-text via webhook
2. **Claude NLP Layer** — parses natural language into structured JSON
3. **FastAPI Backend** — routes requests, talks to DB and Claude
4. **PostgreSQL** — stores users and expenses
5. **Reporting Engine** — aggregates data, generates charts and text tables

### Data Flow
```
User message ("spent $45 on groceries yesterday")
  → Telegram Bot
  → FastAPI Backend
  → Claude API (NLP parsing)
  → { amount: 45, category: "groceries", date: "2026-04-16" }
  → PostgreSQL (INSERT)
  → "✅ Logged $45 for groceries on Apr 16" (reply)
```

---

## Database Schema

```sql
CREATE TABLE users (
    telegram_id  BIGINT PRIMARY KEY,  -- from Telegram, permanent & unique
    first_name   TEXT,
    username     TEXT,                -- optional, can change
    created_at   TIMESTAMP
);

CREATE TABLE expenses (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT REFERENCES users(telegram_id),
    amount       NUMERIC(10, 2),
    currency     TEXT DEFAULT 'USD',
    category     TEXT,
    date         DATE,
    description  TEXT,
    created_at   TIMESTAMP DEFAULT NOW()
);
```

> **Note:** No phone number is stored. Telegram exposes only `user_id`, `username`, and `first_name` to bots. `telegram_id` is permanent and unique — sufficient for all auth needs.

---

## Claude NLP — Intent Schema

Every user message is sent to Claude, which returns one of the following JSON shapes:

```json
{
  "intent": "log_expense | query | delete | unknown",
  "amount": 45.00,
  "currency": "USD",
  "category": "groceries",
  "date": "2026-04-16",
  "description": "weekly grocery run"
}
```

For query intents, additional fields:
```json
{
  "intent": "query",
  "period": "this_month | this_week | today | all_time",
  "group_by": "category | day | none",
  "limit": 5
}
```

Claude returns `null` or `{ "intent": "unknown" }` if the message is not expense-related.

---

## Supported Query Intents

| User says | Intent | SQL shape |
|---|---|---|
| "how much this week?" | total, period=week | SUM by date range |
| "what did I spend on food?" | total, category=food | SUM + WHERE category |
| "show my top categories" | breakdown, group=category | GROUP BY category |
| "spending trend this month" | trend, period=month | GROUP BY day |
| "last 5 expenses" | list, limit=5 | ORDER BY date DESC |

---

## Visualization Strategy

Telegram does **not** support native tables or charts. Workaround:

- **Charts** → rendered as PNG server-side with `matplotlib` → sent via `send_photo()`
- **Tables** → monospaced text in MarkdownV2 code blocks → sent via `send_message()`
- Both are sent together in a single response

Example text table format:
```
📊 April Summary

Category          Amount
─────────────────────────
🍔 Food           $312.50
🚗 Transport       $89.00
🎬 Entertainment   $45.00
─────────────────────────
Total             $446.50
```

---

## Bot Commands

| Command | Description |
|---|---|
| `/summary` | Monthly spend breakdown (chart + table) |
| `/report` | Spending trend chart |
| `/delete` | Remove last or specific expense |
| Free text | Log expense or query in natural language |

---

## Phase 1 Scope (MVP)
- [x] Log expenses via natural language
- [x] Confirm on save
- [x] Query totals by period and category
- [x] Bar chart (by category) as PNG
- [x] Line chart (trend over time) as PNG
- [x] Monospace text summary table
- [x] `/summary`, `/report`, `/delete` commands
- [x] User identification via Telegram `user_id`

## Phase 2 Features (Post-MVP)
- [ ] Multi-currency support with live FX conversion
- [ ] Recurring expense detection
- [ ] Budget setting and alerts ("80% of food budget reached")
- [ ] CSV export via `send_document()`
- [ ] Voice message support (Whisper transcription → same NLP pipeline)
- [ ] Google Sheets sync (deferred — adds OAuth complexity)

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Platform | Telegram (not WhatsApp) | Free, instant setup, no business approval, rich bot API |
| NLP | Claude API | Handles fuzzy dates, typos, multi-currency naturally |
| Webhook vs polling | Webhook | Lower latency, no idle loop |
| Auth | Telegram `user_id` | No separate login needed, permanent and unique |
| Charts | matplotlib server-side | Full control, no external dependency |
| Hosting | Railway / Fly.io | Low cost for personal use |

---

## What Has NOT Been Built Yet
This document is a design handover. No code has been written. The suggested starting point is the **Claude NLP parser**, as all other components depend on it.
