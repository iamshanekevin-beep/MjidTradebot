"""
Wrapper around the unofficial IQ Option API ('iqoptionapi').
IQ Option has no official public API — this library is community-maintained
and can break when IQ Option changes their backend. If connect/fetch/trade
calls start failing, this is the file to check/patch first.
"""
import logging
import os
import time
from urllib.parse import urlparse

import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from iqoptionapi.api import IQOptionAPI

import config

log = logging.getLogger("broker")

# ---------------------------------------------------------------------------
# Proxy + host patching
#
# IQ Option's main domain (iqoption.com / auth.iqoption.com) is blocked from
# many server IPs. The rebranded domain auth.iqbroker.com is reachable, and
# ws.iqoption.com still serves the WebSocket. We patch the library to:
#   - route HTTP login  through auth.iqbroker.com
#   - route WebSocket  through ws.iqoption.com
#   - send both through a SOCKS5/HTTP proxy if IQ_PROXY is set
# ---------------------------------------------------------------------------
_PROXY_URL = os.getenv("IQ_PROXY", "").strip()
_PROXY_DICT = None
_WS_PROXY_KW = {}

if _PROXY_URL:
    _parsed = urlparse(_PROXY_URL)
    _PROXY_DICT = {"http": _PROXY_URL, "https": _PROXY_URL}

    _proxy_host = _parsed.hostname
    _proxy_port = _parsed.port
    _proxy_auth = (_parsed.username, _parsed.password) if _parsed.username else None

    # websocket-client run_forever params
    _WS_PROXY_KW["http_proxy_host"] = _proxy_host
    _WS_PROXY_KW["http_proxy_port"] = _proxy_port
    if _proxy_auth:
        _WS_PROXY_KW["http_proxy_auth"] = _proxy_auth
    # socks5:// → proxy_type "socks5", http:// → omit (defaults to HTTP CONNECT)
    if _parsed.scheme in ("socks5", "socks5h"):
        _WS_PROXY_KW["proxy_type"] = "socks5"

    # Inject proxy into IQOptionAPI HTTP session
    _orig_init = IQOptionAPI.__init__
    def _patched_init(self, host, username, password, proxies=None):
        _orig_init(self, host, username, password, proxies or _PROXY_DICT)
    IQOptionAPI.__init__ = _patched_init

    # Patch start_websocket to route through proxy
    import ssl as _ssl
    import threading as _threading
    import iqoptionapi.global_value as _global_value
    try:
        from iqoptionapi.ws.client import WebsocketClient as _WSClient
    except Exception:
        from iqoptionapi.ws.client_old import WebsocketClient as _WSClient

    _orig_start_ws = IQOptionAPI.start_websocket
    def _patched_start_ws(self):
        _global_value.check_websocket_if_connect = None
        _global_value.check_websocket_if_error = False
        _global_value.websocket_error_reason = None
        self.websocket_client = _WSClient(self)
        self.websocket_thread = _threading.Thread(
            target=self.websocket.run_forever,
            kwargs={
                "sslopt": {"check_hostname": False, "cert_reqs": _ssl.CERT_NONE, "ca_certs": "cacert.pem"},
                **_WS_PROXY_KW,
            },
        )
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        while True:
            if _global_value.check_websocket_if_error:
                return False, _global_value.websocket_error_reason
            if _global_value.check_websocket_if_connect == 0:
                return False, "Websocket connection closed."
            elif _global_value.check_websocket_if_connect == 1:
                return True, None
    IQOptionAPI.start_websocket = _patched_start_ws

    log.info("Proxy enabled: %s://%s:%s", _parsed.scheme, _proxy_host, _proxy_port)
else:
    log.info("No proxy configured (IQ_PROXY not set) — connecting directly.")

# ---------------------------------------------------------------------------
# Host redirection — use reachable domains instead of blocked iqoption.com
# ---------------------------------------------------------------------------
_orig_connect = IQ_Option.connect
def _patched_connect(self):
    try:
        self.api.close()
    except Exception:
        pass
    # ws.iqoption.com is reachable (even when iqoption.com is blocked) and
    # serves the same /echo/websocket endpoint.
    self.api = IQOptionAPI("ws.iqoption.com", self.email, self.password)
    self.api.set_session(headers=self.SESSION_HEADER, cookies=self.SESSION_COOKIE)
    check, reason = self.api.connect()
    if check:
        self.re_subscribe_stream()
        import iqoptionapi.global_value as gv
        while gv.global_value.balance_id is None:
            pass
        self.position_change_all("subscribeMessage", gv.global_value.balance_id)
        self.order_changed_all("subscribeMessage")
        self.api.setOptions(1, True)
        return True, None
    return False, reason
IQ_Option.connect = _patched_connect

# Patch login/logout URLs: auth.iqoption.com → auth.iqbroker.com (reachable)
try:
    from iqoptionapi.http.login import Login as _Login
    _Login._post = lambda self, data=None, headers=None: self.api.send_http_request_v2(
        method="POST", url="https://auth.iqbroker.com/api/v2/login",
        data=data, headers=headers)
except Exception as e:
    log.warning("Could not patch Login URL: %s", e)

try:
    from iqoptionapi.http.logout import Logout as _Logout
    _Logout._post = lambda self, data=None, headers=None: self.api.send_http_request_v2(
        method="POST", url="https://auth.iqbroker.com/api/v1.0/logout",
        data=data, headers=headers)
except Exception as e:
    log.warning("Could not patch Logout URL: %s", e)


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

    def get_balance(self):
        self.ensure_connected()
        return self.api.get_balance()

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
