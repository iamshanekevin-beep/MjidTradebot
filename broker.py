"""
Wrapper around the unofficial IQ Option API ('iqoptionapi').
IQ Option has no official public API — this library is community-maintained
and can break when IQ Option changes their backend. If connect/fetch/trade
calls start failing, this is the file to check/patch first.
"""
import gc
import logging
import os
import time
from urllib.parse import urlparse

import pandas as pd
from iqoptionapi.stable_api import IQ_Option, OP_code

import config

log = logging.getLogger("broker")


def _parse_proxy():
    """Read IQ_PROXY env var and return proxy dict or None.

    Supports these formats:
      - host:port:user:pass   (colon-separated, no scheme, no @)
      - user:pass@host:port   (no scheme)
      - http://user:pass@host:port
      - socks5://host:port
      - socks5://user:pass@host:port
    """
    raw = os.environ.get("IQ_PROXY", "").strip()
    if not raw:
        return None

    scheme = "http"

    # Strip scheme prefix (handles both "scheme://" and "scheme:")
    if "://" in raw:
        scheme, raw = raw.split("://", 1)
        raw = raw.strip()
    elif ":" in raw and raw.split(":", 1)[0].lower() in ("http", "https", "socks5", "socks4"):
        scheme, raw = raw.split(":", 1)
        raw = raw.strip()

    # If there's an @ sign, split into two parts and figure out which is auth
    if "@" in raw:
        left, right = raw.rsplit("@", 1)

        def _try_host_port(s):
            """Parse 'host:port' — return (host, port) or None."""
            if ":" not in s:
                return None
            h, p = s.rsplit(":", 1)
            try:
                return (h, int(p))
            except ValueError:
                return None

        def _try_auth(s):
            """Parse 'user:pass' or 'user' — return (user, pass) or None."""
            if ":" in s:
                u, p = s.split(":", 1)
                return (u, p)
            if s:
                return (s, "")
            return None

        # Try user:pass@host:port (standard)
        auth = _try_auth(left)
        hp = _try_host_port(right)
        if auth and hp:
            return {"scheme": scheme, "host": hp[0], "port": hp[1], "auth": auth}

        # Try host:port@user:pass (reversed)
        hp = _try_host_port(left)
        auth = _try_auth(right)
        if hp and auth:
            return {"scheme": scheme, "host": hp[0], "port": hp[1], "auth": auth}

        log.error("IQ_PROXY with @ could not be parsed")
        return None

    # No @ sign — could be host:port:user:pass or host:port
    colons = raw.count(":")
    if colons >= 3:
        # host:port:user:pass
        parts = raw.split(":")
        host, port_str, user, password = parts[0], parts[1], parts[2], ":".join(parts[3:])
    elif colons == 1:
        # host:port
        host, port_str = raw.split(":", 1)
        user, password = "", ""
    elif colons == 0:
        host, port_str = raw, ""
        user, password = "", ""
    else:
        log.error("IQ_PROXY could not be parsed (colons=%d)", colons)
        return None

    try:
        port = int(port_str) if port_str else (1080 if scheme.startswith("socks") else 8080)
    except ValueError:
        log.error("IQ_PROXY invalid port '%s'", port_str)
        return None

    auth = (user, password) if user else None
    return {"scheme": scheme, "host": host, "port": port, "auth": auth}


_PROXY = _parse_proxy()


