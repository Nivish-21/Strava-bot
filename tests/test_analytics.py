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
