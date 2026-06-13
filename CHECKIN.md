# Training Check-in Runbook

How to run a check-in in a Claude Code session. No bot, no Ollama.

Data comes from the **free Strava v3 API** using your own app credentials
(`STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` in `.env`). No paid MCP, no subscription.

## One-time setup: authenticate Strava
Prerequisite: at developers.strava.com → your API application, the
**Authorization Callback Domain** must be `localhost`.

```bash
source .venv/bin/activate
python strava_auth.py                 # prints an authorise URL
# Open it, click Authorize. The browser redirects to a localhost URL that fails to
# load — that is expected. Copy the full URL from the address bar, then:
python strava_auth.py "<paste the redirect URL>"
```
This saves tokens to `data/strava_tokens.json` (gitignored). Token refresh is automatic
on later fetches.

## Running a check-in
1. **Fetch + store** recent activities (writes `data/activities/YYYY-MM-DD.json`;
   snapshots accumulate so trends work without re-fetching):
   ```bash
   source .venv/bin/activate
   python strava_fetch.py             # last 6 weeks (default)
   ```
2. **Compute** the numbers, split by sport so pace is comparable like-with-like:
   ```bash
   source .venv/bin/activate
   python -c "
import analytics
acts = analytics.load_activities('data/activities')
for sport, weeks in analytics.weekly_aggregates_by_sport(acts).items():
    print(f'=== {sport} ===')
    for k, v in sorted(weeks.items()):
        print(f'  {k}: {v.activity_count} act, {v.total_distance_km}km, {v.avg_pace_min_per_km} min/km')
    t = analytics.trend(weeks)
    print('  TREND:', vars(t) if t else 'not enough weeks')
"
   ```
3. **Interpret**: load `coach.md` and deliver the check-in in that persona, using ONLY
   the numbers printed in step 2. Read pace per sport — never compare a run week against
   a walk week.

## Running the bake-off (one-time decision)
On the same stored snapshot:
- **Arm A:** run the compute step above (Python numbers) → coach check-in.
- **Arm B:** read the raw `data/activities/*.json` directly, compute the weekly numbers
  in-session (no `analytics.py`), → coach check-in.
- **Grade:** Arm A's numbers are ground truth. Check whether Arm B's numbers match them.
  Record, per metric, whether Arm B was exact / close / wrong, and which check-in reads
  better. The winner becomes the default; note it at the bottom of this file.

### Bake-off result (2026-06-14, 7 activities / 6 weeks)
**Winner: Arm A (Python `analytics.py`) — default.**
Arm A and Arm B produced identical numbers on all 6 weeks and the trend (Arm B matched
ground truth exactly). Tie on accuracy *for this small set*, but Arm A is deterministically
correct for zero effort and does not degrade as data grows, whereas Arm B's accuracy
depends on careful manual computation each time. Use `analytics.py`.

**Resolved (2026-06-14):** the first bake-off exposed that mixing runs + walks into one
pace figure made a run week look `is_faster: True, pace_change -2.52` against walk weeks —
an artefact. Fixed by `weekly_aggregates_by_sport` (used in the compute step above), which
keeps each sport separate. Runs-only trend then read correctly (`pace_change +0.57,
is_faster: False`). Always interpret pace per sport.
