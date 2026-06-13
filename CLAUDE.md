# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Python venv: `.venv/` in project root (Python 3.11.13). Activate with `source .venv/bin/activate` before running anything. Confirm with `which python`.

`.env` must contain `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` from your free Strava
API application (developers.strava.com). These drive the direct-API OAuth in
`strava_auth.py` / `strava_fetch.py` (see `CHECKIN.md`). The legacy bot's other env vars
(`TELEGRAM_BOT_TOKEN`, `OLLAMA_*`) are only relevant to the retired code in `legacy/`.

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

**`strava_api.py` + CLIs — free direct Strava API**
The data source is the free Strava v3 API (no paid MCP, no subscription). `strava_api.py`
is stdlib-only: OAuth helpers, automatic token refresh, activity fetch, snapshot writing,
with unit-tested pure helpers (`tests/test_strava_api.py`). `strava_auth.py` does the
one-time browser OAuth (saving tokens to `data/strava_tokens.json`); `strava_fetch.py`
pulls recent activities into `data/activities/`. Requires `STRAVA_CLIENT_ID`/`SECRET`.

**`analytics.py` — deterministic maths**
Pure functions, standard library only, fully unit-tested (`tests/test_analytics.py`):
- `load_activities(dir)` — read and dedupe stored snapshot JSON by activity id.
- `week_key(start)` / `weekly_aggregates(activities)` — per-ISO-week count, distance,
  moving time, average pace, average heart rate (`WeekStats`).
- `weekly_aggregates_by_sport(activities)` — same, split per `sport_type` (runs vs walks)
  so pace stays comparable like-with-like. Use this for check-ins, not the mixed version.
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
