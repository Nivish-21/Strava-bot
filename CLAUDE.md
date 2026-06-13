# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Python venv: `.venv/` in project root (Python 3.11.13). Activate with `source .venv/bin/activate` before running anything. Confirm with `which python`.

No `.env` is required for normal operation — Strava access is handled by the official
Strava MCP via OAuth (see `CHECKIN.md`). The legacy Telegram/Ollama bot's env vars
(`TELEGRAM_BOT_TOKEN`, `STRAVA_CLIENT_ID`, `STRAVA_SECRET`, `OLLAMA_*`) are only relevant
to the retired code in `legacy/`.

## Commands

```bash
# Run the test suite
pytest -v

# Compute weekly stats from stored snapshots (see CHECKIN.md for the full flow)
python -c "import analytics, json; print(json.dumps({k: vars(v) for k, v in sorted(analytics.weekly_aggregates(analytics.load_activities('data/activities')).items())}, indent=2))"

# Install deps
pip install -r requirements.txt

# Lint / format
black .
ruff check .
```

## Architecture

Claude-direct training check-ins. There is no bot and no local LLM — you run a check-in
inside a Claude Code session. Three pieces:

**Strava MCP (external, already installed)**
The official `claude.ai Strava` MCP (`https://mcp.strava.com/mcp`) is the data source.
Authenticate once via OAuth (`mcp__claude_ai_Strava__authenticate` →
`mcp__claude_ai_Strava__complete_authentication`); a free Strava account is sufficient.
Claude fetches activities directly through it.

**`analytics.py` — deterministic maths**
Pure functions, standard library only, fully unit-tested (`tests/test_analytics.py`):
- `load_activities(dir)` — read and dedupe stored snapshot JSON by activity id.
- `week_key(start)` / `weekly_aggregates(activities)` — per-ISO-week count, distance,
  moving time, average pace, average heart rate (`WeekStats`).
- `trend(weekly)` — compare the latest week against the mean of prior weeks (`Trend`).
The numbers come from here so they are never hallucinated.

**`coach.md` — persona; `CHECKIN.md` — runbook**
`coach.md` frames every check-in as a hybrid strength-and-endurance coach (one priority
fix, plain language). `CHECKIN.md` is the operational runbook: authenticate, fetch,
store snapshots to `data/activities/`, compute, interpret. It also defines the bake-off
(Python-computed numbers vs Claude-computed numbers) used to decide the default path.

## Key Constraints

- The split between maths (Python, deterministic) and judgement (Claude, coach persona)
  is the core reliability fix — keep computed figures out of free-text generation.
- The old Telegram + Ollama system lives in `legacy/` (retained, unused). `data/tokens.db`
  is leftover from it and is no longer used.
- Snapshots in `data/activities/` accumulate so trends work across weeks without
  re-fetching history; `data/` is gitignored.
