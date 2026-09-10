"""
Wrapper around the unofficial IQ Option API ('iqoptionapi').
IQ Option has no official public API — this library is community-maintained
and can break when IQ Option changes their backend. If connect/fetch/trade
calls start failing, this is the file to check/patch first.
"""
import logging
import time

import pandas as pd
from iqoptionapi.stable_api import IQ_Option

import config

log = logging.getLogger("broker")


class Broker:
    def __init__(self):
        self.api = None

    def connect(self):
        self.api = IQ_Option(config.IQ_EMAIL, config.IQ_PASSWORD)
        check, reason = self.api.connect()
        if not check:
            raise ConnectionError(f"IQ Option login failed: {reason}")
        self.api.change_balance(config.ACCOUNT_TYPE)
        log.info("Connected to IQ Option (%s account)", config.ACCOUNT_TYPE)
        return True

    def ensure_connected(self):
        if self.api is None or not self.api.check_connect():
            log.warning("Not connected — reconnecting...")
            self.connect()

    def get_candles_df(self, pair=None, timeframe_seconds=None, count=None) -> pd.DataFrame:
        pair = pair or config.PAIR
        timeframe_seconds = timeframe_seconds or config.TIMEFRAME_SECONDS
        count = count or config.CANDLE_COUNT

        self.ensure_connected()
        raw = self.api.get_candles(pair, timeframe_seconds, count, time.time())
        df = pd.DataFrame(raw)
        df = df.rename(columns={"min": "low", "max": "high", "from": "timestamp"})
        df = df[["timestamp", "open", "high", "low", "close"]]
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def place_trade(self, direction: str, amount=None, pair=None, expiration_minutes=None):
        pair = pair or config.PAIR
        amount = amount or config.TRADE_AMOUNT
        expiration_minutes = expiration_minutes or config.EXPIRATION_MINUTES
        action = "call" if direction == "CALL" else "put"

        self.ensure_connected()
        log.info("Placing trade: %s %s amount=%s expiry=%smin", action, pair, amount, expiration_minutes)

        try:
            check, order_id = self.api.buy(amount, pair, action, expiration_minutes)
            if check:
                return True, order_id
            return False, f"buy() returned False: {order_id}"
        except Exception as e:
            log.warning("Classic buy() failed (%s), trying digital spot...", e)

        try:
            check, order_id = self.api.buy_digital_spot(pair, amount, action, expiration_minutes)
            return check, order_id
        except Exception as e:
            return False, f"digital spot buy failed: {e}"

    def get_trade_result(self, order_id, timeout=5):
        try:
            result = self.api.check_win_v4(order_id) if hasattr(self.api, "check_win_v4") else None
            if result is None:
                return "unknown"
            profit, status = result if isinstance(result, tuple) else (result, None)
            if profit is not None and profit > 0:
                return "win"
            if profit is not None and profit < 0:
                return "loss"
            return "unknown"
        except Exception as e:
            log.warning("Could not fetch trade result: %s", e)
            return "unknown"
