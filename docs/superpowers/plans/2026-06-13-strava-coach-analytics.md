# Strava Hybrid-Coach Analytics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Telegram + Ollama bot with Claude-direct training check-ins that pull data from the official Strava MCP, compute weekly stats deterministically in Python (Arm A), and deliver advice through a hybrid strength-and-endurance coach persona — with a bake-off against Claude-computed stats (Arm B).

**Architecture:** Old bot files are retired to `legacy/`. A single pure-Python module `analytics.py` computes weekly aggregates and trends from stored Strava activity snapshots (`data/activities/*.json`). Two markdown artefacts drive Claude's behaviour: `coach.md` (the persona) and `CHECKIN.md` (the operational runbook, including the bake-off). The Strava MCP is already installed and only needs a one-time OAuth.

**Tech Stack:** Python 3.11 (stdlib only for runtime), pytest for tests, the installed `claude.ai Strava` MCP for data. No network/LLM code — fetching and interpretation are Claude actions in-session.

---

## File Structure

- Create: `analytics.py` — pure functions: load + dedupe snapshots, weekly aggregates, trend.
- Create: `tests/test_analytics.py` — unit tests using pytest `tmp_path` fixtures.
- Create: `coach.md` — hybrid-coach persona framing (shared by both bake-off arms).
- Create: `CHECKIN.md` — runbook: OAuth, fetch, store, run a check-in, run the bake-off.
- Create: `legacy/` — holds the retired bot files.
- Modify: `requirements.txt` — drop Telegram/Ollama deps; add pytest.
- Modify: `CLAUDE.md` — rewrite architecture section to match the new system.
- Move: `bot.py`, `ollama_client.py`, `auth_server.py`, `strava.py` → `legacy/`.
- Untouched: `data/tokens.db` (unused), `data/activities/` (new snapshot dir, gitignored via `data/`).

---

### Task 1: Retire the old system

**Files:**
- Move: `bot.py`, `ollama_client.py`, `auth_server.py`, `strava.py` → `legacy/`
- Modify: `requirements.txt`
- Create: `data/activities/.gitkeep`

- [ ] **Step 1: Create the new directories**

```bash
mkdir -p legacy data/activities
touch data/activities/.gitkeep
```

- [ ] **Step 2: Move the retired files with git**

```bash
git mv bot.py ollama_client.py auth_server.py strava.py legacy/
```

- [ ] **Step 3: Verify the move**

Run: `ls legacy/ && ls *.py 2>/dev/null || echo "no stray py files in root"`
Expected: `legacy/` lists the four files; root has no `bot.py`/`strava.py`/etc.

- [ ] **Step 4: Slim requirements.txt**

Replace the entire contents of `requirements.txt` with:

```
# Runtime uses the Python standard library only.
# Dev/test dependency:
pytest>=8.0
```

- [ ] **Step 5: Reinstall deps in the venv**

Run: `source .venv/bin/activate && pip install -r requirements.txt && which python`
Expected: pytest installed; `which python` points inside `.venv/`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: retire Telegram/Ollama bot to legacy/, slim deps"
```

---

### Task 2: `analytics.py` — load and dedupe snapshots

**Files:**
- Create: `analytics.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analytics.py`:

```python
import json
from pathlib import Path

from analytics import load_activities


def _write_snapshot(directory: Path, name: str, activities: list[dict]) -> None:
    (directory / name).write_text(json.dumps(activities), encoding="utf-8")


