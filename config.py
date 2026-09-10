"""
Configuration loaded from environment variables.
On Base44/Railway: set these under your project's Secrets/Variables section.
Locally: copy .env.example to .env and fill it in.

SAFETY NOTE: AUTO_TRADE defaults to False and TRADE_AMOUNT defaults to a
small value. This is deliberate — a missing or misconfigured environment
variable should never cause the bot to silently start placing large real
trades. You must explicitly set AUTO_TRADE=true to enable live trading.
"""
import os

def _get_bool(key, default=False):
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

def _get_float(key, default):
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return float(default)

def _get_int(key, default):
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return int(default)

# --- IQ Option account ---
IQ_EMAIL = os.getenv("IQ_EMAIL", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD", "")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "PRACTICE")  # PRACTICE or REAL — start on PRACTICE

# Hosts to try, in order. Some proxies/networks can reach only some of IQ
# Option's domains (they all serve the same API), so we fall back in order.
# Each entry is "api_host", "api_host|websocket_host", or
# "api_host|websocket_host|auth_host".
IQ_HOSTS = [h.strip() for h in os.getenv(
    "IQ_HOSTS",
    "iqbroker.com|ws.iqbroker.com|auth.iqbroker.com,iqoption.com"
).split(",") if h.strip()]

# --- Market / instrument ---
PAIR = os.getenv("PAIR", "EURUSD-OTC")
PAIRS = [p.strip() for p in os.getenv(
    "PAIRS",
    "EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC,AUDUSD-OTC,USDCAD-OTC,"
    "EURJPY-OTC,EURGBP-OTC,NZDUSD-OTC,USDCHF-OTC,GBPJPY-OTC"
).split(",") if p.strip()]
TIMEFRAME_SECONDS = _get_int("TIMEFRAME_SECONDS", 60)     # candle size, 60 = M1
CANDLE_COUNT = _get_int("CANDLE_COUNT", 100)              # how many candles to pull each cycle
EXPIRATION_MINUTES = _get_int("EXPIRATION_MINUTES", 1)    # trade expiry

# --- Scanning ---
SCAN_INTERVAL_SECONDS = _get_float("SCAN_INTERVAL_SECONDS", 1.0)  # hunt signals every 1s

# --- Strategy selection ---
STRATEGY = os.getenv("STRATEGY", "FCB").upper()  # FCB breakout + candle confirmation

# Fractal Chaos Bands
FCB_FRACTAL_PERIOD = _get_int("FCB_FRACTAL_PERIOD", 2)

# --- Risk / money management ---
TRADE_AMOUNT = _get_float("TRADE_AMOUNT", 1.0)
MAX_TRADES_PER_DAY = _get_int("MAX_TRADES_PER_DAY", 20)
MAX_CONSECUTIVE_LOSSES = _get_int("MAX_CONSECUTIVE_LOSSES", 3)
COOLDOWN_MINUTES = _get_int("COOLDOWN_MINUTES", 15)       # cooldown after max consecutive losses
DAILY_LOSS_LIMIT = _get_float("DAILY_LOSS_LIMIT", 20.0)

# --- Behavior ---
AUTO_TRADE = _get_bool("AUTO_TRADE", False)
POLL_SECONDS = _get_int("POLL_SECONDS", 5)  # legacy, kept for compatibility


def startup_banner():
    """Loud, impossible-to-miss summary of the live settings at boot."""
    lines = [
        "=" * 60,
        "BOT STARTUP — CURRENT LIVE SETTINGS",
        f"  ACCOUNT_TYPE   = {ACCOUNT_TYPE}",
        f"  AUTO_TRADE     = {AUTO_TRADE}  {'<<< TRADES WILL BE PLACED' if AUTO_TRADE else '(signals only, no trades)'}",
        f"  TRADE_AMOUNT   = {TRADE_AMOUNT}  (base stake, no martingale)",
        f"  PAIRS          = {len(PAIRS)} pairs: {', '.join(PAIRS)}",
        f"  SCAN_INTERVAL  = {SCAN_INTERVAL_SECONDS}s",
        f"  STRATEGY       = {STRATEGY} (FCB breakout + candle confirmation)",
        f"  TIMEFRAME      = {TIMEFRAME_SECONDS}s candle, {EXPIRATION_MINUTES}min expiry",
        f"  MAX_TRADES_PER_DAY     = {MAX_TRADES_PER_DAY}",
        f"  MAX_CONSECUTIVE_LOSSES  = {MAX_CONSECUTIVE_LOSSES} → {COOLDOWN_MINUTES}-min cooldown",
        f"  DAILY_LOSS_LIMIT        = {DAILY_LOSS_LIMIT}",
        "=" * 60,
    ]
    return "\n".join(lines)
