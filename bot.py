import hashlib
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from ollama_client import OllamaClient
from strava import StravaClient, StravaError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
STRAVA_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "tokens.db")

# ── Cache ─────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, str]] = {}  # key -> (expiry_ts, value)


def _cache_get(key: str) -> str | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    _cache.pop(key, None)
    return None


def _cache_set(key: str, value: str, ttl: int) -> None:
    _cache[key] = (time.time() + ttl, value)


def _coach_cache_key(run_text: str) -> str:
    return "coach:" + hashlib.sha256(run_text.encode()).hexdigest()[:20]


# ── Token store ───────────────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS tokens "
        "(user_id INTEGER PRIMARY KEY, access TEXT NOT NULL, refresh TEXT NOT NULL)"
    )
    con.commit()
    return con


def save_tokens(user_id: int, access: str, refresh: str) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO tokens(user_id, access, refresh) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET access=excluded.access, refresh=excluded.refresh",
            (user_id, access, refresh),
        )


def load_tokens(user_id: int) -> dict | None:
    with _db() as con:
        row = con.execute(
            "SELECT access, refresh FROM tokens WHERE user_id=?", (user_id,)
        ).fetchone()
    return {"access": row[0], "refresh": row[1]} if row else None


# ── Strava client context manager with auto token persistence ─────────────────


@asynccontextmanager
async def _strava(user_id: int, tokens: dict):
    async def _on_refresh(access: str, refresh: str) -> None:
        save_tokens(user_id, access, refresh)
        log.info("Refreshed and persisted tokens for user %d", user_id)

    async with StravaClient(
        tokens["access"],
        tokens["refresh"],
        STRAVA_ID,
        STRAVA_SECRET,
        on_refresh=_on_refresh,
    ) as client:
        yield client


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _send_long(message, text: str, parse_mode: str = "Markdown") -> None:
    """Split messages that exceed Telegram's 4096-char limit."""
    limit = 4000
    if len(text) <= limit:
        await message.reply_text(text, parse_mode=parse_mode)
        return
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > limit:
            await message.reply_text(chunk, parse_mode=parse_mode)
            chunk = line
        else:
            chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        await message.reply_text(chunk, parse_mode=parse_mode)


def _require_tokens(user_id: int) -> dict | None:
    return load_tokens(user_id)


_COACH_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. Analyze the run data below.

If heart rate data is present and non-zero, use it. If absent or N/A, analyze using pace only — do not mention heart rate.

Write exactly 5 short paragraphs in this order. No headers, no bullet points, no labels — just five paragraphs of plain text:

Paragraph 1 — ZONE: State the training zone (Recovery / Easy / Aerobic / Tempo / Threshold / VO2max). Justify it using pace and split data. Be specific about what the pace indicates physiologically.

Paragraph 2 — PACING: How well was the run paced? Compare the first third vs the last third. Did they fade (positive split), get faster (negative split), or hold even? If km splits are shown, cite the exact numbers.

Paragraph 3 — INSIGHT: One specific observation from the numbers — pace fade rate per km, consistency of effort, cadence effect on pace, or sustainable effort window.

Paragraph 4 — NEXT RUN: One concrete directive with exact numbers. Format: "Run Xkm at Y:YY–Z:ZZ/km — [one specific instruction]." Never say "listen to your body" or "keep it up."

Paragraph 5 — MISTAKE WATCH: The data block ends with a line beginning "VERDICT:". If that verdict flags a pacing error, restate the error in one sentence and give the exact fix using the numbers in the verdict. If the verdict says no errors, write a single short sentence: "No mistake detected on this run."

Hard constraints: under 250 words total. Plain text only — no markdown, no asterisks, no headers. Address the runner as "you." Never open with praise.\
"""


_COACH_MULTI_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. The athlete wants to improve pace and run longer distances. No heart rate monitor — analyze pace only. Do not mention heart rate.

All numbers are pre-computed in the log. Copy them exactly — do not recalculate, do not invent distances, do not leave placeholders.

Write exactly 7 sections in this order. One blank line between sections. No markdown, no asterisks, no bullet symbols. Address the athlete as "you."

TRAINING ZONES
Copy all 5 lines from the ZONE TABLE block verbatim. Nothing else.

ZONE BREAKDOWN
One line per run from the RUNS section. Format: "DATE  DISTkm  PACE  ZONE". Copy values exactly. No paraphrasing, no duration text.

WHAT YOUR DATA SHOWS
Three sentences only.
Sentence 1: Read the ZONE DISTRIBUTION block. State which zone is dominant and its exact percentage.
Sentence 2: Read the "Missing:" line. State which zones are missing and what type of training that represents.
Sentence 3: One direct consequence of this pattern for pace improvement and distance goals.

VO2MAX ESTIMATE
Copy the VO2max estimate from the log header exactly. One additional sentence placing it in a tier (under 35 beginner, 35-45 recreational, 45-55 trained, above 55 elite).

NEXT RUN
One sentence. If the dominant zone from ZONE DISTRIBUTION is Zone 3, 4, or 5: recommend an easy Zone 2 run at the Day 1 distance and pace from RECOMMENDED WEEKLY STRUCTURE. If the dominant zone is Zone 1 or 2: recommend a Zone 4 tempo run at the Day 2 distance and pace. Use exact numbers.

WEEKLY STRUCTURE
Copy the 3 lines from RECOMMENDED WEEKLY STRUCTURE exactly, keeping Day 1/2/3 format and all distances and paces unchanged. Then one sentence explaining how 3 weeks of this structure will move the athlete toward faster pace and longer distance.

MISTAKE WATCH
The log ends with "VERDICT:". If errors are listed, one line per error restating the run and the fix with exact numbers from the verdict. If verdict says no errors, write: "No pacing errors detected across these runs."

Under 450 words total.\
"""


