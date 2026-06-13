"""One-time Strava OAuth to get tokens for the free Strava API.

Usage:
    python strava_auth.py
        Prints the authorise URL. Open it, click Authorize.

    python strava_auth.py "<redirect_url>"
        After authorising, your browser is redirected to a localhost URL that fails to
        load — that is expected. Copy the full URL from the address bar and pass it here
        to exchange the code and save tokens to data/strava_tokens.json.
"""

from __future__ import annotations

import sys

import strava_api


def main(argv: list[str]) -> int:
    client_id, client_secret = strava_api.load_credentials()

    if len(argv) < 2:
        print("1. Open this URL and click Authorize:\n")
        print(strava_api.authorize_url(client_id))
        print(
            "\n2. Your browser will redirect to a localhost URL that fails to load."
            "\n   That is fine. Copy the full URL from the address bar and run:\n"
            '\n   python strava_auth.py "<paste the redirect URL>"'
        )
        return 0

    code = strava_api.parse_code(argv[1])
    tokens = strava_api.exchange_code(code, client_id, client_secret)
    if "access_token" not in tokens:
        print(f"Token exchange failed: {tokens}", file=sys.stderr)
        return 1
    strava_api.save_tokens(tokens)
    print(
        f"Saved tokens to {strava_api.DEFAULT_TOKENS_PATH}. "
        "You can now run: python strava_fetch.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