def _patch_library_for_proxy():
    """Patch iqoptionapi + websocket-client at class level to tunnel through IQ_PROXY."""
    if not _PROXY:
        return

    p = _PROXY
    # socks5h makes the PROXY resolve DNS, which is required when the local
    # resolver cannot see the target host.
    req_scheme = "socks5h" if p["scheme"] == "socks5" else p["scheme"]
    proxy_url = "%s://%s:%d" % (req_scheme, p["host"], p["port"])
    if p["auth"]:
        proxy_url = "%s://%s:%s@%s:%d" % (
            req_scheme, p["auth"][0], p["auth"][1], p["host"], p["port"])

    log.info("Proxy configured: %s://%s:%d", p["scheme"], p["host"], p["port"])

    # 1) Patch IQOptionAPI.__init__ to set proxy on the requests session.
    #    The library sets trust_env=False, so env-var proxies are ignored —
    #    we must set session.proxies explicitly.
    from iqoptionapi.api import IQOptionAPI
    _orig_init = IQOptionAPI.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.session.proxies = {"http": proxy_url, "https": proxy_url}

    IQOptionAPI.__init__ = _patched_init

    # 2) Patch WebSocketApp.run_forever to inject proxy kwargs.  The library
    #    calls run_forever(sslopt=...) — we add http_proxy_host/port before it runs.
    from websocket import WebSocketApp
    _orig_rf = WebSocketApp.run_forever

    def _patched_run_forever(self, **kwargs):
        kwargs.setdefault("http_proxy_host", p["host"])
        kwargs.setdefault("http_proxy_port", p["port"])
        if p["scheme"].startswith("socks"):
            kwargs.setdefault("proxy_type", "socks5" if "5" in p["scheme"] else "socks4")
        if p["auth"]:
            kwargs.setdefault("http_proxy_auth", p["auth"])
        return _orig_rf(self, **kwargs)

    WebSocketApp.run_forever = _patched_run_forever


_patch_library_for_proxy()


# The library hardcodes "iqoption.com" inside IQ_Option.connect(); this lets us
# point it at any of config.IQ_HOSTS (they serve the same API).
_HOST_OVERRIDE = None

# Host pairs proven unreachable from this network; skipped on later attempts.
_UNROUTABLE_HOSTS = set()


def _patch_library_host():
    from iqoptionapi.api import IQOptionAPI
    _orig_init = IQOptionAPI.__init__

    def _patched_init(self, host, *args, **kwargs):
        _orig_init(self, host, *args, **kwargs)
        if _HOST_OVERRIDE:
            api_host, ws_host, _ = _HOST_OVERRIDE
            self.https_url = "https://%s/api" % api_host
            self.wss_url = "wss://%s/echo/websocket" % ws_host

    IQOptionAPI.__init__ = _patched_init

    # The Login resource hardcodes auth.iqoption.com, which some proxies cannot
    # reach — send it to the auth host of the pair we're currently trying.
    from iqoptionapi.http.login import Login
    _orig_post = Login._post

    def _patched_post(self, data=None, headers=None):
        if _HOST_OVERRIDE:
            return self.api.send_http_request_v2(
                method="POST",
                url="https://%s/api/v2/login" % _HOST_OVERRIDE[2],
                data=data, headers=headers)
        return _orig_post(self, data=data, headers=headers)

    Login._post = _patched_post


_patch_library_host()


