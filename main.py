import logging
import math
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


# ---------------------------------------------------------------------------
# Helpers for sharing signal/trade data with the dashboard
# ---------------------------------------------------------------------------

def _to_native(v):
    """Convert numpy/pandas scalars to JSON-safe Python natives."""
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 5) if not (math.isnan(v) or math.isinf(v)) else None
    if isinstance(v, (int, str, bool)):
        return v
    return str(v)


def _extract(info, key):
    """Recursively look for *key* in a (possibly nested) signal info dict."""
    if not isinstance(info, dict):
        return None
    if key in info:
        return _to_native(info[key])
    for v in info.values():
        if isinstance(v, dict):
            found = _extract(v, key)
            if found is not None:
                return found
    return None


def _signal_entry(direction, info):
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "direction": direction,
        "price": _extract(info, "price"),
        "score": _extract(info, "score"),
        "reason": _extract(info, "reason"),
        "upper_band": _extract(info, "upper_band"),
        "lower_band": _extract(info, "lower_band"),
        "summary": _summarize(info),
    }


# ---------------------------------------------------------------------------
# Main bot loop (callable from dashboard.py in a background thread)
# ---------------------------------------------------------------------------

def run_bot():
    log.info("\n" + config.startup_banner())

    # Sync state from config at startup
    state.update(
        auto_trade=config.AUTO_TRADE,
        account_type=config.ACCOUNT_TYPE,
        trade_amount=config.TRADE_AMOUNT,
    )

    if not config.IQ_EMAIL or not config.IQ_PASSWORD:
        log.error("IQ_EMAIL / IQ_PASSWORD are not set. Set them in your platform's Secrets/Variables.")
        state.update(connected=False, last_signal_text="Missing IQ Option credentials.")
        return

    broker = Broker()
    risk = RiskState()

    while True:
        try:
            broker.connect()
            state.update(connected=True, last_signal_text="Connected to IQ Option (%s account)" % config.ACCOUNT_TYPE)
            break
        except Exception as e:
            log.error("Connection failed (%s). Retrying in 15s...", e)
            state.update(connected=False, last_signal_text="Connection failed: %s" % e)
            time.sleep(15)

    last_candle_ts = None

    while True:
        try:
            # Allow pause/resume from the dashboard
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

            # Push signal to dashboard
            entry = _signal_entry(direction, info)
            state.add_signal(entry)
            state.update(last_signal_text=entry["summary"])

            if direction is None:
                log.info("No signal. %s", _summarize(info))
                time.sleep(config.POLL_SECONDS)
                continue

            log.info("Signal: %s | %s", direction, _summarize(info))

            # Read auto_trade from state (allows runtime toggle via dashboard)
            if not state.auto_trade:
                log.info("AUTO_TRADE is off — signal logged only, no order placed.")
                time.sleep(config.POLL_SECONDS)
                continue

            can_trade, reason = risk.can_trade()
            if not can_trade:
                log.warning("Trade skipped — risk control: %s", reason)
                state.update(last_signal_text="Trade skipped — risk control: %s" % reason)
                time.sleep(config.POLL_SECONDS)
                continue

            # Read amount from state (allows runtime change via dashboard)
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

            # Update the trade entry in-place (deque holds a reference to same dict)
            trade_entry["result"] = result
            trade_entry["pnl"] = round(risk.pnl_today, 2)

            # Sync performance metrics to state
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
            state.update(connected=False, last_signal_text="Error: %s. Reconnecting…" % e)
            time.sleep(15)
            try:
                broker.connect()
                state.update(connected=True)
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)


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


if __name__ == "__main__":
    run_bot()
