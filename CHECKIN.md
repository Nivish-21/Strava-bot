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
2. **Compute** the numbers:
   ```bash
   source .venv/bin/activate
   python -c "import analytics, json; \
acts = analytics.load_activities('data/activities'); \
weeks = analytics.weekly_aggregates(acts); \
print(json.dumps({k: vars(v) for k, v in sorted(weeks.items())}, indent=2)); \
t = analytics.trend(weeks); print(vars(t) if t else 'no trend yet')"
   ```
3. **Interpret**: load `coach.md` and deliver the check-in in that persona, using ONLY
   the numbers printed in step 2.

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

**Known limitation exposed by real data:** `weekly_aggregates`/`trend` mix all sport types
(runs + walks) into one pace figure. With walks in the baseline, the trend reported
`is_faster: True, pace_change -2.52` — an artefact, not real running improvement. Pace
should be computed per sport_type (runs vs walks separately) before it is trustworthy.
Coach interpretation must, for now, lean on per-run figures and ignore the mixed pace trend.
