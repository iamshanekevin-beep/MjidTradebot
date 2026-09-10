# AGENTS.md — IQ Option OTC Trading Bot

## What this is
A Python trading bot for IQ Option OTC pairs with a Flask web dashboard. The
bot runs in a background thread (`main.run_bot`) and the dashboard
(`dashboard.py`) serves a live UI on port 3000 showing signals, trades,
performance, and controls.

## Architecture
- `dashboard.py` — Flask app on port 3000, starts the bot in a daemon thread
- `bot_state.py` — thread-safe shared state (singleton `state`) between bot and dashboard
- `main.py` — bot loop (`run_bot()`), updates `bot_state` each cycle
- `templates/index.html` — dashboard frontend (vanilla JS polls `/api/state` every 3s)
- `config.py` — all settings from env vars
- `broker.py` — IQ Option connection + trade placement
- `strategy.py` / `indicators.py` — FCB + Pole Position signal logic

## API endpoints
- `GET /` — dashboard page
- `GET /api/state` — current bot state (JSON)
- `POST /api/control` — update controls (`auto_trade`, `paused`, `trade_amount`, `account_type`)

## Running it
```
docker compose -f docker-compose.base44.yml up -d
docker compose -f docker-compose.base44.yml logs -f bot
```
Dashboard is at `http://localhost:3000`.

## Key gotcha: the `iqoptionapi` dependency
`requirements.txt` must install from the **GitHub** source, NOT PyPI. The PyPI
package `iqoptionapi` (0.5) is a different, incompatible fork that lacks the
`stable_api` module. The code imports `from iqoptionapi.stable_api import IQ_Option`,
which only exists in the GitHub version (Lu-Yi-Hsun/iqoptionapi, v6.8.x).

## Required secrets
- `IQ_EMAIL` — IQ Option login email
- `IQ_PASSWORD` — IQ Option password

Without these the bot logs an error and the dashboard shows "Disconnected."
Local defaults live in `.env.base44-defaults` (loaded first by compose); real
secrets are delivered via `/run/base44/app.env` and override them.

## Safety defaults
- `AUTO_TRADE=false` — signals only, no trades placed
- `TRADE_AMOUNT=1` — minimal amount
- `ACCOUNT_TYPE=PRACTICE`

These are set in `.env.base44-defaults` and `config.py` defaults. The dashboard
can toggle `AUTO_TRADE` and change `TRADE_AMOUNT` at runtime — verify deliberately.
