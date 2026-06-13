from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")


@app.get("/auth/callback")
async def strava_callback(code: str, state: str = None):
    """Exchange authorization code for tokens"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        ) as resp:
            data = await resp.json()

            if "access_token" not in data:
                return HTMLResponse(
                    f"<h1>❌ Error</h1><pre>{data}</pre>", status_code=400
                )
            return HTMLResponse(f"""
                <h1>✅ Strava Connected!</h1>
                <p>Send this in Telegram:</p>
                <code>/token {data['access_token']} {data['refresh_token']}</code>
            """)
