# AGENTS.md — IQ Option OTC Trading Bot

## What this is
A headless Python trading bot (no web UI, no port 3000 web server). It runs a
long-running `main.py` loop that connects to IQ Option, fetches candles, checks
FCB + Pole Position signals, and optionally places trades. Output is via logs
only — `docker compose -f docker-compose.base44.yml logs -f bot`.

## Running it
```
docker compose -f docker-compose.base44.yml up -d
docker compose -f docker-compose.base44.yml logs -f bot
```

## Key gotcha: the `iqoptionapi` dependency
`requirements.txt` must install from the **GitHub** source, NOT PyPI. The PyPI
package `iqoptionapi` (0.5) is a different, incompatible fork that lacks the
`stable_api` module. The code imports `from iqoptionapi.stable_api import IQ_Option`,
which only exists in the GitHub version (Lu-Yi-Hsun/iqoptionapi, v6.8.x).

## Required secrets
- `IQ_EMAIL` — IQ Option login email
- `IQ_PASSWORD` — IQ Option password

Without these the bot logs an error and exits. With them it connects and runs.
Local defaults live in `.env.base44-defaults` (loaded first by compose); real
secrets are delivered via `/run/base44/app.env` and override them.

## Safety defaults
- `AUTO_TRADE=false` — signals only, no trades placed
- `TRADE_AMOUNT=1` — minimal amount
- `ACCOUNT_TYPE=PRACTICE`

Both are set in `.env.base44-defaults` and `config.py` defaults. Changing
`AUTO_TRADE` to `true` enables live trading — verify deliberately.
