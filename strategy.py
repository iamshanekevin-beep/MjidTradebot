"""
Strategy logic. Each function returns one of "CALL", "PUT", or None (no trade).
"""
import pandas as pd
import config
import indicators as ind


def fcb_signal(df):
    upper, lower = ind.fractal_chaos_bands(df, period=config.FCB_FRACTAL_PERIOD)
    price = df["close"].iloc[-1]
    up = upper.iloc[-1]
    low = lower.iloc[-1]

    if pd.isna(up) or pd.isna(low):
        return None, {"reason": "bands not yet confirmed"}

    if price > up:
        return "CALL", {"price": price, "upper_band": up, "lower_band": low}
    if price < low:
        return "PUT", {"price": price, "upper_band": up, "lower_band": low}
    return None, {"price": price, "upper_band": up, "lower_band": low, "reason": "inside bands"}


def pole_position_signal(df):
    close = df["close"]

    ema_fast = ind.ema(close, config.EMA_FAST).iloc[-1]
    ema_slow = ind.ema(close, config.EMA_SLOW).iloc[-1]
    rsi_val = ind.rsi(close, config.RSI_PERIOD).iloc[-1]
    cci_val = ind.cci(df, config.CCI_PERIOD).iloc[-1]
    bb_upper, bb_mid, bb_lower = ind.bollinger_bands(close, config.BB_PERIOD, config.BB_STD)
    price = close.iloc[-1]

    score = 0
    votes = {}

    if ema_fast > ema_slow:
        score += 1; votes["ema"] = 1
    elif ema_fast < ema_slow:
        score -= 1; votes["ema"] = -1
    else:
        votes["ema"] = 0

    if rsi_val >= config.RSI_UP:
        score += 1; votes["rsi"] = 1
    elif rsi_val <= config.RSI_DOWN:
        score -= 1; votes["rsi"] = -1
    else:
        votes["rsi"] = 0

    if cci_val >= config.CCI_UP:
        score += 1; votes["cci"] = 1
    elif cci_val <= config.CCI_DOWN:
        score -= 1; votes["cci"] = -1
    else:
        votes["cci"] = 0

    if price >= bb_upper.iloc[-1]:
        score += 1; votes["bb"] = 1
    elif price <= bb_lower.iloc[-1]:
        score -= 1; votes["bb"] = -1
    else:
        votes["bb"] = 0

    details = {
        "price": price, "ema_fast": ema_fast, "ema_slow": ema_slow,
        "rsi": rsi_val, "cci": cci_val,
        "bb_upper": bb_upper.iloc[-1], "bb_lower": bb_lower.iloc[-1],
        "score": score, "votes": votes,
    }

    if score >= config.POLE_POSITION_SCORE_THRESHOLD:
        return "CALL", details
    if score <= -config.POLE_POSITION_SCORE_THRESHOLD:
        return "PUT", details
    return None, details


def get_signal(df):
    if config.STRATEGY == "FCB":
        return fcb_signal(df)
    if config.STRATEGY == "POLE_POSITION":
        return pole_position_signal(df)

    fcb_dir, fcb_info = fcb_signal(df)
    pole_dir, pole_info = pole_position_signal(df)

    info = {"fcb": fcb_info, "pole_position": pole_info}
    if fcb_dir is not None and fcb_dir == pole_dir:
        return fcb_dir, info
    return None, info
