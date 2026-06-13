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
