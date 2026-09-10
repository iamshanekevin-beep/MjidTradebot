"""
Flask web dashboard for the IQ Option trading bot.
Serves the dashboard UI on port 3000 and starts the bot in a background thread.
"""
import logging
import threading

from flask import Flask, jsonify, render_template, request

import config
from bot_state import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dashboard")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

_bot_started = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    return jsonify(state.snapshot())


@app.route("/api/control", methods=["POST"])
def set_control():
    data = request.get_json(silent=True) or {}

    if "auto_trade" in data:
        state.auto_trade = bool(data["auto_trade"])
        log.info("AUTO_TRADE set to %s via dashboard", state.auto_trade)

    if "paused" in data:
        state.paused = bool(data["paused"])
        log.info("Bot %s via dashboard", "paused" if state.paused else "resumed")

    if "trade_amount" in data:
        try:
            state.trade_amount = float(data["trade_amount"])
            log.info("TRADE_AMOUNT set to %s via dashboard", state.trade_amount)
        except (TypeError, ValueError):
            pass

    if "account_type" in data:
        val = str(data["account_type"]).upper()
        if val in ("PRACTICE", "REAL"):
            state.account_type = val
            config.ACCOUNT_TYPE = val  # picked up on next reconnect
            log.info("ACCOUNT_TYPE set to %s via dashboard (applies on reconnect)", val)

    return jsonify(state.snapshot())


def start():
    """Start the bot in a daemon thread, then launch Flask on port 3000."""
    global _bot_started
    if not _bot_started:
        _bot_started = True
        from main import run_bot

        t = threading.Thread(target=run_bot, daemon=True, name="bot")
        t.start()
        log.info("Bot background thread started")

    app.run(host="0.0.0.0", port=3000, threaded=True)


if __name__ == "__main__":
    start()
