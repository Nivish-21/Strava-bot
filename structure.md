strava-gemma-bot/
├── .env
├── requirements.txt
├── bot.py              # Telegram bot
├── strava.py           # Strava API client
├── ollama_client.py    # Ollama API wrapper
├── auth_server.py      # OAuth callback (only needed for setup)
└── data/
    └── tokens.db       # SQLite for user tokens