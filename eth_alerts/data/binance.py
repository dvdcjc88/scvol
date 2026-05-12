"""
Binance spot + futures public REST API client.
Also provides the WebSocket liquidation tracker (background thread).
"""
import json
import logging
import math
import threading
import time
from datetime import datetime
from typing import Optional

import numpy as np
import requests
import websocket

log = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
FAPI_BASE = "https://fapi.binance.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "eth-reversal-alerts/1.0"})


def _get(base: str, path: str, params: dict | None = None, timeout: int = 10) -> Optional[dict | list]:
    try:
        r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("Binance request failed (%s%s): %s", base, path, exc)
        return None


# ── Spot ──────────────────────────────────────────────────────────────

def get_spot_price() -> Optional[float]:
    data = _get(SPOT_BASE, "/api/v3/ticker/price", {"symbol": "ETHUSDT"})
    return float(data["price"]) if data and "price" in data else None


def get_klines(interval: str = "1m", limit: int = 480) -> list[list]:
    """Returns raw kline list [[open_ts, o, h, l, close, vol, ...], ...]."""
    data = _get(SPOT_BASE, "/api/v3/klines", {"symbol": "ETHUSDT", "interval": interval, "limit": limit})
    return data if isinstance(data, list) else []


# ── Futures / Perpetuals ──────────────────────────────────────────────

def get_perp_price() -> Optional[float]:
    data = _get(FAPI_BASE, "/fapi/v1/ticker/price", {"symbol": "ETHUSDT"})
    return float(data["price"]) if data and "price" in data else None


def get_funding_rate() -> Optional[float]:
    """Latest funding rate (not annualized, raw 8h rate)."""
    data = _get(FAPI_BASE, "/fapi/v1/fundingRate", {"symbol": "ETHUSDT", "limit": 1})
    if isinstance(data, list) and data:
        return float(data[0].get("fundingRate", 0))
    return None


def get_open_interest() -> Optional[float]:
    """Open interest in ETH contracts (notional = OI * price)."""
    data = _get(FAPI_BASE, "/fapi/v1/openInterest", {"symbol": "ETHUSDT"})
    if data and "openInterest" in data:
        return float(data["openInterest"])
    return None


def get_agg_trades(limit: int = 1000) -> list[dict]:
    """Recent aggregated trades from perp market. Used for CVD calculation."""
    data = _get(FAPI_BASE, "/fapi/v1/aggTrades", {"symbol": "ETHUSDT", "limit": limit})
    return data if isinstance(data, list) else []


# ── Derived calculations ──────────────────────────────────────────────

def calc_cvd_usd(agg_trades: list[dict]) -> float:
    """
    Cumulative Volume Delta in USD over the provided agg_trades window.
    Positive = net taker buying; Negative = net taker selling.
    isBuyerMaker=True  → seller is the taker  → selling pressure
    isBuyerMaker=False → buyer is the taker   → buying pressure
    """
    buy_vol = 0.0
    sell_vol = 0.0
    for t in agg_trades:
        qty = float(t.get("q", 0))
        price = float(t.get("p", 0))
        notional = qty * price
        if t.get("m"):  # buyer is maker → taker is selling
            sell_vol += notional
        else:
            buy_vol += notional
    return buy_vol - sell_vol


def calc_realized_vol_pct(klines: list[list], window: int = 480) -> Optional[float]:
    """
    Annualized realized volatility (%) from log returns of close prices.
    Default window=480 → 8 hours of 1-min bars.
    """
    if len(klines) < 10:
        return None
    closes = np.array([float(k[4]) for k in klines[-window:]])
    log_returns = np.diff(np.log(closes))
    if len(log_returns) < 5:
        return None
    # Annualize: there are 525_600 minutes in a year
    rv = float(np.std(log_returns)) * math.sqrt(525_600)
    return rv * 100.0  # as percentage


# ── Liquidation WebSocket tracker ─────────────────────────────────────

class LiquidationTracker:
    """
    Background WebSocket thread that accumulates ETH liquidation events
    from Binance's perp liquidation stream. Thread-safe via a simple lock.
    """

    WS_URL = "wss://fstream.binance.com/ws/ethusdt@forceOrder"

    def __init__(self, state) -> None:
        self._state = state
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="liq-tracker")
        self._thread.start()
        log.info("Liquidation tracker started")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.WS_URL,
                    on_message=self._on_message,
                    on_error=lambda ws, e: log.debug("LiqWS error: %s", e),
                    on_close=lambda ws, c, m: log.debug("LiqWS closed"),
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
                backoff = 2
            except Exception as exc:
                log.warning("LiqWS crash, reconnecting in %ds: %s", backoff, exc)
            if not self._stop.is_set():
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_message(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
            order = msg.get("o", {})
            side = order.get("S", "")   # "BUY" or "SELL"
            qty = float(order.get("q", 0))
            price = float(order.get("ap", 0) or order.get("p", 0))
            usd = qty * price
            if usd < 1000:
                return
            event = {"ts": datetime.utcnow(), "usd": usd, "side": side}
            with self._lock:
                self._state.liq_events.append(event)
        except Exception:
            pass
