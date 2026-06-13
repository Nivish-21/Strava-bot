import json
from pathlib import Path

from analytics import (
    load_activities,
    WeekStats,
    week_key,
    weekly_aggregates,
    Trend,
    trend,
)


def _write_snapshot(directory: Path, name: str, activities: list[dict]) -> None:
    (directory / name).write_text(json.dumps(activities), encoding="utf-8")


def test_load_activities_dedupes_by_id(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "2026-06-01.json", [{"id": 1}, {"id": 2}])
    _write_snapshot(tmp_path, "2026-06-08.json", [{"id": 2}, {"id": 3}])

    result = load_activities(str(tmp_path))

    ids = sorted(a["id"] for a in result)
    assert ids == [1, 2, 3]


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
