"""
Market data client — uses OKX (perp/funding/OI/CVD) + Kraken (spot/klines).
The Binance REST API is geo-restricted in some regions; OKX and Kraken are
used as drop-in replacements with equivalent data quality.
The Binance WebSocket liquidation stream is kept (it connects successfully).

Public API, no keys required for any of the endpoints below.
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

OKX_BASE = "https://www.okx.com"
KRAKEN_BASE = "https://api.kraken.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "eth-reversal-alerts/1.0"})


def _get(base: str, path: str, params: dict | None = None, timeout: int = 10) -> Optional[dict | list]:
    try:
        r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("Request failed (%s%s): %s", base, path, exc)
        return None


def _okx(path: str, params: dict | None = None) -> Optional[list]:
    """Returns OKX data[] list or None."""
    raw = _get(OKX_BASE, path, params)
    if raw and raw.get("code") == "0":
        return raw.get("data")
    return None


# ── Spot ──────────────────────────────────────────────────────────────

def get_spot_price() -> Optional[float]:
    """ETH/USD spot from Kraken."""
    raw = _get(KRAKEN_BASE, "/0/public/Ticker", {"pair": "ETHUSD"})
    if raw and not raw.get("error"):
        entry = raw.get("result", {}).get("XETHZUSD", {})
        last = entry.get("c", [None])[0]
        return float(last) if last else None
    return None


def get_klines(interval: str = "1m", limit: int = 480) -> list[list]:
    """
    Returns raw kline list compatible with [[..., close, ...], ...] indexing.
    Kraken OHLC: [time, open, high, low, close, vwap, volume, count]
    Close is index 4, matching Binance format.
    interval param: "1m" → Kraken interval=1
    limit: Kraken returns up to 720 1-min candles (12h).
    """
    kraken_interval = 1  # only 1m used; extend mapping if needed
    raw = _get(KRAKEN_BASE, "/0/public/OHLC", {"pair": "ETHUSD", "interval": kraken_interval})
    if raw and not raw.get("error"):
        candles = raw.get("result", {}).get("XETHZUSD", [])
        return candles[-limit:] if candles else []
    return []


# ── Perpetuals / Futures — OKX ETH-USDT-SWAP ─────────────────────────

def get_perp_price() -> Optional[float]:
    """ETH-USDT perpetual last price from OKX."""
    data = _okx("/api/v5/market/ticker", {"instId": "ETH-USDT-SWAP"})
    if data:
        return float(data[0].get("last", 0)) or None
    return None


def get_funding_rate() -> Optional[float]:
    """
    Current funding rate for ETH-USDT-SWAP on OKX.
    Returns raw rate (e.g. 0.0001 = 0.01% per 8h).
    """
    data = _okx("/api/v5/public/funding-rate", {"instId": "ETH-USDT-SWAP"})
    if data:
        return float(data[0].get("fundingRate", 0))
    return None


def get_open_interest() -> Optional[float]:
    """Open interest in ETH (oiCcy) for ETH-USDT-SWAP on OKX."""
    data = _okx("/api/v5/public/open-interest", {"instType": "SWAP", "instId": "ETH-USDT-SWAP"})
    if data:
        return float(data[0].get("oiCcy", 0)) or None
    return None


def get_agg_trades(limit: int = 500) -> list[dict]:
    """
    Recent trades from OKX ETH-USDT-SWAP.
    Each entry has: side ('buy'/'sell'), sz (ETH qty), px (price).
    OKX max limit is 500 per call.
    """
    actual_limit = min(limit, 500)
    data = _okx("/api/v5/market/trades", {"instId": "ETH-USDT-SWAP", "limit": actual_limit})
    return data if data else []


# ── Derived calculations ──────────────────────────────────────────────

def calc_cvd_usd(agg_trades: list[dict]) -> float:
    """
    Cumulative Volume Delta in USD.
    OKX trade side: 'buy' = taker buying (positive delta)
                    'sell' = taker selling (negative delta)
    """
    buy_vol = 0.0
    sell_vol = 0.0
    for t in agg_trades:
        qty = float(t.get("sz", 0))
        price = float(t.get("px", 0))
        notional = qty * price
        if t.get("side") == "buy":
            buy_vol += notional
        else:
            sell_vol += notional
    return buy_vol - sell_vol


def calc_realized_vol_pct(klines: list[list], window: int = 480) -> Optional[float]:
    """
    Annualized realized volatility (%) from log returns of 1-min close prices.
    Kraken OHLC close is at index 4.
    """
    if len(klines) < 10:
        return None
    closes = np.array([float(k[4]) for k in klines[-window:]])
    log_returns = np.diff(np.log(closes))
    if len(log_returns) < 5:
        return None
    # 525_600 minutes per year for annualization
    rv = float(np.std(log_returns)) * math.sqrt(525_600)
    return rv * 100.0


# ── Liquidation WebSocket — Binance perp stream ───────────────────────

class LiquidationTracker:
    """
    Background WebSocket thread accumulating ETH liquidation events from
    Binance's futures force-order stream (WebSocket endpoint works globally
    even when the REST API is geo-restricted).
    Thread-safe via a simple lock.
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
        log.info("Liquidation tracker started (Binance WS)")

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
            side = order.get("S", "")   # "BUY" (short liq) or "SELL" (long liq)
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