_TRENDS_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. The data block below summarises the athlete's last 10 runs with per-run zone labels and a first-5 vs last-5 average pace comparison. The block ends with a line beginning "VERDICT:".

Write exactly 4 sections in the order shown below. One blank line between sections. Plain text only, no markdown, no asterisks, no bullet symbols. Address the athlete as "you." Under 300 words.

PACE TREND
One short paragraph. Are you getting faster, slower, or holding flat? Cite the first-5-avg and last-5-avg pace numbers from the data block.

ZONE BALANCE
One short paragraph naming which zones dominate the last 10 runs and which are missing.

IMPROVEMENT
One short paragraph with specific numbers comparing first 5 vs last 5 (pace delta, distance trend, consistency).

FOCUS
One single sentence with one concrete directive for the next 2 weeks (a specific run type, pace range, or volume change). If the VERDICT line flags an error, name it inside this sentence and give the fix.\
"""


_WEEK_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. The data block below summarises this week's runs, including which run types (easy / interval / long) are present or missing. The block ends with a line beginning "VERDICT:".

Write exactly 3 sections in the order shown below. One blank line between sections. Plain text only, no markdown, no asterisks, no bullet symbols. Address the athlete as "you." Under 300 words.

WEEK LOAD
One short paragraph stating total km this week and the zone balance (which zones dominate).

STRUCTURE CHECK
One short paragraph: did you hit easy + interval + long this week? Name what is missing or doubled up. If structure is balanced, say so directly.

MISTAKE WATCH
If the VERDICT line lists pacing errors, output one line per flagged run restating the error and the exact fix (use the numbers from the verdict). If the verdict says "No pacing errors detected.", output exactly: "No mistakes."\
"""


_MONTHLY_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. The data block below shows side-by-side stats for the current month vs the prior month. The block ends with a line beginning "VERDICT:" describing pacing issues in the current month only.

Write exactly 3 sections in the order shown below. One blank line between sections. Plain text only, no markdown, no asterisks, no bullet symbols. Address the athlete as "you." Under 350 words.

MONTHLY PROGRESS
One short paragraph stating what went up and what went down between the two months. Cite exact numbers for run count, total km, and avg pace.

WHAT IMPROVED
One short paragraph naming the single biggest improvement (pace, distance, or consistency) with exact numbers. If nothing improved, say so plainly and name the regression.

NEXT MONTH TARGET
One single sentence with one concrete numeric target derived from the stats (e.g. "Hit 120km at sub-5:30/km avg pace next month."). If the VERDICT line flags an error, name the fix inside this sentence.\
"""


_OVERALL_SYSTEM = """\
You are Coach Alex, an RRCA-certified running coach. The data block below shows the athlete's all-time, year-to-date, and last-4-weeks totals.

Write exactly 3 sections in the order shown below. One blank line between sections. Plain text only, no markdown, no asterisks, no bullet symbols. Address the athlete as "you." Under 300 words.

WHERE YOU STAND
One short paragraph placing the athlete in a tier — beginner (under 500km lifetime), recreational (500-2000km), trained (2000-5000km), or elite (over 5000km) — and refine using the last-4-weeks volume and pace. Cite the actual numbers.

BIGGEST LEVER
One short paragraph naming the single thing that will move the needle most: volume, pace work, or consistency. Justify from the totals (e.g. low YTD km → volume; low 4-week vs YTD ratio → consistency).

