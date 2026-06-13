# Strava → Hybrid-Coach Training Check-ins

**Date:** 2026-06-13
**Status:** Approved design — pending implementation plan
**Owner:** Nivish

## Problem

The existing system (Telegram bot + local `gemma4:4b` via Ollama) produces unreliable
analytics. Root cause: a 4-billion-parameter local model is doing **both** the arithmetic
over activity data **and** the coaching judgement. Small models cannot reliably aggregate
numbers (pace, distance, heart rate, week-over-week trends), so the figures come out wrong
and the advice built on them is worthless.

The user wants simple, trustworthy feedback — not lab-grade analytics. Specifically:
- Am I doing enough? (consistency / volume)
- Am I improving? (trend vs my own past)
- What is the one thing to fix next?

The user is a casual athlete, not training for a marathon or triathlon. No detailed
line-by-line breakdowns wanted.

## Goals

- Trustworthy numbers — no hallucinated figures.
- Short, plain-language check-ins delivered through a hybrid strength-and-endurance
  coach persona.
- Progress measured against the user's **own past weeks** (trend-based), no goal-setting.
- Free: no Strava subscription, no API costs, no background services.

## Non-Goals

- No Telegram bot.
- No local LLM (Ollama).
- No goal/target tracking (race plans, target paces).
- No database. No multi-user support.
- No detailed per-split or per-segment analysis.

## Key Decision: where the analysis lives

Interaction happens **inside Claude Code**. The user opens a session when they want a
check-in. There is no bot, no scheduled job, nothing running in the background.

Data comes from the **official Strava MCP** (`https://mcp.strava.com/mcp`), which is
already installed in the environment. It authenticates via OAuth against a normal (free)
Strava account — no subscription required. Claude calls the MCP tools directly to fetch
activities.

## Architecture

```
Strava MCP (installed, free, OAuth once)
        │
        ▼
  raw activities  ──►  data/activities/YYYY-MM-DD.json   (stored snapshots)
        │
        ▼
  [maths]          weekly numbers: count, distance, moving time,
                   avg pace, avg HR, trend vs prior weeks
        │
        ▼
  Coach persona (Claude)  ──►  short plain-language check-in,
                               one priority fix
```

**Core principle: separate the maths from the judgement.** Whoever computes the numbers
must be deterministic and correct; the coaching interpretation sits on top of trusted
figures. This is the single fix for the old system's unreliability.

## The Bake-off

We do not assume which "maths" path is best. We build two arms, run both on the same
stored week, and pick the winner empirically.

| | **Arm A — Python maths** | **Arm B — Claude maths** |
|---|---|---|
| Numbers computed by | `analytics.py` (deterministic Python) | Claude, from raw activity JSON in-session |
| Interpretation by | Coach persona (Claude) | Coach persona (Claude) |
| Role in the test | also serves as the **answer key** | graded against Arm A's numbers |

Because Arm A's figures are deterministic ground truth, the same run that compares the
two check-ins also reveals whether Arm B's in-session arithmetic matched reality. The
winner is therefore decided on **accuracy first, readability second**, not taste.

**Outcome:**
- If Arm A wins → end state is Strava MCP + `analytics.py` + coach persona.
- If Arm B wins → end state is Strava MCP + coach persona, no maths module (simpler).

## Components

### Strava MCP (external, already installed)
- Auth: OAuth once via `mcp__claude_ai_Strava__authenticate` →
  `mcp__claude_ai_Strava__complete_authentication`.
- Provides activity list and per-activity detail. No code to write.

### Snapshot storage
- Each check-in fetches recent activities and writes `data/activities/YYYY-MM-DD.json`.
- Plain, human-readable JSON. Inspectable by the user.
- Snapshots accumulate so trend-over-weeks works without re-fetching history.

### `analytics.py` (Arm A only)
- Pure, tested functions. No network, no LLM, no global state.
- Responsibilities:
  - `load_activities(dir)` — read stored snapshots.
  - `weekly_aggregates(activities)` — per-week count, total distance, total moving
    time, average pace, average heart rate.
  - `trend(weeks)` — compare the latest week against prior weeks (consistency and
    pace direction).
- ~100 lines. Likely standard library only.

### Coach persona (both arms)
- Fixed framing applied to every check-in: hybrid strength-and-endurance coach
  (Alex Viada / Fergus Crawley / Nick Bare school — programs both lifting and running
  for everyday athletes, not a track-only running coach).
- Behaviour: prioritise consistency over intensity; enforce easy days easy; give exactly
  **one** priority fix per check-in; plain language, no jargon dump.

## Data Flow (per check-in)

1. Claude fetches recent activities via the Strava MCP.
2. Snapshot saved to `data/activities/YYYY-MM-DD.json`.
3. Numbers computed — Arm A: `analytics.py`; Arm B: Claude in-session.
4. Coach persona reads the numbers and writes the check-in (consistency, trend, one fix).

## Error Handling

- Strava MCP not authenticated → prompt the user through the OAuth flow; do not fabricate
  data.
- No activities in the period → say so plainly; do not invent a check-in.
- Malformed / missing fields in activity JSON (e.g. no HR) → skip that metric explicitly,
  never guess a value.

## Testing

- `analytics.py` functions are unit-tested against small fixture datasets with known
  expected aggregates (Arrange-Act-Assert, named by behaviour).
- The bake-off itself is the integration test: Arm A's deterministic output is the
  ground truth that validates Arm B.

## Retiring the old system

Moved to `legacy/` (not deleted — kept until proven unnecessary):
- `bot.py` — Telegram bot
- `ollama_client.py` — local LLM wrapper (the unreliable brain)
- `auth_server.py` — old OAuth callback server
- `strava.py` — REST client (Strava MCP replaces it)

`data/tokens.db` stays in place, unused. `requirements.txt` shrinks to whatever
`analytics.py` needs (likely nothing beyond the standard library).

## Open Questions

None. Ready for implementation planning.

## Correction (2026-06-14): data source is the direct Strava API, not the MCP

The original design assumed the `claude.ai Strava` MCP connector was free. It is not —
it is paywalled behind a subscription the user cannot pay. The free path is the **direct
Strava v3 API** using the user's own registered app credentials (`STRAVA_CLIENT_ID` /
`STRAVA_CLIENT_SECRET`), exactly as the retired `legacy/strava.py` did. Implemented as
stdlib-only `strava_api.py` (+ `strava_auth.py`, `strava_fetch.py`). Everything else in
this spec — the maths/judgement split, `analytics.py`, the coach persona, the bake-off —
is unchanged. See `docs/lessons.md` for the root cause.
