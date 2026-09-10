# IQ Option OTC Trading Bot — Fractal Chaos Bands + Pole Position

Auto-trading bot for IQ Option OTC pairs, combining:

**Fractal Chaos Bands (FCB)** — single clean-entry indicator:
price above the high band = CALL, below the low band = PUT, inside = no trade.

**Pole Position** — scoring system across EMA cross, RSI, CCI, and Bollinger Bands.

`STRATEGY=BOTH` (default) requires both to agree before a trade fires — stricter, fewer, higher-conviction signals.

## ⚠️ What changed after the last incident

A previous Base44 deployment of this bot ended up with `TRADE_AMOUNT=100` and
`AUTO_TRADE` effectively on, with no one having deliberately set either —
real $100 trades fired automatically. Two things fixed that here:

1. **`config.py` now defaults `AUTO_TRADE=False` and `TRADE_AMOUNT=1`** — if an
   environment variable is ever missing or fails to load, the bot fails safe
   (logs only) instead of failing dangerous.
2. **`.env.base44-defaults` explicitly sets both values** at the top of the
   file with a comment explaining why — so they're never accidentally blank
   or inherited from an old config.

**Still — always verify manually before reconnecting your IQ Option login
anywhere:** open your Base44 project's Secrets/Environment Variables and
confirm `AUTO_TRADE` and `TRADE_AMOUNT` are exactly what you expect. Don't
trust the defaults blindly either — check them.

## Deploying to Base44 (correctly, this time)

1. Upload/sync all these files to your Base44 project.
2. In Base44's project settings, find the **Secrets** or **Environment
   Variables** section (you'll see it alongside where `IQ_EMAIL` /
   `IQ_PASSWORD` are configured).
3. Explicitly set:
   - `AUTO_TRADE` = `true`
   - `TRADE_AMOUNT` = `1` (or whatever small amount you intend)
   - `ACCOUNT_TYPE` = `PRACTICE`
4. **Do not add `IQ_EMAIL` / `IQ_PASSWORD` yet.** Deploy first without
   credentials if possible, or accept that adding them starts the container
   (per `AGENTS.md`'s `requiredAtBoot` behavior) — either way, `AUTO_TRADE=false`
   means it will only log signals even once it boots.
5. Check the logs. You should see the startup banner:
   ```
   ============================================================
   BOT STARTUP — CURRENT LIVE SETTINGS
     ACCOUNT_TYPE   = PRACTICE
     AUTO_TRADE     = False  (signals only, no trades)
     TRADE_AMOUNT   = 1.0
     ...
   ============================================================
   ```
   If `AUTO_TRADE` shows `True` here and you didn't set it to `true`
   yourself, **stop and investigate before doing anything else** — do not
   assume it will sort itself out.
6. Watch signal logs for a while. Only once you trust them, deliberately
   change `AUTO_TRADE` to `true` in Base44's settings and redeploy.

## Note on `docker-compose.base44.yml`'s `restart: unless-stopped`

Base44 auto-generates a compose file with `restart: unless-stopped` on the
bot service. This means the container restarts itself after a crash or
reboot — and critically, reconnecting your IQ Option credentials can
trigger `docker compose up -d` again per `AGENTS.md`. That's expected
platform behavior, not a bug — which is exactly why the code-level safe
defaults above matter: even an unexpected restart now boots into
"signals only" mode unless `AUTO_TRADE=true` was deliberately set.

## Files

| File | Purpose |
|---|---|
| `main.py` | Main loop — logs a startup banner, fetches candles, checks signals, trades if `AUTO_TRADE=true` |
| `strategy.py` | FCB and Pole Position signal logic |
| `indicators.py` | EMA, RSI, CCI, Bollinger Bands, Fractal Chaos Bands math |
| `broker.py` | IQ Option connection + order placement |
| `config.py` | All settings — safe-by-default |
| `.env.base44-defaults` | Explicit safe defaults for Base44 deployments |
| `.env.example` | Template for local testing |

## Local testing

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your values, keep AUTO_TRADE=false
export $(cat .env | xargs)
python main.py
```
