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