3-MONTH GOAL
One single sentence stating one target with exact numbers derived from the stats (e.g. "Reach 400km in the next 3 months at sub-5:45/km long-run pace.").\
"""


def _sec_to_pace(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}/km"


def _estimate_vo2max(fastest_pace_sec: float) -> str:
    """Daniels formula assuming fastest training pace ≈ threshold effort (85–88% VO2max)."""
    if fastest_pace_sec <= 0:
        return "N/A"
    v = (1000.0 / fastest_pace_sec) * 60  # m/min
    vo2_at_pace = 0.000104 * v**2 + 0.182258 * v - 4.60
    lo = round(vo2_at_pace / 0.88)
    hi = round(vo2_at_pace / 0.82)
    return f"~{lo}–{hi} ml/kg/min (assumes fastest training pace = threshold effort)"


def _zone_label(pace_sec: float, anchor: float) -> str:
    diff = pace_sec - anchor
    if diff < 15:
        return "Zone 5 (VO2max)"
    if diff < 45:
        return "Zone 4 (Threshold)"
    if diff < 90:
        return "Zone 3 (Tempo)"
    if diff < 150:
        return "Zone 2 (Aerobic)"
    return "Zone 1 (Recovery)"


def _compute_anchor(runs: list[dict]) -> float:
    """Anchor = fastest pace (sec/km) across the run set; 0.0 if none."""
    fastest = float("inf")
    for r in runs:
        dist = r.get("distance", 0) / 1000
        t_sec = r.get("moving_time", 0)
        pace_sec = (t_sec / dist) if dist > 0 else 0.0
        if 0 < pace_sec < fastest:
            fastest = pace_sec
    return fastest if fastest != float("inf") else 0.0


def _infer_run_type(dist_km: float, pace_sec: float, anchor_sec: float) -> str:
    if anchor_sec <= 0 or pace_sec <= 0:
        return "ok"
    if dist_km <= 7 and pace_sec < anchor_sec + 30:
        return "interval"
    if dist_km >= 12 and pace_sec < anchor_sec + 90:
        return "long_run_too_fast"
    return "ok"


def _mistake_watch(runs: list[dict], anchor: float) -> str:
    """Pre-computed verdict string. Flags only genuine pacing errors (interval
    classification is informational, not a mistake)."""
    if anchor <= 0:
        return "No pacing errors detected."
    flagged: list[str] = []
    for r in runs:
        dist = r.get("distance", 0) / 1000
        t_sec = r.get("moving_time", 0)
        pace_sec = (t_sec / dist) if dist > 0 else 0.0
        rtype = _infer_run_type(dist, pace_sec, anchor)
        if rtype != "long_run_too_fast":
            continue
        date = r.get("start_date_local", "")[:10]
        fix = anchor + 165  # solid Zone 1-2 floor
        flagged.append(
            f"{date} long run: {dist:.1f}km at {_sec_to_pace(pace_sec)} "
            f"is too fast for a long run. Fix: slow to >= {_sec_to_pace(fix)} (Zone 1-2)."
        )
    if not flagged:
        return "No pacing errors detected."
    return "\n".join(flagged)


def _format_training_log(runs: list[dict]) -> str:
    total_dist = 0.0
    fastest_pace_sec: float = float("inf")

    # First pass: collect pace data and find anchor
    entries: list[tuple[dict, float, int, float]] = []
    for r in runs:
        dist = r.get("distance", 0) / 1000
        t_sec = r.get("moving_time", 0)
        pace_sec = (t_sec / dist) if dist > 0 else 0.0
        if 0 < pace_sec < fastest_pace_sec:
            fastest_pace_sec = pace_sec
        total_dist += dist
        entries.append((r, dist, t_sec, pace_sec))

    anchor = fastest_pace_sec if fastest_pace_sec != float("inf") else 0.0

    # Pre-compute zone table
    zone_table = (
        f"  Zone 5 (VO2max):    {_sec_to_pace(anchor)} – {_sec_to_pace(anchor + 15)}\n"
        f"  Zone 4 (Threshold): {_sec_to_pace(anchor + 15)} – {_sec_to_pace(anchor + 45)}\n"
        f"  Zone 3 (Tempo):     {_sec_to_pace(anchor + 45)} – {_sec_to_pace(anchor + 90)}\n"
        f"  Zone 2 (Aerobic):   {_sec_to_pace(anchor + 90)} – {_sec_to_pace(anchor + 150)}\n"
        f"  Zone 1 (Recovery):  slower than {_sec_to_pace(anchor + 150)}"
    )

    # Second pass: build rows with pre-assigned zones + zone distribution counts
    rows = []
    zone_counts: dict[str, int] = {
        "Zone 5": 0,
        "Zone 4": 0,
        "Zone 3": 0,
        "Zone 2": 0,
        "Zone 1": 0,
    }
    for r, dist, t_sec, pace_sec in entries:
        pace_str = _sec_to_pace(pace_sec) if pace_sec > 0 else "N/A"
        zone = _zone_label(pace_sec, anchor) if pace_sec > 0 and anchor > 0 else "N/A"
        if zone != "N/A":
            key = zone.split(" (")[0]
            if key in zone_counts:
                zone_counts[key] += 1
        date = r.get("start_date_local", "")[:10]
        mins_total, secs = divmod(t_sec, 60)
        hrs, mins = divmod(mins_total, 60)
        t_fmt = f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"
        elev = r.get("total_elevation_gain", 0)
        elev_str = f"  +{elev:.0f}m" if elev else ""
        rows.append(f"{date}  {dist:5.1f}km  {pace_str}  {zone}  {t_fmt}{elev_str}")

    total_labeled = sum(zone_counts.values())
    dominant = max(zone_counts, key=zone_counts.__getitem__) if total_labeled else "N/A"
    missing_zones = [z for z, c in zone_counts.items() if c == 0]

    zone_dist_lines = "  " + "  |  ".join(f"{z}: {c}" for z, c in zone_counts.items())
    dom_pct = (
        zone_counts.get(dominant, 0) * 100 // total_labeled if total_labeled else 0
    )
    dom_line = f"  Dominant: {dominant} ({dom_pct}% of runs)"
    missing_line = f"  Missing: {', '.join(missing_zones) if missing_zones else 'none'}"

    max_long_km = max((e[1] for e in entries), default=0.0)
    long_target = max(round(max_long_km + 2), 10)
    easy_target = max(round(max_long_km * 0.6), 5)
    rec_block = (
        f"  Day 1 (easy):      {easy_target}km at {_sec_to_pace(anchor + 90)}–{_sec_to_pace(anchor + 150)}\n"
        f"  Day 2 (intervals): 5x1km at {_sec_to_pace(anchor + 15)}–{_sec_to_pace(anchor + 45)}, 90s rest\n"
        f"  Day 3 (long):      {long_target}km at {_sec_to_pace(anchor + 150)}–{_sec_to_pace(anchor + 210)}"
    )

    header = (
        f"ATHLETE TRAINING LOG — {len(runs)} most recent runs\n"
        f"Total distance: {total_dist:.1f} km\n"
        f"Anchor pace (fastest): {_sec_to_pace(anchor) if anchor > 0 else 'unknown'}\n"
        f"VO2max estimate: {_estimate_vo2max(anchor)}\n\n"
        f"ZONE TABLE (pre-calculated — copy exactly, do not modify):\n"
        f"{zone_table}\n\n"
        f"ZONE DISTRIBUTION (pre-counted — use these numbers, do not recount):\n"
        f"{zone_dist_lines}\n"
        f"{dom_line}\n"
        f"{missing_line}\n\n"
        f"RECOMMENDED WEEKLY STRUCTURE (pre-computed — copy distances and paces exactly):\n"
        f"{rec_block}\n\n"
        f"RUNS (zone pre-assigned — copy exactly, do not reassign):\n"
        f"Date        Dist     Pace       Zone                 Duration\n"
    )
    return header + "\n".join(rows)


def _format_trends_log(runs: list[dict]) -> str:
    """Last-N runs trend log with anchor, zone labels, first-half vs last-half pace, verdict."""
    if not runs:
        return "No runs.\n\nVERDICT: No pacing errors detected."
    runs_sorted = sorted(runs, key=lambda r: r.get("start_date_local", ""))
    anchor = _compute_anchor(runs_sorted)
    lines: list[str] = []
    paces: list[float] = []
    total_dist = 0.0
    for r in runs_sorted:
        dist = r.get("distance", 0) / 1000
        t_sec = r.get("moving_time", 0)
        pace_sec = (t_sec / dist) if dist > 0 else 0.0
        paces.append(pace_sec)
        total_dist += dist
        date = r.get("start_date_local", "")[:10]
        zone = _zone_label(pace_sec, anchor) if pace_sec > 0 and anchor > 0 else "N/A"
        pace_str = _sec_to_pace(pace_sec) if pace_sec > 0 else "N/A"
        lines.append(f"  {date}  {dist:5.1f}km  {pace_str}  {zone}")

    valid_paces = [p for p in paces if p > 0]
    if len(valid_paces) >= 6:
        half = len(valid_paces) // 2
        first_half = valid_paces[:half]
        last_half = valid_paces[-half:]
        first_avg = sum(first_half) / len(first_half)
        last_avg = sum(last_half) / len(last_half)
        compare = (
            f"First {len(first_half)} avg pace: {_sec_to_pace(first_avg)}  |  "
            f"Last {len(last_half)} avg pace: {_sec_to_pace(last_avg)}  |  "
            f"Delta: {last_avg - first_avg:+.0f}s/km (negative = faster)"
        )
    else:
        compare = "Insufficient runs for first-half vs last-half comparison."

    verdict = _mistake_watch(runs_sorted, anchor)
    header = (
        f"LAST {len(runs_sorted)} RUNS (oldest first)\n"
        f"Anchor pace (fastest): {_sec_to_pace(anchor) if anchor > 0 else 'unknown'}\n"
        f"Total distance: {total_dist:.1f} km\n"
        f"{compare}\n\n"
    )
    return header + "\n".join(lines) + f"\n\nVERDICT: {verdict}"


def _format_week_log(runs: list[dict]) -> str:
    if not runs:
        return "No runs this week.\n\nVERDICT: No pacing errors detected."
    anchor = _compute_anchor(runs)
    types_seen: dict[str, bool] = {"easy": False, "interval": False, "long": False}
    lines: list[str] = []
    total_dist = 0.0
    for r in sorted(runs, key=lambda a: a.get("start_date_local", "")):
        dist = r.get("distance", 0) / 1000
        t_sec = r.get("moving_time", 0)
        pace_sec = (t_sec / dist) if dist > 0 else 0.0
        total_dist += dist
        zone = _zone_label(pace_sec, anchor) if pace_sec > 0 and anchor > 0 else "N/A"
        pace_str = _sec_to_pace(pace_sec) if pace_sec > 0 else "N/A"
        date = r.get("start_date_local", "")[:10]
        rtype = _infer_run_type(dist, pace_sec, anchor)
        if dist >= 12:
            run_kind = "long"
            types_seen["long"] = True
        elif rtype == "interval":
            run_kind = "interval"
            types_seen["interval"] = True
        else:
            run_kind = "easy"
            types_seen["easy"] = True
        lines.append(f"  {date}  {dist:5.1f}km  {pace_str}  {zone}  ({run_kind})")

    present = [k for k, v in types_seen.items() if v]
    missing = [k for k, v in types_seen.items() if not v]
    structure = (
        f"Structure present: {', '.join(present) if present else 'none'}  |  "
        f"Missing: {', '.join(missing) if missing else 'none'}"
    )
    verdict = _mistake_watch(runs, anchor)
    header = (
        f"THIS WEEK — {len(runs)} runs\n"
        f"Total distance: {total_dist:.1f} km\n"
        f"Anchor pace (fastest): {_sec_to_pace(anchor) if anchor > 0 else 'unknown'}\n"
        f"{structure}\n\n"
    )
    return header + "\n".join(lines) + f"\n\nVERDICT: {verdict}"


def _format_monthly_log(runs: list[dict], current_month: int, current_year: int) -> str:
    """Splits runs into current vs prior month, builds side-by-side stats, appends verdict."""
    cur_runs: list[dict] = []
    prev_runs: list[dict] = []
    for r in runs:
        date = r.get("start_date_local", "")[:7]
        if not date or "-" not in date:
            continue
        try:
            yr, mo = (int(x) for x in date.split("-"))
        except ValueError:
            continue
        if yr == current_year and mo == current_month:
            cur_runs.append(r)
        else:
            prev_runs.append(r)

    def _summary(rs: list[dict]) -> tuple[int, float, float, dict[str, int]]:
        if not rs:
            return 0, 0.0, 0.0, {}
        total_dist = sum(a.get("distance", 0) for a in rs) / 1000
        total_min = sum(a.get("moving_time", 0) for a in rs) / 60
        avg_pace = (total_min * 60 / total_dist) if total_dist > 0 else 0.0
        anchor = _compute_anchor(rs)
        zone_counts: dict[str, int] = {}
        for r in rs:
            d = r.get("distance", 0) / 1000
            t = r.get("moving_time", 0)
            p = (t / d) if d > 0 else 0.0
            if p <= 0 or anchor <= 0:
                continue
            z = _zone_label(p, anchor).split(" (")[0]
            zone_counts[z] = zone_counts.get(z, 0) + 1
        return len(rs), total_dist, avg_pace, zone_counts

    cur_count, cur_km, cur_pace, cur_zones = _summary(cur_runs)
    prev_count, prev_km, prev_pace, prev_zones = _summary(prev_runs)

    def _zones_str(zc: dict[str, int]) -> str:
        if not zc:
            return "n/a"
        return ", ".join(f"{k}:{v}" for k, v in sorted(zc.items()))

    cur_pace_str = _sec_to_pace(cur_pace) if cur_pace > 0 else "n/a"
    prev_pace_str = _sec_to_pace(prev_pace) if prev_pace > 0 else "n/a"
    anchor_cur = _compute_anchor(cur_runs)
    verdict = _mistake_watch(cur_runs, anchor_cur)

    return (
        f"MONTHLY COMPARISON\n"
        f"                  Current month   Prior month\n"
        f"Runs:             {cur_count:<15} {prev_count}\n"
        f"Total km:         {cur_km:<15.1f} {prev_km:.1f}\n"
        f"Avg pace:         {cur_pace_str:<15} {prev_pace_str}\n"
        f"Zones (current):  {_zones_str(cur_zones)}\n"
        f"Zones (prior):    {_zones_str(prev_zones)}\n\n"
        f"VERDICT: {verdict}"
    )


async def _coach(run_text: str) -> str:
    key = _coach_cache_key(run_text)
    cached = _cache_get(key)
    if cached:
        return cached
    try:
        async with OllamaClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL) as ollama:
            insight = await ollama.generate(
                prompt=run_text, system=_COACH_SYSTEM, num_predict=350
            )
        _cache_set(key, insight, ttl=86400)  # run data is immutable; cache 24h
        return insight
    except Exception as exc:
        log.warning("Ollama unavailable: %s", exc)
        return ""


async def _ollama_insight(
    prompt: str, system: str, num_predict: int, temperature: float = 0.5
) -> str:
    """Single-shot Ollama call for handler insight panels. Empty string on failure."""
    try:
        async with OllamaClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL) as ollama:
            return await ollama.generate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                num_predict=num_predict,
            )
    except Exception as exc:
        log.warning("Ollama unavailable: %s", exc)
        return ""


# ── Handlers ──────────────────────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏃 *Strava AI Coach*\n\n"
        "/token ACCESS REFRESH — link Strava account\n"
        "/lastrun — latest run (full detail + pace graph)\n"
        "/analyze N — Nth recent run (full detail)\n"
        "/trends — last 10 runs overview\n"
        "/week — this week's summary\n"
        "/monthly — this month's stats\n"
        "/overall — all-time & YTD stats",
        parse_mode="Markdown",
    )


async def set_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: `/token ACCESS_TOKEN REFRESH_TOKEN`", parse_mode="Markdown"
        )
        return
    user_id = update.effective_user.id
    save_tokens(user_id, context.args[0], context.args[1])
    await update.message.reply_text(
        "✅ Strava connected! Tokens saved — you won't need to do this again."
    )


async def lastrun(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token ACCESS_TOKEN REFRESH_TOKEN`",
            parse_mode="Markdown",
        )
        return

    display_key = f"lastrun:{user_id}"
    log_key = f"lastrun_log:{user_id}"
    cached_display = _cache_get(display_key)
    cached_log = _cache_get(log_key)
    if cached_display:
        await update.message.reply_text(
            f"📊 *Last Run* _(cached)_\n\n```\n{cached_display}\n```",
            parse_mode="Markdown",
        )
        prompt = cached_log or cached_display
        insight = await _coach(prompt)
        if insight:
            await update.message.reply_text(
                f"🧠 *Coach*\n\n{insight}", parse_mode="Markdown"
            )
        return

    status = await update.message.reply_text("🔄 Fetching run...")
    try:
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=10)
            if not activities:
                await status.edit_text("No recent activities.")
                return
            act = activities[0]
            detail = await strava.get_activity_detail(act["id"])
            streams = await strava.get_streams(act["id"])
            run_pool = [a for a in activities if a.get("type") == "Run"]
            anchor = _compute_anchor(run_pool)
            verdict = _mistake_watch([detail], anchor)
            text = strava.format_run_detail(detail, streams)
        augmented = f"{text}\n\nVERDICT: {verdict}"
        _cache_set(display_key, text, ttl=300)
        _cache_set(log_key, augmented, ttl=300)
        await status.edit_text(
            f"📊 *Last Run*\n\n```\n{text}\n```", parse_mode="Markdown"
        )
        coach_msg = await update.message.reply_text("🧠 Asking coach...")
        insight = await _coach(augmented)
        if insight:
            await coach_msg.edit_text(f"🧠 *Coach*\n\n{insight}", parse_mode="Markdown")
        else:
            await coach_msg.delete()
    except StravaError as e:
        log.error("StravaError in lastrun: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    n = int(context.args[0]) if context.args else 1
    display_key = f"analyze:{user_id}:{n}"
    log_key = f"analyze_log:{user_id}:{n}"
    cached_display = _cache_get(display_key)
    cached_log = _cache_get(log_key)
    if cached_display:
        await update.message.reply_text(
            f"🔬 *Run #{n}* _(cached)_\n\n```\n{cached_display}\n```",
            parse_mode="Markdown",
        )
        prompt = cached_log or cached_display
        insight = await _coach(prompt)
        if insight:
            await update.message.reply_text(
                f"🧠 *Coach*\n\n{insight}", parse_mode="Markdown"
            )
        return

    status = await update.message.reply_text(f"🔄 Fetching run #{n}...")
    try:
        fetch_n = max(n, 10)
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=fetch_n)
            if len(activities) < n:
                await status.edit_text(f"Only {len(activities)} activities found.")
                return
            act = activities[n - 1]
            detail = await strava.get_activity_detail(act["id"])
            streams = await strava.get_streams(act["id"])
            run_pool = [a for a in activities if a.get("type") == "Run"]
            anchor = _compute_anchor(run_pool)
            verdict = _mistake_watch([detail], anchor)
            text = strava.format_run_detail(detail, streams)
        augmented = f"{text}\n\nVERDICT: {verdict}"
        _cache_set(display_key, text, ttl=600)
        _cache_set(log_key, augmented, ttl=600)
        await status.edit_text(
            f"🔬 *Run #{n}*\n\n```\n{text}\n```", parse_mode="Markdown"
        )
        coach_msg = await update.message.reply_text("🧠 Asking coach...")
        insight = await _coach(augmented)
        if insight:
            await coach_msg.edit_text(f"🧠 *Coach*\n\n{insight}", parse_mode="Markdown")
        else:
            await coach_msg.delete()
    except StravaError as e:
        log.error("StravaError in analyze: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def trends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    cache_key = f"trends:{user_id}"
    log_cache_key = f"trends_log:{user_id}"
    cached = _cache_get(cache_key)
    cached_log = _cache_get(log_cache_key)
    if cached:
        await update.message.reply_text(
            f"📊 *Trends* _(cached)_\n\n```\n{cached}\n```", parse_mode="Markdown"
        )
        if cached_log:
            insight = await _ollama_insight(cached_log, _TRENDS_SYSTEM, num_predict=400)
            if insight:
                await _send_long(
                    update.message,
                    f"🧠 *Trend Insight*\n\n{insight}",
                    parse_mode="Markdown",
                )
        return

    status = await update.message.reply_text("📈 Fetching last 10 runs...")
    try:
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=10)
            runs = [a for a in activities if a.get("type") == "Run"]
            if len(runs) < 3:
                await status.edit_text("Need at least 3 runs for trends.")
                return
            lines = []
            for r in runs:
                dist = r.get("distance", 0) / 1000
                t_min = r.get("moving_time", 0) / 60
                pace = t_min / dist if dist > 0 else 0
                m, s = divmod(int(pace * 60), 60)
                date = r.get("start_date_local", "")[:10]
                lines.append(f"{date}  {dist:.1f}km  {m}:{s:02d}/km")
            display_text = "\n".join(lines)
            log_text = _format_trends_log(runs)
        _cache_set(cache_key, display_text, ttl=300)
        _cache_set(log_cache_key, log_text, ttl=300)
        await status.edit_text(
            f"📊 *Last {len(runs)} Runs*\n\n```\n{display_text}\n```",
            parse_mode="Markdown",
        )
        insight_msg = await update.message.reply_text("🧠 Reading the trend...")
        insight = await _ollama_insight(log_text, _TRENDS_SYSTEM, num_predict=400)
        if insight:
            await insight_msg.delete()
            await _send_long(
                update.message,
                f"🧠 *Trend Insight*\n\n{insight}",
                parse_mode="Markdown",
            )
        else:
            await insight_msg.delete()
    except StravaError as e:
        log.error("StravaError in trends: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
    cache_key = f"week:{user_id}"
    log_cache_key = f"week_log:{user_id}"
    cached = _cache_get(cache_key)
    cached_log = _cache_get(log_cache_key)
    if cached:
        await update.message.reply_text(
            f"📅 *This Week* _(cached)_\n\n```\n{cached}\n```", parse_mode="Markdown"
        )
        if cached_log:
            insight = await _ollama_insight(cached_log, _WEEK_SYSTEM, num_predict=350)
            if insight:
                await _send_long(
                    update.message,
                    f"🧠 *Week Insight*\n\n{insight}",
                    parse_mode="Markdown",
                )
        return

    status = await update.message.reply_text("📅 Fetching this week's runs...")
    try:
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=30, after=week_ago)
            runs = [a for a in activities if a.get("type") == "Run"]
            if not runs:
                await status.edit_text("No runs this week.")
                return
            total_km = sum(a.get("distance", 0) for a in runs) / 1000
            total_min = sum(a.get("moving_time", 0) for a in runs) / 60
            display_text = strava.format_monthly(runs, "This Week")
            log_text = _format_week_log(runs)
        _cache_set(cache_key, display_text, ttl=300)
        _cache_set(log_cache_key, log_text, ttl=300)
        await status.edit_text(
            f"📅 *This Week*: {len(runs)} runs · {total_km:.1f}km · {total_min:.0f}min\n\n```\n{display_text}\n```",
            parse_mode="Markdown",
        )
        insight_msg = await update.message.reply_text("🧠 Reading the week...")
        insight = await _ollama_insight(log_text, _WEEK_SYSTEM, num_predict=350)
        if insight:
            await insight_msg.delete()
            await _send_long(
                update.message,
                f"🧠 *Week Insight*\n\n{insight}",
                parse_mode="Markdown",
            )
        else:
            await insight_msg.delete()
    except StravaError as e:
        log.error("StravaError in week: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def monthly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    now = datetime.now()
    sixty_days_ago = int((now - timedelta(days=60)).timestamp())
    month_label = now.strftime("%B %Y")
    cache_key = f"monthly:{user_id}:{now.year}-{now.month}"
    log_cache_key = f"monthly_log:{user_id}:{now.year}-{now.month}"
    cached = _cache_get(cache_key)
    cached_log = _cache_get(log_cache_key)
    if cached:
        await _send_long(
            update.message, f"📆 *{month_label}* _(cached)_\n\n```\n{cached}\n```"
        )
        if cached_log:
            insight = await _ollama_insight(
                cached_log, _MONTHLY_SYSTEM, num_predict=450
            )
            if insight:
                await _send_long(
                    update.message,
                    f"🧠 *Monthly Insight*\n\n{insight}",
                    parse_mode=None,
                )
        return

    status = await update.message.reply_text(f"📆 Fetching {month_label} stats...")
    try:
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=100, after=sixty_days_ago)
            runs_all = [a for a in activities if a.get("type") == "Run"]
            cur_month_str = now.strftime("%Y-%m")
            prev_month_date = now.replace(day=1) - timedelta(days=1)
            prev_month_str = prev_month_date.strftime("%Y-%m")
            prev_label = prev_month_date.strftime("%B %Y")
            current_runs = [
                r
                for r in runs_all
                if r.get("start_date_local", "")[:7] == cur_month_str
            ]
            prev_runs = [
                r
                for r in runs_all
                if r.get("start_date_local", "")[:7] == prev_month_str
            ]
            cur_display = strava.format_monthly(current_runs, month_label)
            if prev_runs:
                prev_display = strava.format_monthly(prev_runs, prev_label)
                display_text = f"{cur_display}\n\n{prev_display}"
            else:
                display_text = cur_display
            log_text = _format_monthly_log(runs_all, now.month, now.year)
        _cache_set(cache_key, display_text, ttl=600)
        _cache_set(log_cache_key, log_text, ttl=600)
        stats_msg = f"📆 *{month_label}*\n\n```\n{display_text}\n```"
        if len(stats_msg) <= 4000:
            await status.edit_text(stats_msg, parse_mode="Markdown")
        else:
            await status.delete()
            await _send_long(update.message, stats_msg, parse_mode="Markdown")

        insight_msg = await update.message.reply_text("🧠 Reading the month...")
        buffer = ""
        last_edit = time.time()
        try:
            async with OllamaClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL) as ollama:
                async for token in ollama.generate_stream(
                    prompt=log_text,
                    system=_MONTHLY_SYSTEM,
                    temperature=0.5,
                    num_predict=450,
                ):
                    buffer += token
                    tnow = time.time()
                    if tnow - last_edit >= 2.5 and buffer:
                        preview = buffer[-900:] if len(buffer) > 900 else buffer
                        prefix = "..." if len(buffer) > 900 else ""
                        try:
                            await insight_msg.edit_text(
                                f"🧠 Thinking...\n\n{prefix}{preview}"
                            )
                        except Exception:
                            pass
                        last_edit = tnow
        except Exception as exc:
            log.warning("Ollama unavailable in /monthly: %s", exc)
            await insight_msg.edit_text(
                "❌ Monthly insight unavailable — is Ollama running?"
            )
            return

        await insight_msg.delete()
        await _send_long(
            update.message, f"🧠 *Monthly Insight*\n\n{buffer}", parse_mode=None
        )
    except StravaError as e:
        log.error("StravaError in monthly: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def overall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    cache_key = f"overall:{user_id}"
    cached = _cache_get(cache_key)
    if cached:
        await update.message.reply_text(
            f"🌍 *Overall Stats* _(cached)_\n\n```\n{cached}\n```",
            parse_mode="Markdown",
        )
        insight = await _ollama_insight(cached, _OVERALL_SYSTEM, num_predict=300)
        if insight:
            await _send_long(
                update.message,
                f"🧠 *Where You Stand*\n\n{insight}",
                parse_mode="Markdown",
            )
        return

    status = await update.message.reply_text("🌍 Fetching overall stats...")
    try:
        async with _strava(user_id, tokens) as strava:
            stats = await strava.get_athlete_stats()
            text = strava.format_athlete_stats(stats)
        _cache_set(cache_key, text, ttl=1800)
        await status.edit_text(
            f"🌍 *Overall Stats*\n\n```\n{text}\n```", parse_mode="Markdown"
        )
        insight_msg = await update.message.reply_text("🧠 Sizing you up...")
        insight = await _ollama_insight(text, _OVERALL_SYSTEM, num_predict=300)
        if insight:
            await insight_msg.delete()
            await _send_long(
                update.message,
                f"🧠 *Where You Stand*\n\n{insight}",
                parse_mode="Markdown",
            )
        else:
            await insight_msg.delete()
    except StravaError as e:
        log.error("StravaError in overall: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def coach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tokens = _require_tokens(user_id)
    if not tokens:
        await update.message.reply_text(
            "Connect first with `/token`", parse_mode="Markdown"
        )
        return

    cache_key = f"coach_plan:{user_id}"
    cached = _cache_get(cache_key)
    if cached:
        await _send_long(
            update.message,
            f"🏋 *Training Analysis* _(cached)_\n\n{cached}",
            parse_mode=None,
        )
        return

    status = await update.message.reply_text("🔄 Fetching your last 15 runs...")
    try:
        async with _strava(user_id, tokens) as strava:
            activities = await strava.get_activities(limit=20)
            runs = [a for a in activities if a.get("type") == "Run"][:15]
        if len(runs) < 5:
            await status.edit_text(
                f"Only {len(runs)} runs found. Need at least 5 for zone analysis — keep logging!"
            )
            return
        anchor = _compute_anchor(runs)
        verdict = _mistake_watch(runs, anchor)
        log_text = f"{_format_training_log(runs)}\n\nVERDICT: {verdict}"
        insight_key = _coach_cache_key(log_text)
        insight = _cache_get(insight_key)
        if insight:
            _cache_set(cache_key, insight, ttl=1800)
            await status.delete()
            await _send_long(
                update.message, f"🏋 *Training Analysis*\n\n{insight}", parse_mode=None
            )
            return

        await status.edit_text("🧠 Coach is thinking...")
        buffer = ""
        last_edit = time.time()
        try:
            async with OllamaClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL) as ollama:
                async for token in ollama.generate_stream(
                    prompt=log_text,
                    system=_COACH_MULTI_SYSTEM,
                    temperature=0.4,
                    num_predict=1400,
                ):
                    buffer += token
                    now = time.time()
                    if now - last_edit >= 2.5 and buffer:
                        preview = buffer[-900:] if len(buffer) > 900 else buffer
                        prefix = "..." if len(buffer) > 900 else ""
                        try:
                            await status.edit_text(
                                f"🧠 Thinking...\n\n{prefix}{preview}"
                            )
                        except Exception:
                            pass
                        last_edit = now
        except Exception as exc:
            log.warning("Ollama unavailable in /coach: %s", exc)
            await status.edit_text("❌ Coach unavailable — is Ollama running?")
            return

        _cache_set(insight_key, buffer, ttl=1800)
        _cache_set(cache_key, buffer, ttl=1800)
        await status.delete()
        await _send_long(
            update.message, f"🏋 *Training Analysis*\n\n{buffer}", parse_mode=None
        )
    except StravaError as e:
        log.error("StravaError in coach: %s", e)
        await status.edit_text(f"❌ Strava error: {e}")


async def _set_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Show help"),
            BotCommand("token", "Link Strava account"),
            BotCommand("lastrun", "Latest run — full detail + coaching"),
            BotCommand("analyze", "Nth recent run (e.g. /analyze 3)"),
            BotCommand("coach", "Zone analysis + training plan from last 15 runs"),
            BotCommand("trends", "Last 10 runs overview"),
            BotCommand("week", "This week's summary"),
            BotCommand("monthly", "This month's stats"),
            BotCommand("overall", "All-time & YTD totals"),
        ]
    )


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_set_commands).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("token", set_token))
    app.add_handler(CommandHandler("lastrun", lastrun))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("coach", coach))
    app.add_handler(CommandHandler("trends", trends))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("monthly", monthly))
    app.add_handler(CommandHandler("overall", overall))
    log.info("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
