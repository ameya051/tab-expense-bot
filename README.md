# 💰 Expense Tracker Bot

A personal Telegram bot for tracking expenses using natural language, powered by OpenAI models via OpenRouter for NLP parsing and PostgreSQL for storage.

## Quick Start

### 1. Prerequisites

- Python 3.12+
- PostgreSQL 15+ (or Docker) — the budgets table relies on `NULLS NOT DISTINCT` unique indexes
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- An OpenRouter API key (from [openrouter.ai](https://openrouter.ai/keys))

### 2. Setup

```bash
# Clone and enter the project
cd expense-bot

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create your .env file
copy .env.example .env
# Edit .env with your actual credentials
```

### 3. Database

**Option A — Docker (recommended):**
```bash
docker-compose up -d
```

**Option B — Existing PostgreSQL:**
```bash
# Create the database
createdb expense_bot
# Update DATABASE_URL in your .env
```

Run migrations:
```bash
alembic upgrade head
```

### 4. Run

The bot defaults to **webhook mode**. For local development you can use polling so you don't need an HTTPS tunnel.

```bash
# Production / deployed (webhook mode)
# Ensure MODE=webhook and WEBHOOK_BASE_URL are set in .env
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Development (polling mode — no HTTPS needed)
# Set MODE=polling in .env
uvicorn app.main:app --reload
```

## Webhook Mode

In production the bot receives updates from Telegram via HTTPS webhooks.

### Requirements

- A public HTTPS URL (`https://...`).
- Telegram only accepts webhook ports `443`, `80`, `88`, and `8443`.
- `WEBHOOK_BASE_URL` set to your public domain (e.g. `https://expense-bot.example.com`).
- **`WEBHOOK_SECRET` (required in webhook mode)** — a strong random secret Telegram echoes back in the `X-Telegram-Bot-Api-Secret-Token` header on every update. The app refuses to start in webhook mode without it, and requests without a matching secret are rejected with `403`. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Only `A-Z`, `a-z`, `0-9`, `_` and `-` are allowed (1–256 chars); the app refuses to start otherwise.

### Local webhook testing with ngrok

If you want to test webhooks locally without polling:

```bash
# Terminal 1: expose the local server
ngrok http 8000

# Terminal 2: set the ngrok HTTPS URL and run
# WEBHOOK_BASE_URL=https://<ngrok-id>.ngrok.io
uvicorn app.main:app --reload
```

### Verify the webhook

```bash
python scripts/webhook_info.py
```

This prints the current webhook URL, pending update count, and any Telegram-reported errors.

## Usage

Talk to your bot in natural language:

| What you say | What happens |
|---|---|
| "spent ₹500 on groceries" | Logs the expense |
| "₹200 cab yesterday" | Logs with yesterday's date |
| "how much this week?" | Shows total spending |
| "show my top categories" | Category breakdown chart |
| "last 5 expenses" | Lists recent expenses |

### Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and usage help |
| `/summary` | Monthly breakdown (chart + table) |
| `/report` | Spending trend chart |
| `/delete` | Remove last expense |

## Tech Stack

- **Bot:** python-telegram-bot v21
- **Backend:** FastAPI + Uvicorn
- **NLP:** OpenRouter API (OpenAI GPT-5.6 Luna)
- **Database:** PostgreSQL + SQLAlchemy (async)
- **Charts:** matplotlib (server-side PNG)

## Deployment

### Cheap hosting options

| Provider | Est. cost | Notes |
|---|---|---|
| **Railway Hobby** | $0–5/mo | Easiest managed deploy; often stays within the $5 monthly credit. |
| **Fly.io** | ~$3–6/mo | Cheapest always-on option; Postgres is unmanaged by default. |
| **Render Starter** | ~$14/mo | Predictable managed stack (compute + Postgres); free tier sleeps after 15 min. |
| **Hetzner CX22** | ~$5/mo | Cheapest VPS; you manage TLS, Postgres, and backups. |
| **Oracle Cloud Free Tier** | $0 | Generous always-on ARM VM; setup can be painful. |

For a personal expense bot, **Railway Hobby** is the fastest path to a working HTTPS webhook + managed Postgres.

### Railway

1. Push to GitHub.
2. Connect repo in Railway.
3. Add environment variables (`TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, `DATABASE_URL`, `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` (required), `MODE=webhook`).
4. Deploy.
5. Run `python scripts/webhook_info.py` to confirm the webhook is set.

### Docker

```bash
docker build -t expense-bot .
docker run --env-file .env -p 8000:8000 expense-bot
```

## Timezone

`TIMEZONE` (default `Asia/Kolkata`) is the IANA timezone used for "today", month boundaries in budgets/summaries, and for the recurring-expense scheduler, which runs at local midnight.

## Development fallback (polling)

If you don't want to set up HTTPS locally, set `MODE=polling` in `.env`. Polling is kept as a non-breaking fallback for local development only.

## Health check

```bash
curl https://<your-domain>/health
# {"status":"ok","mode":"webhook","webhook_url":"https://.../webhook"}
```
