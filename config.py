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

# --- Market / instrument ---
PAIR = os.getenv("PAIR", "EURUSD-OTC")
TIMEFRAME_SECONDS = _get_int("TIMEFRAME_SECONDS", 60)     # candle size, e.g. 60 = M1
CANDLE_COUNT = _get_int("CANDLE_COUNT", 100)              # how many candles to pull each cycle
EXPIRATION_MINUTES = _get_int("EXPIRATION_MINUTES", 1)    # trade expiry

# --- Strategy selection ---
STRATEGY = os.getenv("STRATEGY", "BOTH").upper()  # FCB, POLE_POSITION, or BOTH

# Fractal Chaos Bands
FCB_FRACTAL_PERIOD = _get_int("FCB_FRACTAL_PERIOD", 2)

# Pole Position indicator settings
EMA_FAST = _get_int("EMA_FAST", 9)
EMA_SLOW = _get_int("EMA_SLOW", 21)
RSI_PERIOD = _get_int("RSI_PERIOD", 14)
RSI_UP = _get_float("RSI_UP", 55)
RSI_DOWN = _get_float("RSI_DOWN", 45)
CCI_PERIOD = _get_int("CCI_PERIOD", 14)
CCI_UP = _get_float("CCI_UP", 100)
CCI_DOWN = _get_float("CCI_DOWN", -100)
BB_PERIOD = _get_int("BB_PERIOD", 20)
BB_STD = _get_float("BB_STD", 2.0)
POLE_POSITION_SCORE_THRESHOLD = _get_int("POLE_POSITION_SCORE_THRESHOLD", 3)

# --- Risk / money management ---
# SAFE DEFAULT: $1. A previous deployment had TRADE_AMOUNT silently set to
# $100 in its environment, which is exactly the kind of mistake defaults
# should protect against, not enable. Always double check this value
# explicitly in your platform's env var / secrets UI before enabling AUTO_TRADE.
TRADE_AMOUNT = _get_float("TRADE_AMOUNT", 1.0)
MAX_TRADES_PER_DAY = _get_int("MAX_TRADES_PER_DAY", 20)
MAX_CONSECUTIVE_LOSSES = _get_int("MAX_CONSECUTIVE_LOSSES", 3)
DAILY_LOSS_LIMIT = _get_float("DAILY_LOSS_LIMIT", 20.0)

# --- Behavior ---
# SAFE DEFAULT: False. If this variable is ever missing, unset, or fails to
# be picked up by a platform's env system, the bot must fall back to
# logging signals only — never to placing real trades. Set AUTO_TRADE=true
# explicitly, only once you've watched the logs and trust what you see.
AUTO_TRADE = _get_bool("AUTO_TRADE", False)
POLL_SECONDS = _get_int("POLL_SECONDS", 5)


def startup_banner():
    """Loud, impossible-to-miss summary of the live settings at boot."""
    lines = [
        "=" * 60,
        "BOT STARTUP — CURRENT LIVE SETTINGS",
        f"  ACCOUNT_TYPE   = {ACCOUNT_TYPE}",
        f"  AUTO_TRADE     = {AUTO_TRADE}  {'<<< TRADES WILL BE PLACED' if AUTO_TRADE else '(signals only, no trades)'}",
        f"  TRADE_AMOUNT   = {TRADE_AMOUNT}",
        f"  PAIR           = {PAIR}",
        f"  STRATEGY       = {STRATEGY}",
        f"  MAX_TRADES_PER_DAY     = {MAX_TRADES_PER_DAY}",
        f"  MAX_CONSECUTIVE_LOSSES = {MAX_CONSECUTIVE_LOSSES}",
        f"  DAILY_LOSS_LIMIT       = {DAILY_LOSS_LIMIT}",
        "=" * 60,
    ]
    return "\n".join(lines)