class Broker:
    def __init__(self):
        self.api = None
        self._calls_since_reconnect = 0
        # IQ Option websocket breaks after ~3 get_candles calls for different
        # pairs.  We reconnect after every 2 to stay safely under the limit.
        self._MAX_CALLS = 2
        # Exponential backoff for reconnection attempts
        self._last_reconnect_attempt = 0.0
        self._reconnect_delay = 5.0
        self._MAX_RECONNECT_DELAY = 60.0

    def connect(self):
        # Properly close old connection so the server releases the session
        if self.api is not None:
            try:
                del self.api
            except Exception:
                pass
            self.api = None
            gc.collect()
            time.sleep(1)  # let the server close the old websocket session

        global _HOST_OVERRIDE

        def _split(entry):
            parts = entry.split("|")
            api_host = parts[0]
            ws_host = parts[1] if len(parts) > 1 and parts[1] else api_host
            auth_host = parts[2] if len(parts) > 2 and parts[2] else "auth.iqoption.com"
            return (api_host, ws_host, auth_host)

        hosts = [_split(h) for h in (config.IQ_HOSTS or ["iqoption.com"])]
        # Skip hosts already found unroutable (e.g. blocked by the proxy), but
        # never skip all of them.
        routable = [h for h in hosts if h not in _UNROUTABLE_HOSTS]
        if routable:
            hosts = routable
        # Prefer the host pair that worked last time
        if _HOST_OVERRIDE in hosts:
            hosts = [_HOST_OVERRIDE] + [h for h in hosts if h != _HOST_OVERRIDE]

        last_reason = None
        for host in hosts:
            _HOST_OVERRIDE = host
            self.api = IQ_Option(config.IQ_EMAIL, config.IQ_PASSWORD)
            try:
                check, reason = self.api.connect()
            except Exception as e:
                check, reason = False, str(e)
            if check:
                break
            last_reason = reason
            # A network-level failure means this host isn't reachable from here
            # at all (blocked domain / proxy route) — stop retrying it.
            if any(s in str(reason) for s in ("Host unreachable", "unreachable",
                                              "Name or service not known",
                                              "Connection closed unexpectedly")):
                if host not in _UNROUTABLE_HOSTS:
                    log.warning("Host %s is unreachable from this network — skipping it", host[0])
                    _UNROUTABLE_HOSTS.add(host)
            else:
                log.warning("Login via %s failed: %s", host[0], reason)
        else:
            _HOST_OVERRIDE = None
            raise ConnectionError(f"IQ Option login failed: {last_reason}")

        log.info("Logged in via %s (ws %s)", _HOST_OVERRIDE[0], _HOST_OVERRIDE[1])
        self.api.change_balance(config.ACCOUNT_TYPE)
        self._calls_since_reconnect = 0
        self._patch_get_candles()
        log.info("Connected to IQ Option (%s account)", config.ACCOUNT_TYPE)
        return True

    def get_balance(self):
        self.ensure_connected()
        return self.api.get_balance()

    def ensure_connected(self):
        if self.api is None or not self.api.check_connect():
            now = time.time()
            elapsed = now - self._last_reconnect_attempt
            if elapsed < self._reconnect_delay:
                raise ConnectionError(
                    f"Reconnect on cooldown ({self._reconnect_delay - elapsed:.0f}s remaining)"
                )
            self._last_reconnect_attempt = now
            try:
                self.connect()
                self._reconnect_delay = 5.0  # reset on success
            except Exception:
                self._reconnect_delay = min(self._reconnect_delay * 2, self._MAX_RECONNECT_DELAY)
                raise

    def _patch_get_candles(self):
        """Monkey-patch the library's get_candles to prevent infinite retry loops.

        The original implementation has ``while True`` with no retry limit —
        when the websocket breaks it loops forever, logging the same error.
        We replace it with a bounded retry + timeout version.
        """
        api = self.api

        def patched_get_candles(ACTIVES, interval, count, endtime, _max_retries=3):
            inner = api.api  # internal IQOptionAPI object
            for attempt in range(_max_retries):
                inner.candles.candles_data = None
                try:
                    inner.getcandles(OP_code.ACTIVES[ACTIVES], interval, count, endtime)
                    # Wait for the websocket response with a 5s timeout
                    deadline = time.time() + 5
                    while inner.candles.candles_data is None:
                        if time.time() > deadline:
                            break
                        time.sleep(0.01)
                    if inner.candles.candles_data is not None:
                        return inner.candles.candles_data
                except Exception:
                    pass
                # Reconnect on failure (library's internal reconnect)
                try:
                    api.connect()
                except Exception:
                    pass
            log.warning("get_candles exhausted %d retries for %s", _max_retries, ACTIVES)
            return None

        api.get_candles = patched_get_candles

    def get_candles_df(self, pair=None, timeframe_seconds=None, count=None) -> pd.DataFrame:
        pair = pair or config.PAIR
        timeframe_seconds = timeframe_seconds or config.TIMEFRAME_SECONDS
        count = count or config.CANDLE_COUNT

        # Reconnect every 2 calls to prevent websocket overload
        if self._calls_since_reconnect >= self._MAX_CALLS:
            log.info("Reconnecting (every %d calls)", self._MAX_CALLS)
            self.connect()

        self.ensure_connected()
        self._calls_since_reconnect += 1

        try:
            raw = self.api.get_candles(pair, timeframe_seconds, count, time.time())
        except Exception as e:
            log.warning("get_candles failed for %s: %s — reconnecting", pair, e)
            self.connect()
            return pd.DataFrame()
        if not raw or not isinstance(raw, list) or len(raw) == 0:
            log.warning("get_candles returned invalid data for %s — reconnecting", pair)
            self.connect()
            return pd.DataFrame()
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
