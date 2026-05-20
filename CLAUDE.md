# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Python venv: `.venv/` in project root (Python 3.11.13). Activate with `source .venv/bin/activate` before running anything. Confirm with `which python`.

Required env vars (`.env`):
```
TELEGRAM_BOT_TOKEN=
STRAVA_CLIENT_ID=
STRAVA_SECRET=
OLLAMA_URL=http://localhost:11434   # default
OLLAMA_MODEL=gemma4:4b              # default
```

Ollama must be running locally with the target model pulled (`ollama pull gemma4:4b`).

## Commands

```bash
# Run the bot
python bot.py

# One-time OAuth setup (run separately, not needed once tokens are stored)
uvicorn auth_server:app --port 8000

# Install deps
pip install -r requirements.txt

# Lint / format
black .
ruff check .
```

No test suite exists yet.

## Architecture

Single-process async Telegram bot. Three components wired together in `bot.py`:

**`strava.py` — `StravaClient`**  
Async context manager wrapping the Strava v3 REST API. Handles token refresh transparently on 401: `get_activities()` retries itself once after calling `refresh()`. Tokens are passed in at construction; the client does not persist them — the caller (`bot.py`) owns state.

**`ollama_client.py` — `OllamaClient`**  
Async context manager for the Ollama `/api/generate` endpoint. Non-streaming. Model and base URL are constructor params, overridable via env.

**`bot.py` — Telegram bot**  
`user_tokens` is an in-memory `dict` keyed by Telegram user ID — **tokens are lost on restart**. `structure.md` notes SQLite (`data/tokens.db`) as the intended persistence layer but it is not implemented. All handlers follow the same pattern: check `user_tokens`, open `StravaClient`, format activity data, open `OllamaClient`, send LLM output back.

**`auth_server.py`**  
One-shot FastAPI server used only during initial Strava OAuth setup. Not part of normal operation. Exchanges the OAuth code for tokens and displays them for the user to paste into Telegram via `/token`.

## Key Constraints

- `user_tokens` is process-local; any multi-instance deployment will lose tokens. Migrate to SQLite before scaling.
- `StravaClient.get_activities()` has unbounded recursion on repeated 401s — add a retry limit before exposing to untrusted tokens.
- `auth_server.py` returns raw access/refresh tokens in an HTML response. Treat as a dev tool only.
