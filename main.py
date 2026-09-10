import logging
import time
from datetime import datetime, timezone

import config
import strategy
from broker import Broker
from bot_state import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")


class RiskState:
    def __init__(self):
        self.trades_today = 0
        self.consecutive_losses = 0
        self.pnl_today = 0.0
        self.day = datetime.now(timezone.utc).date()

    def reset_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            log.info("New UTC day — resetting daily counters.")
            self.day = today
            self.trades_today = 0
            self.consecutive_losses = 0
            self.pnl_today = 0.0

    def can_trade(self):
        self.reset_if_new_day()
        if self.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, "hit MAX_TRADES_PER_DAY"
        if self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            return False, "hit MAX_CONSECUTIVE_LOSSES"
        if self.pnl_today <= -abs(config.DAILY_LOSS_LIMIT):
            return False, "hit DAILY_LOSS_LIMIT"
        return True, None

    def record_trade(self, amount):
        self.trades_today += 1

    def record_result(self, result, amount):
        if result == "win":
            self.consecutive_losses = 0
            self.pnl_today += amount * 0.8
        elif result == "loss":
            self.consecutive_losses += 1
            self.pnl_today -= amount


def _summarize(info):
    if not isinstance(info, dict):
        return str(info)
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


def run_bot():
    """Main bot loop — called from dashboard.py in a background thread."""
    log.info("\n" + config.startup_banner())

    if not config.IQ_EMAIL or not config.IQ_PASSWORD:
        log.error("IQ_EMAIL / IQ_PASSWORD are not set.")
        state.update(connected=False, last_signal_text="Missing IQ Option credentials.")
        return

    broker = Broker()
    risk = RiskState()

    # Connect — back off instead of logging the same failure every 15s.
    delay = 15
    attempt = 0
    while True:
        try:
            broker.connect()
            balance = broker.get_balance()
            state.update(connected=True, balance=balance,
                         last_signal_text="Connected — scanning %s" % config.PAIR)
            log.info("Account balance: %s", balance)
            break
        except Exception as e:
            attempt += 1
            text = str(e)
            if "SSL" in text or "timed out" in text or "Max retries" in text:
                status = "IQ Option is not reachable from this server (network blocked)."
            elif "invalid_credentials" in text:
                status = "IQ Option rejected the login credentials."
            else:
                status = "Connection failed: %s" % text
            if attempt == 1:
                log.error("%s Retrying with backoff.", status)
            else:
                log.info("Still not connected (attempt %d) — next try in %ds.", attempt, delay)
            state.update(connected=False, last_signal_text=status)
            time.sleep(delay)
            delay = min(delay * 2, 300)

    last_candle_ts = None

    # Main loop
    while True:
        try:
            if state.paused:
                time.sleep(config.POLL_SECONDS)
                continue

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

            signal_text = "%s | %s" % (config.PAIR, _summarize(info))
            state.update(last_signal_text=signal_text, active_pair=config.PAIR)
            state.add_signal({
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                "pair": config.PAIR,
                "direction": direction,
                "summary": signal_text,
            })

            if direction is None:
                log.info("No signal. %s", _summarize(info))
                time.sleep(config.POLL_SECONDS)
                continue

            log.info("Signal: %s | %s", direction, _summarize(info))

            if not state.auto_trade:
                log.info("AUTO_TRADE is off — signal logged only.")
                time.sleep(config.POLL_SECONDS)
                continue

            can_trade, reason = risk.can_trade()
            if not can_trade:
                log.warning("Trade skipped — risk control: %s", reason)
                time.sleep(config.POLL_SECONDS)
                continue

            amount = state.trade_amount
            success, order_id = broker.place_trade(direction, amount=amount)
            risk.record_trade(amount)

            trade_entry = {
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                "direction": direction,
                "amount": amount,
                "pair": config.PAIR,
                "expiration": config.EXPIRATION_MINUTES,
                "order_id": order_id if success else None,
                "result": "pending" if success else "failed",
                "pnl": None,
            }
            state.add_trade(trade_entry)

            if not success:
                log.error("Trade failed: %s", order_id)
                time.sleep(config.POLL_SECONDS)
                continue

            log.info("Trade placed: %s amount=%s order_id=%s", direction, amount, order_id)

            time.sleep(config.EXPIRATION_MINUTES * 60 + 5)
            result = broker.get_trade_result(order_id)
            risk.record_result(result, amount)

            trade_entry["result"] = result
            trade_entry["pnl"] = round(risk.pnl_today, 2)

            completed = [t for t in state.trades if t.get("result") in ("win", "loss")]
            wins = [t for t in completed if t["result"] == "win"]
            state.update(
                daily_pnl=risk.pnl_today,
                trades_today=risk.trades_today,
                consecutive_losses=risk.consecutive_losses,
                win_rate=(len(wins) / len(completed) * 100) if completed else 0.0,
            )
            state.pnl_history.append(risk.pnl_today)

            log.info("Trade result: %s | daily P&L (approx): %.2f", result, risk.pnl_today)

        except Exception as e:
            log.error("Error in main loop: %s. Reconnecting in 15s...", e)
            state.update(connected=False, last_signal_text="Error: %s" % e)
            time.sleep(15)
            try:
                broker.connect()
                bal = broker.get_balance()
                state.update(connected=True, balance=bal)
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)


if __name__ == "__main__":
    run_bot()
