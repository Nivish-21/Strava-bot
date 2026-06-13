"""Free direct access to the Strava v3 API (no paid MCP).

Pure helpers (env parsing, OAuth URL building, token-expiry logic, snapshot writing)
are unit-tested. The thin HTTP wrappers use only the standard library so the project
keeps zero runtime dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

TOKEN_URL = "https://www.strava.com/oauth/token"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
API_BASE = "https://www.strava.com/api/v3"
DEFAULT_TOKENS_PATH = Path("data/strava_tokens.json")
REDIRECT_URI = "http://localhost/exchange_token"
SCOPE = "activity:read_all"
REFRESH_BUFFER_SECONDS = 60


def parse_env(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines from a .env file, ignoring comments and blanks."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_credentials(env_path: str = ".env") -> tuple[str, str]:
    """Read the Strava API app's client id and secret from a .env file."""
    values = parse_env(Path(env_path).read_text(encoding="utf-8"))
    try:
        return values["STRAVA_CLIENT_ID"], values["STRAVA_CLIENT_SECRET"]
    except KeyError as exc:
        raise KeyError(f"Missing {exc} in {env_path}") from exc


def authorize_url(client_id: str) -> str:
    """Build the Strava OAuth authorise URL the user opens once in a browser."""
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "approval_prompt": "force",
            "scope": SCOPE,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def parse_code(redirect_url: str) -> str:
    """Extract the one-time authorisation code from the post-approval redirect URL."""
    query = urllib.parse.urlparse(redirect_url).query
    params = urllib.parse.parse_qs(query)
    if "code" not in params:
        raise ValueError("No 'code' parameter in redirect URL")
    return params["code"][0]


def needs_refresh(tokens: dict, now: float) -> bool:
    """True if the access token is expired or within the refresh buffer of expiring."""
    return now >= tokens.get("expires_at", 0) - REFRESH_BUFFER_SECONDS


def after_epoch(now: float, weeks: int = 6) -> int:
    """Unix timestamp `weeks` before `now`, for the Strava `after` query parameter."""
    return int(now - weeks * 7 * 86400)


def write_snapshot(activities: list, snapshot_dir: str, today: date) -> Path:
    """Write the raw activity list to `<snapshot_dir>/<YYYY-MM-DD>.json`."""
    path = Path(snapshot_dir) / f"{today.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(activities, indent=2), encoding="utf-8")
    return path


def _post_form(url: str, fields: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode())


def _get_json(url: str, access_token: str) -> object:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token}"}
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode())


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    """Exchange a one-time authorisation code for access and refresh tokens."""
    return _post_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )


def refresh_tokens(refresh_token: str, client_id: str, client_secret: str) -> dict:
    """Trade a refresh token for a fresh access token (and rotated refresh token)."""
    return _post_form(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


def save_tokens(tokens: dict, path: Path = DEFAULT_TOKENS_PATH) -> None:
    """Persist the token payload to disk (gitignored under data/)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def load_tokens(path: Path = DEFAULT_TOKENS_PATH) -> dict:
    """Load the stored token payload."""
    return json.loads(path.read_text(encoding="utf-8"))


def valid_access_token(
    path: Path = DEFAULT_TOKENS_PATH,
    env_path: str = ".env",
    now: float | None = None,
) -> str:
    """Return a usable access token, refreshing and re-saving it if it has expired."""
    current = time.time() if now is None else now
    tokens = load_tokens(path)
    if needs_refresh(tokens, current):
        client_id, client_secret = load_credentials(env_path)
        tokens = refresh_tokens(tokens["refresh_token"], client_id, client_secret)
        save_tokens(tokens, path)
    return tokens["access_token"]


def fetch_activities(access_token: str, after: int, per_page: int = 100) -> list:
    """Fetch the athlete's activities recorded after the given Unix timestamp."""
    query = urllib.parse.urlencode({"after": after, "per_page": per_page})
    result = _get_json(f"{API_BASE}/athlete/activities?{query}", access_token)
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected Strava response: {result}")
    return result
