"""
Thread-safe shared state between the trading bot and the web dashboard.
Both the bot thread (main.run_bot) and the Flask server (dashboard) import
the singleton `state` instance below.
"""
import threading
from collections import deque

import config


class BotState:
    def __init__(self):
        self._lock = threading.Lock()

        # Connection / status
        self.connected = False
        self.paused = False

        # Live-mutable controls (initialised from config defaults)
        self.auto_trade = config.AUTO_TRADE
        self.account_type = config.ACCOUNT_TYPE
        self.trade_amount = config.TRADE_AMOUNT
        self.daily_loss_limit = config.DAILY_LOSS_LIMIT

        # Scanning
        self.pairs = list(config.PAIRS)
        self.active_pair = None
        self.scan_interval = config.SCAN_INTERVAL_SECONDS

        # Cooldown
        self.cooldown_until = None       # epoch timestamp or None
        self.cooldown_remaining = 0      # seconds remaining (for display)
        self.cooldown_minutes = config.COOLDOWN_MINUTES

        # Static settings (display only)
        self.strategy = config.STRATEGY
        self.max_trades_per_day = config.MAX_TRADES_PER_DAY
        self.max_consecutive_losses = config.MAX_CONSECUTIVE_LOSSES
        self.timeframe_seconds = config.TIMEFRAME_SECONDS
        self.expiration_minutes = config.EXPIRATION_MINUTES

        # Activity feeds
        self.latest_signal = None
        self.signals = deque(maxlen=50)
        self.trades = deque(maxlen=50)

        # Performance metrics
        self.daily_pnl = 0.0
        self.balance = 0.0
        self.win_rate = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.pnl_history = deque(maxlen=120)

        # Latest log line for the footer
        self.last_signal_text = "Waiting for first signal…"

    # -- writers (called from bot thread) --

    def add_signal(self, entry):
        with self._lock:
            self.signals.append(entry)
            self.latest_signal = entry

    def add_trade(self, entry):
        with self._lock:
            self.trades.append(entry)

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    # -- reader (called from Flask thread) --

    def snapshot(self):
        with self._lock:
            return {
                "connected": self.connected,
                "paused": self.paused,
                "auto_trade": self.auto_trade,
                "account_type": self.account_type,
                "trade_amount": self.trade_amount,
                "daily_loss_limit": self.daily_loss_limit,
                "pairs": list(self.pairs),
                "active_pair": self.active_pair,
                "scan_interval": self.scan_interval,
                "cooldown_until": self.cooldown_until,
                "cooldown_remaining": self.cooldown_remaining,
                "cooldown_minutes": self.cooldown_minutes,
                "strategy": self.strategy,
                "max_trades_per_day": self.max_trades_per_day,
                "max_consecutive_losses": self.max_consecutive_losses,
                "timeframe_seconds": self.timeframe_seconds,
                "expiration_minutes": self.expiration_minutes,
                "latest_signal": self.latest_signal,
                "signals": list(self.signals),
                "trades": list(self.trades),
                "daily_pnl": round(self.daily_pnl, 2),
                "balance": round(self.balance, 2),
                "win_rate": round(self.win_rate, 1),
                "trades_today": self.trades_today,
                "consecutive_losses": self.consecutive_losses,
                "pnl_history": list(self.pnl_history),
                "last_signal_text": self.last_signal_text,
            }


state = BotState()
