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

    def reset_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            log.info("New UTC day — resetting daily counters.")
            self.day = today
            self.trades_today = 0
            self.consecutive_losses = 0
            self.pnl_today = 0.0

    def can_trade(self, daily_loss_limit=None):
        self.reset_if_new_day()
        if self.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, "hit MAX_TRADES_PER_DAY"
        limit = daily_loss_limit if daily_loss_limit is not None else config.DAILY_LOSS_LIMIT
        if self.pnl_today <= -abs(limit):
            return False, "hit DAILY_LOSS_LIMIT"
        return True, None

    def record_trade(self, amount):
        self.trades_today += 1

    def record_result(self, result, amount):
        if result == "win":
            self.consecutive_losses = 0
            self.pnl_today += amount * 0.8  # approximate payout
        elif result == "loss":
            self.consecutive_losses += 1
            self.pnl_today -= amount


# ---------------------------------------------------------------------------
# Helpers for sharing signal/trade data with the dashboard
# ---------------------------------------------------------------------------

def _to_native(v):
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


def _signal_entry(direction, info, pair):
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "pair": pair,
        "direction": direction,
        "price": _extract(info, "price"),
        "upper_band": _extract(info, "upper_band"),
        "lower_band": _extract(info, "lower_band"),
        "candle_dir": _extract(info, "candle_dir"),
        "reason": _extract(info, "reason"),
        "summary": "%s | %s" % (pair, _summarize(info)),
    }


def _summarize(info):
    if not isinstance(info, dict):
        return str(info)
    keys_of_interest = ("price", "reason", "upper_band", "lower_band", "candle_dir")
    parts = []
    for k in keys_of_interest:
        if k in info:
            v = info[k]
            parts.append(f"{k}={v:.5f}" if isinstance(v, float) else f"{k}={v}")
    return " ".join(parts) if parts else str(info)


# ---------------------------------------------------------------------------
# Main bot loop (callable from dashboard.py in a background thread)
#
# Scans ONE pair per cycle, rotating through all pairs every 0.5s.
# This keeps the API request rate at ~2 req/s (well within IQ Option's limits)
# while still covering all 10 pairs every 5 seconds.
# ---------------------------------------------------------------------------