def test_load_activities_dedupes_by_id(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "2026-06-01.json", [{"id": 1}, {"id": 2}])
    _write_snapshot(tmp_path, "2026-06-08.json", [{"id": 2}, {"id": 3}])

    result = load_activities(str(tmp_path))

    ids = sorted(a["id"] for a in result)
    assert ids == [1, 2, 3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py::test_load_activities_dedupes_by_id -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics'`.

- [ ] **Step 3: Write minimal implementation**

Create `analytics.py`:

```python
from __future__ import annotations

import json
from pathlib import Path


def load_activities(snapshot_dir: str) -> list[dict]:
    """Read every snapshot JSON file in a directory, merge, and dedupe by activity id.

    Each snapshot file is a JSON list of raw Strava activity objects. The first time an
    id is seen wins; later duplicates are ignored.
    """
    activities: dict[int, dict] = {}
    for path in sorted(Path(snapshot_dir).glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for activity in payload:
            activity_id = activity["id"]
            if activity_id not in activities:
                activities[activity_id] = activity
    return list(activities.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py::test_load_activities_dedupes_by_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add snapshot loader with id dedupe"
```

---

### Task 3: `analytics.py` — week key and weekly aggregates

**Files:**
- Modify: `analytics.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analytics.py`:

```python
from analytics import WeekStats, week_key, weekly_aggregates


def test_week_key_maps_iso_week() -> None:
    assert week_key("2026-06-08T07:00:00Z") == "2026-W24"
    assert week_key("2026-06-10T07:00:00Z") == "2026-W24"
    assert week_key("2026-06-01T07:00:00Z") == "2026-W23"


def test_weekly_aggregates_single_week() -> None:
    activities = [
        {
            "id": 1,
            "start_date_local": "2026-06-08T07:00:00Z",
            "distance": 5000,
            "moving_time": 1800,
            "average_heartrate": 150,
        },
        {
            "id": 2,
            "start_date_local": "2026-06-10T07:00:00Z",
            "distance": 10000,
            "moving_time": 3600,
            "average_heartrate": 155,
        },
    ]

    stats = weekly_aggregates(activities)

    week = stats["2026-W24"]
    assert week == WeekStats(
        week="2026-W24",
        activity_count=2,
        total_distance_km=15.0,
        total_moving_time_min=90.0,
        avg_pace_min_per_km=6.0,
        avg_heart_rate=152.5,
    )


def test_weekly_aggregates_skips_missing_hr() -> None:
    activities = [
        {
            "id": 1,
            "start_date_local": "2026-06-08T07:00:00Z",
            "distance": 5000,
            "moving_time": 1800,
        }
    ]

    stats = weekly_aggregates(activities)

    assert stats["2026-W24"].avg_heart_rate is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py -k "week" -v`
Expected: FAIL with `ImportError: cannot import name 'WeekStats'`.

- [ ] **Step 3: Write minimal implementation**

Add to the top imports of `analytics.py`:

```python
from dataclasses import dataclass
from datetime import datetime
```

Append to `analytics.py`:

```python
@dataclass(frozen=True)
class WeekStats:
    week: str
    activity_count: int
    total_distance_km: float
    total_moving_time_min: float
    avg_pace_min_per_km: float
    avg_heart_rate: float | None


def week_key(start_date_local: str) -> str:
    """Map an ISO-8601 start time to its ISO week label, e.g. '2026-W24'."""
    parsed = datetime.fromisoformat(start_date_local.replace("Z", "+00:00"))
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def weekly_aggregates(activities: list[dict]) -> dict[str, WeekStats]:
    """Group activities by ISO week and compute summary stats per week.

    Pace is total moving time divided by total distance (min/km). Average heart rate is
    the mean of per-activity averages, ignoring activities with no HR; None if none have it.
    """
    buckets: dict[str, list[dict]] = {}
    for activity in activities:
        buckets.setdefault(week_key(activity["start_date_local"]), []).append(activity)

    stats: dict[str, WeekStats] = {}
    for week, items in buckets.items():
        total_distance_km = sum(a["distance"] for a in items) / 1000
        total_moving_time_min = sum(a["moving_time"] for a in items) / 60
        avg_pace = total_moving_time_min / total_distance_km if total_distance_km > 0 else 0.0
        hrs = [a["average_heartrate"] for a in items if a.get("average_heartrate") is not None]
        avg_hr = sum(hrs) / len(hrs) if hrs else None
        stats[week] = WeekStats(
            week=week,
            activity_count=len(items),
            total_distance_km=round(total_distance_km, 2),
            total_moving_time_min=round(total_moving_time_min, 1),
            avg_pace_min_per_km=round(avg_pace, 2),
            avg_heart_rate=round(avg_hr, 1) if avg_hr is not None else None,
        )
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py -k "week" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add weekly aggregates and ISO week keying"
```

---

### Task 4: `analytics.py` — trend vs prior weeks

**Files:**
- Modify: `analytics.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analytics.py`:

```python
from analytics import Trend, trend


def _week(week: str, count: int, pace: float) -> WeekStats:
    return WeekStats(
        week=week,
        activity_count=count,
        total_distance_km=10.0,
        total_moving_time_min=pace * 10.0,
        avg_pace_min_per_km=pace,
        avg_heart_rate=150.0,
    )


def test_trend_detects_more_consistent_and_faster() -> None:
    weekly = {
        "2026-W22": _week("2026-W22", count=1, pace=6.5),
        "2026-W23": _week("2026-W23", count=1, pace=6.4),
        "2026-W24": _week("2026-W24", count=3, pace=6.0),
    }

    result = trend(weekly)

    assert result == Trend(
        latest_week="2026-W24",
        count_delta=2,
        pace_change_min_per_km=-0.45,
        is_more_consistent=True,
        is_faster=True,
    )


def test_trend_returns_none_without_prior_week() -> None:
    weekly = {"2026-W24": _week("2026-W24", count=2, pace=6.0)}

    assert trend(weekly) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py -k "trend" -v`
Expected: FAIL with `ImportError: cannot import name 'Trend'`.

- [ ] **Step 3: Write minimal implementation**

Append to `analytics.py`:

```python
@dataclass(frozen=True)
class Trend:
    latest_week: str
    count_delta: int                 # latest count minus rounded prior-weeks average
    pace_change_min_per_km: float    # negative means faster (improving)
    is_more_consistent: bool
    is_faster: bool


def trend(weekly: dict[str, WeekStats], prior_weeks: int = 3) -> Trend | None:
    """Compare the most recent week against the mean of up to `prior_weeks` before it.

    Returns None when there is no prior week to compare against.
    """
    if len(weekly) < 2:
        return None
    ordered = [weekly[key] for key in sorted(weekly.keys())]
    latest = ordered[-1]
    priors = ordered[-(prior_weeks + 1):-1]
    avg_count = sum(w.activity_count for w in priors) / len(priors)
    avg_pace = sum(w.avg_pace_min_per_km for w in priors) / len(priors)
    return Trend(
        latest_week=latest.week,
        count_delta=latest.activity_count - round(avg_count),
        pace_change_min_per_km=round(latest.avg_pace_min_per_km - avg_pace, 2),
        is_more_consistent=latest.activity_count >= avg_count,
        is_faster=latest.avg_pace_min_per_km < avg_pace,
    )
```

- [ ] **Step 4: Run the full test suite**

Run: `source .venv/bin/activate && pytest tests/test_analytics.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Lint and format**

Run: `source .venv/bin/activate && black analytics.py tests/test_analytics.py && ruff check analytics.py tests/test_analytics.py`
Expected: black reformats/clean; ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add week-over-week trend comparison"
```

---

### Task 5: Coach persona artefact

**Files:**
- Create: `coach.md`

- [ ] **Step 1: Write the persona file**

Create `coach.md` with this exact content:

```markdown
# Coach Persona — Hybrid Strength & Endurance

You are a hybrid strength-and-endurance coach in the mould of the hybrid-athlete school
(Alex Viada / Fergus Crawley / Nick Bare): you program both lifting and running for
everyday people, not elite track athletes. The athlete here is casual and wants honest,
simple guidance — not lab data.

## Voice
- Plain language. No jargon dumps, no zone-by-zone tables.
- Direct and encouraging, but honest. If a week was thin, say so.
- British English. Short sentences.

## What every check-in must contain (and nothing more)
1. **Am I doing enough?** — comment on consistency / volume this week vs usual.
2. **Am I improving?** — one line on the trend (pace direction, frequency).
3. **One fix.** — exactly ONE concrete priority for next week. Never a list of fixes.

## Coaching principles
- Consistency beats intensity. Reward showing up.
- Easy days easy. If pace is creeping up on what should be easy mileage, flag it.
- Never invent numbers. Only state figures that come from the supplied stats.
- If heart rate is missing, don't guess — say it isn't available.
```

- [ ] **Step 2: Verify**

Run: `head -5 coach.md`
Expected: shows the persona heading.

- [ ] **Step 3: Commit**

```bash
git add coach.md
git commit -m "feat: add hybrid-coach persona artefact"
```

---

### Task 6: Check-in runbook (including the bake-off)

**Files:**
- Create: `CHECKIN.md`

- [ ] **Step 1: Write the runbook**

Create `CHECKIN.md` with this exact content:

````markdown
# Training Check-in Runbook

How to run a check-in in a Claude Code session. No bot, no Ollama.

## One-time setup: authenticate Strava
1. Call the MCP tool `mcp__claude_ai_Strava__authenticate`.
2. Open the returned authorisation URL, approve access (a free Strava account is fine).
3. Pass the redirected `localhost/callback?...` URL to
   `mcp__claude_ai_Strava__complete_authentication`.
The Strava tools then become available for the session.

## Running a check-in
1. **Fetch** recent activities via the Strava MCP (last ~6 weeks).
2. **Store** them: write the raw JSON list to `data/activities/YYYY-MM-DD.json`
   (today's date). Snapshots accumulate so trends work without re-fetching.
3. **Compute** the numbers:
   ```bash
   source .venv/bin/activate
   python -c "import analytics, json; \
acts = analytics.load_activities('data/activities'); \
weeks = analytics.weekly_aggregates(acts); \
print(json.dumps({k: vars(v) for k, v in sorted(weeks.items())}, indent=2)); \
t = analytics.trend(weeks); print(vars(t) if t else 'no trend yet')"
   ```
4. **Interpret**: load `coach.md` and deliver the check-in in that persona, using ONLY
   the numbers printed in step 3.

## Running the bake-off (one-time decision)
On the same stored snapshot:
- **Arm A:** run step 3 above (Python numbers) → coach check-in.
- **Arm B:** read the raw `data/activities/*.json` directly, compute the weekly numbers
  in-session (no `analytics.py`), → coach check-in.
- **Grade:** Arm A's numbers are ground truth. Check whether Arm B's numbers match them.
  Record, per metric, whether Arm B was exact / close / wrong, and which check-in reads
  better. The winner becomes the default; note it at the bottom of this file.

### Bake-off result
_TBD — fill in after the first real dataset is available._
````

- [ ] **Step 2: Verify**

Run: `head -5 CHECKIN.md`
Expected: shows the runbook heading.

- [ ] **Step 3: Commit**

```bash
git add CHECKIN.md
git commit -m "docs: add check-in runbook and bake-off procedure"
```

---

### Task 7: Update project CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the Architecture and Commands sections**

In `CLAUDE.md`, replace the `## Commands` and `## Architecture` and `## Key Constraints`
sections with content describing the new system:
- Data source: official Strava MCP (OAuth once); no Telegram, no Ollama.
- `analytics.py`: pure functions for weekly aggregates + trend; tested with pytest.
- `coach.md`: persona. `CHECKIN.md`: runbook + bake-off.
- Old bot lives in `legacy/`, retained but unused.
- Commands: `pytest`, `black .`, `ruff check .`. Remove `python bot.py` and the uvicorn
  OAuth command.

Keep the `## Environment` section, but drop the `OLLAMA_*` env vars and the
`TELEGRAM_BOT_TOKEN`/`STRAVA_*` lines that the bot needed (MCP owns auth now).

- [ ] **Step 2: Verify it reads correctly**

Run: `grep -i "ollama\|telegram" CLAUDE.md || echo "clean: no stale bot references"`
Expected: `clean: no stale bot references`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for MCP + analytics architecture"
```

---

### Task 8: Authenticate, fetch real data, run the bake-off (interactive)

> This task is interactive — it needs the user present for OAuth and a real dataset.
> It cannot be unit-tested; it is the integration test described in the spec.

**Files:**
- Modify: `CHECKIN.md` (fill in the bake-off result)
- Writes: `data/activities/<today>.json`

- [ ] **Step 1: Authenticate the Strava MCP**

Follow `CHECKIN.md` → "One-time setup". Confirm Strava tools are available.

- [ ] **Step 2: Fetch and store**

Fetch the last ~6 weeks of activities and save them to
`data/activities/<today>.json` (raw JSON list).

- [ ] **Step 3: Run Arm A**

Run the compute command from `CHECKIN.md` step 3. Capture the weekly stats + trend.

- [ ] **Step 4: Run Arm B**

Without using `analytics.py`, compute the same weekly numbers directly from the JSON,
then check each against Arm A's figures (exact / close / wrong).

- [ ] **Step 5: Deliver both check-ins and decide**

Produce the coach check-in for each arm, present side by side, and pick the winner on
accuracy first, readability second.

- [ ] **Step 6: Record the result and commit**

Fill in the "Bake-off result" section of `CHECKIN.md`. If Arm B wins decisively,
note that `analytics.py` may be retired later (do not delete now).

```bash
git add CHECKIN.md
git commit -m "docs: record bake-off result"
```

---

## Self-Review

**Spec coverage:**
- Strava MCP as data source → Task 8 (auth) + CHECKIN.md (Task 6). ✓
- Split maths from judgement → analytics.py (Tasks 2–4) + coach.md (Task 5). ✓
- Trend vs own past, no goals → Task 4 `trend`. ✓
- Bake-off with Arm A as answer key → CHECKIN.md (Task 6) + Task 8. ✓
- Coach persona (hybrid school) → Task 5. ✓
- Retire old files to legacy/, not deleted → Task 1. ✓
- No DB, no subscription, free → no DB code; data/ gitignored; MCP free. ✓
- Error handling (no auth / no activities / missing HR) → coach.md rules + `avg_heart_rate=None` path (Task 3). ✓
- Testing (unit + bake-off integration) → Tasks 2–4 unit tests; Task 8 integration. ✓

**Placeholder scan:** The only `TBD` is the bake-off result in CHECKIN.md, intentionally filled by Task 8. No placeholder code steps.

**Type consistency:** `WeekStats`, `Trend`, `load_activities`, `week_key`, `weekly_aggregates`, `trend` names are consistent across tasks and tests. `weekly_aggregates` returns `dict[str, WeekStats]`; `trend` consumes that same type.
