# 💰 Expense Tracker Bot

A personal Telegram bot for tracking expenses using natural language, powered by Groq (LLaMA) for NLP parsing and PostgreSQL for storage.

## Quick Start

### 1. Prerequisites

- Python 3.12+
- PostgreSQL (or Docker)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Groq API key (from [console.groq.com](https://console.groq.com))

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

```bash
# Development (polling mode — no ngrok needed)
uvicorn app.main:app --reload

# Production (webhook mode)
# Set MODE=webhook and WEBHOOK_BASE_URL in .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

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
- **NLP:** Groq API (LLaMA 3.3 70B)
- **Database:** PostgreSQL + SQLAlchemy (async)
- **Charts:** matplotlib (server-side PNG)

## Deployment

### Railway

1. Push to GitHub
2. Connect repo in Railway
3. Add environment variables
4. Set `MODE=webhook` and `WEBHOOK_BASE_URL` to your Railway URL
5. Deploy

### Docker

```bash
docker build -t expense-bot .
docker run --env-file .env -p 8000:8000 expense-bot
```
