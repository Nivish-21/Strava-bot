import json
from datetime import date
from pathlib import Path

import strava_api


def test_parse_env_ignores_comments_and_blanks() -> None:
    text = "STRAVA_CLIENT_ID=abc\n# a comment\n\nSTRAVA_CLIENT_SECRET=def\n"
    values = strava_api.parse_env(text)
    assert values == {"STRAVA_CLIENT_ID": "abc", "STRAVA_CLIENT_SECRET": "def"}


def test_authorize_url_has_client_scope_and_redirect() -> None:
    url = strava_api.authorize_url("12345")
    assert "client_id=12345" in url
    assert "activity%3Aread_all" in url
    assert "redirect_uri=" in url


def test_parse_code_extracts_code_param() -> None:
    redirect = "http://localhost/exchange_token?state=&code=ABC123&scope=read,activity:read_all"
    assert strava_api.parse_code(redirect) == "ABC123"


def test_needs_refresh_respects_buffer() -> None:
    assert strava_api.needs_refresh({"expires_at": 1000}, now=2000) is True
    assert strava_api.needs_refresh({"expires_at": 1000}, now=500) is False
    assert strava_api.needs_refresh({"expires_at": 1000}, now=950) is True


def test_after_epoch_subtracts_weeks() -> None:
    now = 1_000_000_000
    assert strava_api.after_epoch(now, weeks=6) == now - 6 * 7 * 86400


def test_write_snapshot_writes_dated_json(tmp_path: Path) -> None:
    activities = [{"id": 1}, {"id": 2}]
    path = strava_api.write_snapshot(activities, str(tmp_path), date(2026, 6, 14))
    assert path == tmp_path / "2026-06-14.json"
    assert json.loads(path.read_text(encoding="utf-8")) == activities