def run_bot():
    log.info("\n" + config.startup_banner())

    state.update(
        auto_trade=config.AUTO_TRADE,
        account_type=config.ACCOUNT_TYPE,
        trade_amount=config.TRADE_AMOUNT,
        daily_loss_limit=config.DAILY_LOSS_LIMIT,
        pairs=list(config.PAIRS),
        scan_interval=config.SCAN_INTERVAL_SECONDS,
        cooldown_minutes=config.COOLDOWN_MINUTES,
    )

    if not config.IQ_EMAIL or not config.IQ_PASSWORD:
        log.error("IQ_EMAIL / IQ_PASSWORD are not set.")
        state.update(connected=False, last_signal_text="Missing IQ Option credentials.")
        return

    broker = Broker()
    risk = RiskState()

    # --- Connect ---
    while True:
        try:
            broker.connect()
            balance = broker.get_balance()
            state.update(
                connected=True,
                balance=balance,
                last_signal_text="Connected — scanning %d pairs every %ss" % (len(config.PAIRS), config.SCAN_INTERVAL_SECONDS),
            )
            log.info("Account balance: %s", balance)
            break
        except Exception as e:
            log.error("Connection failed (%s). Retrying in 15s...", e)
            state.update(connected=False, last_signal_text="Connection failed: %s" % e)
            time.sleep(15)

    # --- State ---
    last_candle_ts = {}        # {pair: last processed candle timestamp}
    pending_trade = None        # {order_id, pair, direction, amount, trade_entry, complete_at}
    cooldown_until = None       # epoch timestamp or None
    last_balance_check = 0.0
    pair_index = 0
    scan_interval = config.SCAN_INTERVAL_SECONDS

    # --- Main loop ---
    while True:
        try:
            now = time.time()

            # Dashboard pause
            if state.paused:
                time.sleep(scan_interval)
                continue

            # --- Cooldown management ---
            if cooldown_until:
                if now >= cooldown_until:
                    cooldown_until = None
                    risk.consecutive_losses = 0
                    state.update(cooldown_until=None, cooldown_remaining=0, consecutive_losses=0,
                                 last_signal_text="Cooldown ended — resuming trading")
                    log.info("Cooldown ended — resuming trading")
                else:
                    remaining = int(cooldown_until - now)
                    state.update(cooldown_remaining=remaining)

            # --- Check pending trade result ---
            if pending_trade and now >= pending_trade["complete_at"]:
                result = broker.get_trade_result(pending_trade["order_id"])
                risk.record_result(result, pending_trade["amount"])

                pending_trade["trade_entry"]["result"] = result
                pending_trade["trade_entry"]["pnl"] = round(risk.pnl_today, 2)

                completed = [t for t in state.trades if t.get("result") in ("win", "loss")]
                wins = [t for t in completed if t["result"] == "win"]
                state.update(
                    daily_pnl=risk.pnl_today,
                    trades_today=risk.trades_today,
                    consecutive_losses=risk.consecutive_losses,
                    win_rate=(len(wins) / len(completed) * 100) if completed else 0.0,
                )
                state.pnl_history.append(risk.pnl_today)

                log.info("Trade result: %s on %s | daily P&L: %.2f", result, pending_trade["pair"], risk.pnl_today)

                # Trigger cooldown after 3 consecutive losses
                if risk.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
                    cooldown_until = now + config.COOLDOWN_MINUTES * 60
                    state.update(cooldown_until=cooldown_until,
                                 last_signal_text="3 consecutive losses — %d-min cooldown" % config.COOLDOWN_MINUTES)
                    log.info("3 consecutive losses — %d-minute cooldown started", config.COOLDOWN_MINUTES)

                pending_trade = None

            # --- Refresh balance periodically ---
            if now - last_balance_check > 30:
                try:
                    bal = broker.get_balance()
                    state.update(balance=bal)
                    last_balance_check = now
                except Exception:
                    pass

            # --- Scan ONE pair this cycle (rotate through all pairs) ---
            pair = config.PAIRS[pair_index % len(config.PAIRS)]
            pair_index += 1

            in_cooldown = cooldown_until is not None and now < cooldown_until
            can_trade = (state.auto_trade and not pending_trade and not in_cooldown)

            if can_trade:
                ok, reason = risk.can_trade(state.daily_loss_limit)
                if not ok:
                    can_trade = False

            try:
                df = broker.get_candles_df(pair=pair)
                if not df.empty:
                    latest_ts = df["timestamp"].iloc[-1]
                    if latest_ts != last_candle_ts.get(pair):
                        last_candle_ts[pair] = latest_ts

                        direction, info = strategy.get_signal(df)
                        entry = _signal_entry(direction, info, pair)
                        state.add_signal(entry)
                        state.update(last_signal_text=entry["summary"], active_pair=pair)

                        if direction is None:
                            log.info("No clean signal on %s. %s", pair, _summarize(info))
                        else:
                            log.info("★ Clean signal on %s: %s | %s", pair, direction, _summarize(info))

                            if can_trade:
                                # Place trade — base stake only, no martingale
                                amount = state.trade_amount
                                success, order_id = broker.place_trade(direction, amount=amount, pair=pair)
                                risk.record_trade(amount)

                                trade_entry = {
                                    "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                                    "direction": direction,
                                    "amount": amount,
                                    "pair": pair,
                                    "expiration": config.EXPIRATION_MINUTES,
                                    "order_id": order_id if success else None,
                                    "result": "pending" if success else "failed",
                                    "pnl": None,
                                }
                                state.add_trade(trade_entry)

                                if success:
                                    log.info("✓ Trade placed: %s %s amount=%s order_id=%s", direction, pair, amount, order_id)
                                    pending_trade = {
                                        "order_id": order_id,
                                        "pair": pair,
                                        "direction": direction,
                                        "amount": amount,
                                        "trade_entry": trade_entry,
                                        "complete_at": time.time() + config.EXPIRATION_MINUTES * 60 + 5,
                                    }
                                else:
                                    log.error("Trade failed on %s: %s", pair, order_id)
                            else:
                                reason = ("auto_trade off" if not state.auto_trade
                                          else "pending trade" if pending_trade
                                          else "cooldown" if in_cooldown
                                          else "risk control")
                                log.info("  → not trading (%s), signal logged only", reason)

            except Exception as e:
                state.update(connected=False)
                log.warning("Error scanning %s: %s", pair, e)
                # ensure_connected() in broker handles backoff; don't hammer reconnect here
                try:
                    broker.ensure_connected()
                    state.update(connected=True)
                except Exception:
                    pass

            time.sleep(scan_interval)

        except Exception as e:
            log.error("Error in main loop: %s. Reconnecting in 15s...", e)
            state.update(connected=False, last_signal_text="Error: %s. Reconnecting…" % e)
            pending_trade = None
            time.sleep(15)
            try:
                broker.connect()
                bal = broker.get_balance()
                state.update(connected=True, balance=bal)
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)


if __name__ == "__main__":
    run_bot()
