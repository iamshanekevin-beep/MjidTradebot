import logging
import time
from datetime import datetime, timezone

import config
import strategy
from broker import Broker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")


class RiskState:
    def __init__(self):
        self.trades_today = 0
        self.consecutive_losses = 0
        self.pnl_today = 0.0
        self.day = datetime.now(timezone.utc).date()
        self.paused = False

    def reset_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            log.info("New UTC day — resetting daily counters.")
            self.day = today
            self.trades_today = 0
            self.consecutive_losses = 0
            self.pnl_today = 0.0
            self.paused = False

    def can_trade(self):
        self.reset_if_new_day()
        if self.paused:
            return False, "paused after hitting a risk limit"
        if self.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, "hit MAX_TRADES_PER_DAY"
        if self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            self.paused = True
            return False, "hit MAX_CONSECUTIVE_LOSSES"
        if self.pnl_today <= -abs(config.DAILY_LOSS_LIMIT):
            self.paused = True
            return False, "hit DAILY_LOSS_LIMIT"
        return True, None

    def record_trade(self, amount):
        self.trades_today += 1

    def record_result(self, result, amount):
        if result == "win":
            self.consecutive_losses = 0
            self.pnl_today += amount * 0.8  # approximate payout — adjust to your real payout %
        elif result == "loss":
            self.consecutive_losses += 1
            self.pnl_today -= amount


def main():
    # Loud, unmissable startup banner — this is the fix for the $100/trade
    # incident: you should never have to guess what the live settings are.
    log.info("\n" + config.startup_banner())

    if not config.IQ_EMAIL or not config.IQ_PASSWORD:
        log.error("IQ_EMAIL / IQ_PASSWORD are not set. Set them in your platform's Secrets/Variables.")
        return

    broker = Broker()
    risk = RiskState()

    while True:
        try:
            broker.connect()
            break
        except Exception as e:
            log.error("Connection failed (%s). Retrying in 15s...", e)
            time.sleep(15)

    last_candle_ts = None

    while True:
        try:
            df = broker.get_candles_df()
            if df.empty:
                time.sleep(config.POLL_SECONDS)
                continue

            latest_ts = df["timestamp"].iloc[-1]
            if latest_ts == last_candle_ts:
                time.sleep(config.POLL_SECONDS)
                continue
            last_candle_ts = latest_ts

            direction, info = strategy.get_signal(df)

            if direction is None:
                log.info("No signal. %s", _summarize(info))
                time.sleep(config.POLL_SECONDS)
                continue

            log.info("Signal: %s | %s", direction, _summarize(info))

            if not config.AUTO_TRADE:
                log.info("AUTO_TRADE is off — signal logged only, no order placed.")
                time.sleep(config.POLL_SECONDS)
                continue

            can_trade, reason = risk.can_trade()
            if not can_trade:
                log.warning("Trade skipped — risk control: %s", reason)
                time.sleep(config.POLL_SECONDS)
                continue

            success, order_id = broker.place_trade(direction)
            risk.record_trade(config.TRADE_AMOUNT)

            if not success:
                log.error("Trade failed: %s", order_id)
                time.sleep(config.POLL_SECONDS)
                continue

            log.info("Trade placed: %s amount=%s order_id=%s", direction, config.TRADE_AMOUNT, order_id)

            time.sleep(config.EXPIRATION_MINUTES * 60 + 5)
            result = broker.get_trade_result(order_id)
            risk.record_result(result, config.TRADE_AMOUNT)
            log.info("Trade result: %s | daily P&L (approx): %.2f", result, risk.pnl_today)

        except Exception as e:
            log.error("Error in main loop: %s. Reconnecting in 15s...", e)
            time.sleep(15)
            try:
                broker.connect()
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)


def _summarize(info):
    if not isinstance(info, dict):
        return info
    keys_of_interest = ("price", "score", "reason", "upper_band", "lower_band")
    parts = []
    for k in keys_of_interest:
        if k in info:
            v = info[k]
            parts.append(f"{k}={v:.5f}" if isinstance(v, float) else f"{k}={v}")
    if "fcb" in info:
        parts.append(f"fcb={_summarize(info['fcb'])}")
    if "pole_position" in info:
        parts.append(f"pole_position={_summarize(info['pole_position'])}")
    return " ".join(parts) if parts else str(info)


if __name__ == "__main__":
    main()
