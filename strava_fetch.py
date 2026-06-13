"""Fetch recent Strava activities and store a dated snapshot for analytics.

Usage:
    python strava_fetch.py [weeks]      # default: last 6 weeks

Requires tokens from a prior `python strava_auth.py` run.
"""

from __future__ import annotations

import sys
import time
from datetime import date

import strava_api


def main(argv: list[str]) -> int:
    weeks = int(argv[1]) if len(argv) > 1 else 6
    now = time.time()
    access_token = strava_api.valid_access_token(now=now)
    activities = strava_api.fetch_activities(
        access_token, after=strava_api.after_epoch(now, weeks)
    )
    path = strava_api.write_snapshot(activities, "data/activities", date.today())
    print(f"Stored {len(activities)} activities ({weeks} weeks) to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
