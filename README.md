# Strava AI Coach — Telegram Bot

A Telegram bot that pulls your Strava running data and displays detailed stats: km splits, pace trends, heart rate zones, monthly summaries, and all-time totals. Built to feed rich data into a local LLM (Ollama/Gemma) for coaching insights.

## Features

| Command | What it does |
|---|---|
| `/lastrun` | Full detail of your latest run — km splits, pace trend, best efforts |
| `/analyze N` | Same detail for the Nth most recent run |
| `/trends` | Table of your last 10 runs |
| `/week` | This week's runs with per-run breakdown |
| `/monthly` | Every run this calendar month in a table |
| `/overall` | All-time, year-to-date, and last-4-week totals |
| `/token` | Link your Strava account (one-time setup) |

## Stack

- **python-telegram-bot 21** — async Telegram polling
- **aiohttp** — async HTTP to Strava API and Ollama
- **SQLite** — persists Strava tokens per Telegram user (survives restarts)
- **Ollama + Gemma** — local LLM for coaching analysis (wired up in `ollama_client.py`, integration coming)
- **FastAPI** — one-shot OAuth callback server (`auth_server.py`), only needed during initial setup

## Setup

### 1. Prerequisites

- Python 3.11 (via pyenv recommended)
- [Ollama](https://ollama.com) installed and running locally
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Strava API app (create one at [strava.com/settings/api](https://www.strava.com/settings/api))
  - Set the **Authorization Callback Domain** to `localhost`

### 2. Clone and create venv

```bash
git clone <repo>
cd Strava
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

Copy the example and fill in your values:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN=your_token_from_botfather
STRAVA_CLIENT_ID=your_strava_app_client_id
STRAVA_CLIENT_SECRET=your_strava_app_client_secret
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:4b
```

### 4. Pull the Ollama model

```bash
ollama pull gemma4:4b
```

### 5. Link your Strava account (one-time)

Start the OAuth callback server in one terminal:

```bash
source .venv/bin/activate
uvicorn auth_server:app --port 8000
```

Open this URL in your browser (replace `YOUR_CLIENT_ID`):

```
https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8000/auth/callback&response_type=code&scope=activity:read_all
```

Authorize the app. The page will show:

```
/token <access_token> <refresh_token>
```

Send that exact message to your Telegram bot. Tokens are saved to SQLite — you will never need to do this again.

Stop the auth server after.

### 6. Run the bot

```bash
source .venv/bin/activate
python bot.py
```

The bot registers its command list with Telegram on startup, so `/` autocomplete works immediately.

## Project Structure

```
bot.py            — Telegram handlers, SQLite token store, command routing
strava.py         — StravaClient (async), data formatters, stream analysis
auth_server.py    — One-shot FastAPI OAuth callback (setup only)
ollama_client.py  — OllamaClient (async), LLM integration (coming)
data/tokens.db    — SQLite database (auto-created on first run)
```

## How token refresh works

`StravaClient` handles 401s transparently: it retries the request once after calling the Strava token refresh endpoint. When a refresh happens, the new tokens are immediately written back to SQLite so they persist across restarts.

## Known limitations

- Single-process only — if you run multiple instances they will share the SQLite file but race on writes.
- `auth_server.py` returns raw tokens in an HTML page — treat it as a local dev tool, not a production server.
