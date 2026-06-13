from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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


def sport_of(activity: dict) -> str:
    """The activity's sport, preferring the modern `sport_type` over legacy `type`."""
    return activity.get("sport_type") or activity.get("type") or "Unknown"


def filter_by_sport(activities: list[dict], sport_type: str) -> list[dict]:
    """Keep only the activities of a single sport (e.g. 'Run', 'Walk')."""
    return [a for a in activities if sport_of(a) == sport_type]


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
        avg_pace = (
            total_moving_time_min / total_distance_km if total_distance_km > 0 else 0.0
        )
        hrs = [
            a["average_heartrate"]
            for a in items
            if a.get("average_heartrate") is not None
        ]
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


@dataclass(frozen=True)
class Trend:
    latest_week: str
    count_delta: int
    pace_change_min_per_km: float
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
    priors = ordered[-(prior_weeks + 1) : -1]
    avg_count = sum(w.activity_count for w in priors) / len(priors)
    avg_pace = sum(w.avg_pace_min_per_km for w in priors) / len(priors)
    return Trend(
        latest_week=latest.week,
        count_delta=latest.activity_count - round(avg_count),
        pace_change_min_per_km=round(latest.avg_pace_min_per_km - avg_pace, 2),
        is_more_consistent=latest.activity_count >= avg_count,
        is_faster=latest.avg_pace_min_per_km < avg_pace,
    )


def weekly_aggregates_by_sport(
    activities: list[dict],
) -> dict[str, dict[str, WeekStats]]:
    """Per-sport weekly stats: {sport_type: {week: WeekStats}}.

    Splitting by sport keeps pace comparable like-with-like; mixing runs and walks
    into one pace figure produces misleading trends.
    """
    sports = sorted({sport_of(a) for a in activities})
    return {
        sport: weekly_aggregates(filter_by_sport(activities, sport)) for sport in sports
    }
