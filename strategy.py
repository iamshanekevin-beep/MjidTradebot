"""
Strategy logic: FCB breakout + candle body confirmation (clean signals only).

A "clean signal" requires BOTH:
1. FCB breakout — price closes above the upper fractal band (CALL) or below
   the lower fractal band (PUT).
2. Candle body confirmation — the breakout candle's body is in the same
   direction (close > open for CALL, close < open for PUT).

If the breakout occurs but the candle body doesn't confirm the direction,
it's a "blind trade" and is skipped. No indicators (EMA/RSI/CCI/BB) needed —
pole position is just the candle direction confirming the breakout trend.
"""
import pandas as pd
import config
import indicators as ind


def get_signal(df):
    """
    Returns (direction, info) where direction is "CALL", "PUT", or None.
    Only returns a non-None direction for clean, confirmed signals.
    """
    upper, lower = ind.fractal_chaos_bands(df, period=config.FCB_FRACTAL_PERIOD)
    price = df["close"].iloc[-1]
    open_price = df["open"].iloc[-1]
    up = upper.iloc[-1]
    low = lower.iloc[-1]

    if pd.isna(up) or pd.isna(low):
        return None, {"reason": "bands not yet confirmed", "price": price}

    info = {
        "price": price,
        "open": open_price,
        "upper_band": up,
        "lower_band": low,
    }

    # --- FCB breakout ---
    breakout_dir = None
    if price > up:
        breakout_dir = "CALL"
    elif price < low:
        breakout_dir = "PUT"

    if breakout_dir is None:
        info["reason"] = "inside bands"
        return None, info

    # --- Candle body confirmation (clean signal check) ---
    candle_bullish = price > open_price
    candle_bearish = price < open_price
    info["candle_dir"] = "bullish" if candle_bullish else "bearish"

    if breakout_dir == "CALL" and not candle_bullish:
        info["reason"] = "breakout CALL but candle bearish — blind trade skipped"
        return None, info
    if breakout_dir == "PUT" and not candle_bearish:
        info["reason"] = "breakout PUT but candle bullish — blind trade skipped"
        return None, info

    # --- Clean signal: FCB breakout + candle direction confirmed ---
    info["reason"] = "clean signal: FCB breakout + candle confirmed"
    return breakout_dir, info
