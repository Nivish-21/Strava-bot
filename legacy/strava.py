import logging
from collections.abc import Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)


class StravaError(Exception):
    pass


class StravaClient:
    BASE = "https://www.strava.com/api/v3"

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        on_refresh: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self._on_refresh = on_refresh
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "StravaClient":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.session.close()

    async def _raw_get(
        self, path: str, params: dict | None = None
    ) -> tuple[int, object]:
        r = await self.session.get(
            f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params=params,
        )
        return r.status, await r.json()

    async def refresh(self) -> None:
        r = await self.session.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        d = await r.json()
        log.info("Token refresh response (status %s): %s", r.status, d)
        if "access_token" not in d:
            raise StravaError(f"Token refresh failed: {d}")
        self.access_token = d["access_token"]
        self.refresh_token = d.get("refresh_token", self.refresh_token)
        if self._on_refresh:
            await self._on_refresh(self.access_token, self.refresh_token)

    async def _get(self, path: str, params: dict | None = None) -> object:
        status, data = await self._raw_get(path, params)
        if status == 401:
            log.info("Access token expired, refreshing...")
            await self.refresh()
            status, data = await self._raw_get(path, params)
            if status != 200:
                raise StravaError(
                    f"Strava error after refresh (status {status}): {data}"
                )
        if status != 200:
            raise StravaError(f"Strava error (status {status}): {data}")
        return data

    async def get_activities(self, limit: int = 10, after: int | None = None) -> list:
        params: dict = {"per_page": limit}
        if after is not None:
            params["after"] = after
        data = await self._get("/athlete/activities", params)
        if not isinstance(data, list):
            raise StravaError(f"Unexpected response: {data}")
        return data

    async def get_activity_detail(self, activity_id: int) -> dict:
        data = await self._get(f"/activities/{activity_id}")
        if not isinstance(data, dict):
            raise StravaError(f"Unexpected response: {data}")
        return data

    async def get_streams(self, activity_id: int) -> dict:
        data = await self._get(
            f"/activities/{activity_id}/streams",
            {
                "keys": "time,distance,velocity_smooth,heartrate,altitude,cadence",
                "key_by_type": "true",
            },
        )
        if not isinstance(data, dict):
            raise StravaError(f"Unexpected streams response: {data}")
        return data

    async def get_athlete_stats(self) -> dict:
        athlete = await self._get("/athlete")
        if not isinstance(athlete, dict):
            raise StravaError(f"Unexpected athlete response: {athlete}")
        stats = await self._get(f"/athletes/{athlete['id']}/stats")
        if not isinstance(stats, dict):
            raise StravaError(f"Unexpected stats response: {stats}")
        return stats

    # ── Formatters ────────────────────────────────────────────────────────────

    def format_run(self, activity: dict) -> str:
        dist = activity.get("distance", 0) / 1000
        time_min = activity.get("moving_time", 0) / 60
        pace = time_min / dist if dist > 0 else 0
        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")
        lines = [
            f"Run: {activity.get('name')}",
            f"Date: {activity.get('start_date_local', '')[:10]}",
            f"Distance: {dist:.2f} km  |  Time: {time_min:.0f} min  |  Pace: {_pace_str(pace)}",
        ]
        if avg_hr or max_hr:
            lines.append(
                f"Avg HR: {avg_hr or 'N/A'} bpm  |  Max HR: {max_hr or 'N/A'} bpm"
            )
        elev = activity.get("total_elevation_gain", 0)
        suffer = activity.get("suffer_score")
        elev_line = f"Elevation: +{elev:.0f}m"
        if suffer is not None:
            elev_line += f"  |  Suffer Score: {suffer}"
        lines.append(elev_line)
        return "\n".join(lines)

    def format_run_detail(self, activity: dict, streams: dict | None = None) -> str:
        dist = activity.get("distance", 0) / 1000
        time_min = activity.get("moving_time", 0) / 60
        pace = time_min / dist if dist > 0 else 0

        avg_hr = activity.get("average_heartrate")
        max_hr = activity.get("max_heartrate")
        lines = [
            f"Run: {activity.get('name')}",
            f"Date: {activity.get('start_date_local', '')[:10]}",
            f"Distance: {dist:.2f} km  |  Time: {time_min:.0f} min  |  Pace: {_pace_str(pace)}",
        ]
        if avg_hr or max_hr:
            lines.append(
                f"Avg HR: {avg_hr or 'N/A'} bpm  |  Max HR: {max_hr or 'N/A'} bpm"
            )
        elev = activity.get("total_elevation_gain", 0)
        cadence = activity.get("average_cadence")
        suffer = activity.get("suffer_score")
        meta_parts = [f"Elevation: +{elev:.0f}m"]
        if cadence:
            meta_parts.append(f"Cadence: {cadence:.0f} spm")
        if suffer is not None:
            meta_parts.append(f"Suffer: {suffer}")
        lines.append("  |  ".join(meta_parts))

        splits = activity.get("splits_metric", [])
        if splits:
            has_split_hr = any(s.get("average_heartrate") for s in splits)
            lines.append("\n── KM SPLITS ──────────────────────────────")
            lines.append(
                " Km │ Pace    │  HR  │ Elev"
                if has_split_hr
                else " Km │ Pace    │ Elev"
            )
            for i, s in enumerate(splits, 1):
                s_dist = s.get("distance", 0) / 1000
                s_time = s.get("moving_time", 0) / 60
                s_pace = s_time / s_dist if s_dist > 0 else 0
                elev = s.get("elevation_difference", 0)
                if has_split_hr:
                    hr = s.get("average_heartrate")
                    hr_str = f"{hr:.0f}" if hr else " N/A"
                    lines.append(
                        f" {i:2} │ {_pace_str(s_pace):7} │ {hr_str:4} │ {elev:+.0f}m"
                    )
                else:
                    lines.append(f" {i:2} │ {_pace_str(s_pace):7} │ {elev:+.0f}m")

        if streams:
            trend = _pace_trend(streams)
            if trend:
                lines.append("\n── PACE TREND (first / mid / last third) ──")
                lines.append(trend)

            cadence_avg = _stream_avg(streams, "cadence")
            if cadence_avg:
                lines.append(f"\nAvg cadence from stream: {cadence_avg:.0f} spm")

        efforts = activity.get("best_efforts", [])
        if efforts:
            wanted = {"400m", "1/2 mile", "1k", "1 mile", "2 mile", "5k", "10k"}
            effort_lines = []
            for e in efforts:
                if e.get("name") in wanted:
                    t = e.get("moving_time", 0)
                    m, s = divmod(t, 60)
                    effort_lines.append(f"  {e['name']}: {m}:{s:02d}")
            if effort_lines:
                lines.append("\n── BEST EFFORTS ───────────────────────────")
                lines.extend(effort_lines)

        return "\n".join(lines)

    def format_monthly(self, activities: list, month_label: str) -> str:
        runs = [a for a in activities if a.get("type") == "Run"]
        if not runs:
            return f"No runs in {month_label}."
        total_km = sum(a.get("distance", 0) for a in runs) / 1000
        total_min = sum(a.get("moving_time", 0) for a in runs) / 60
        total_elev = sum(a.get("total_elevation_gain", 0) for a in runs)
        avg_pace = total_min / total_km if total_km > 0 else 0
        has_hr = any(r.get("average_heartrate") for r in runs)
        lines = [
            f"── {month_label} ──────────────────────────────",
            f"Runs: {len(runs)}  |  Distance: {total_km:.1f} km  |  Time: {total_min:.0f} min",
            f"Avg Pace: {_pace_str(avg_pace)}  |  Total Elevation: +{total_elev:.0f}m",
            "",
            (
                " Date       │ Dist  │ Pace    │  HR  │ Elev"
                if has_hr
                else " Date       │ Dist  │ Pace    │ Elev"
            ),
        ]
        for r in sorted(
            runs, key=lambda a: a.get("start_date_local", ""), reverse=True
        ):
            d_km = r.get("distance", 0) / 1000
            t_min = r.get("moving_time", 0) / 60
            p = t_min / d_km if d_km > 0 else 0
            date = r.get("start_date_local", "")[:10]
            elev = r.get("total_elevation_gain", 0)
            if has_hr:
                hr = r.get("average_heartrate")
                hr_str = f"{hr:.0f}" if hr else " N/A"
                lines.append(
                    f" {date} │ {d_km:4.1f}  │ {_pace_str(p):7} │ {hr_str:4} │ +{elev:.0f}m"
                )
            else:
                lines.append(
                    f" {date} │ {d_km:4.1f}  │ {_pace_str(p):7} │ +{elev:.0f}m"
                )
        return "\n".join(lines)

    def format_athlete_stats(self, stats: dict) -> str:
        def _block(label: str, s: dict) -> str:
            dist = s.get("distance", 0) / 1000
            time_h = s.get("moving_time", 0) / 3600
            count = s.get("count", 0)
            elev = s.get("elevation_gain", 0)
            return f"{label}: {count} runs · {dist:.0f} km · {time_h:.0f}h · +{elev:.0f}m elev"

        return "\n".join(
            [
                "── ALL TIME ───────────────────────────────",
                _block("Total", stats.get("all_run_totals", {})),
                "",
                "── YEAR TO DATE ───────────────────────────",
                _block("YTD  ", stats.get("ytd_run_totals", {})),
                "",
                "── LAST 4 WEEKS ───────────────────────────",
                _block("4wk  ", stats.get("recent_run_totals", {})),
            ]
        )


# ── Module-level helpers ──────────────────────────────────────────────────────


def _pace_str(pace_min_per_km: float) -> str:
    m = int(pace_min_per_km)
    s = int((pace_min_per_km % 1) * 60)
    return f"{m}:{s:02d}/km"


def _stream_avg(streams: dict, key: str) -> float | None:
    data = streams.get(key, {}).get("data", [])
    valid = [v for v in data if v and v > 0]
    return sum(valid) / len(valid) if valid else None


def _pace_trend(streams: dict) -> str:
    dist_data = streams.get("distance", {}).get("data", [])
    vel_data = streams.get("velocity_smooth", {}).get("data", [])
    if not dist_data or not vel_data or len(vel_data) < 10:
        return ""
    n = len(vel_data)
    thirds = [vel_data[: n // 3], vel_data[n // 3 : 2 * n // 3], vel_data[2 * n // 3 :]]
    labels = ["First third ", "Middle      ", "Last third  "]
    parts = []
    for label, vels in zip(labels, thirds):
        valid = [v for v in vels if v > 0]
        if not valid:
            continue
        avg_vel = sum(valid) / len(valid)
        pace = (1000 / avg_vel) / 60
        parts.append(f"  {label}: {_pace_str(pace)}")
    return "\n".join(parts)
