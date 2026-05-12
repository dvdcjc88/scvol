"""
Deribit public REST API client.

All endpoints are unauthenticated (public data only).
"""
import logging
import time
from typing import Optional
import requests

log = logging.getLogger(__name__)

BASE = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "eth-reversal-alerts/1.0"})


def _get(path: str, params: dict | None = None, timeout: int = 10) -> Optional[dict]:
    try:
        r = SESSION.get(f"{BASE}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            log.warning("Deribit error on %s: %s", path, data["error"])
            return None
        return data.get("result")
    except Exception as exc:
        log.warning("Deribit request failed (%s): %s", path, exc)
        return None


# ── Public getters ────────────────────────────────────────────────────

def get_eth_spot() -> Optional[float]:
    """ETH index price in USD from Deribit."""
    result = _get("/get_index_price", {"index_name": "eth_usd"})
    if result:
        return float(result.get("index_price", 0) or 0) or None
    return None


def get_dvol() -> Optional[float]:
    """Current ETH DVOL (30-day implied vol index, annualized %)."""
    result = _get("/get_index_price", {"index_name": "eth_dvol"})
    if result:
        val = result.get("index_price")
        return float(val) if val is not None else None
    return None


def get_dvol_history(lookback_hours: int = 2) -> list[dict]:
    """
    Returns list of {ts_ms, close} DVOL hourly candles for the past
    `lookback_hours`. Used to detect spikes vs baseline.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_hours * 3600 * 1000
    result = _get(
        "/get_volatility_index_data",
        {"currency": "ETH", "start_timestamp": start_ms, "end_timestamp": now_ms, "resolution": "3600"},
    )
    if not result:
        return []
    candles = result.get("data", [])
    # Each candle: [timestamp_ms, open, high, low, close]
    return [{"ts_ms": c[0], "close": float(c[4])} for c in candles if len(c) >= 5]


def get_options_chain() -> list[dict]:
    """
    Fetches entire ETH options book summary from Deribit.
    Returns list of dicts, each containing:
      instrument_name, expiration_timestamp, underlying_price,
      mark_iv, open_interest, greeks (delta, gamma, theta, vega)
    Filters out instruments with no OI or mark_iv.
    """
    result = _get("/get_book_summary_by_currency", {"currency": "ETH", "kind": "option"})
    if not result:
        return []

    chain = []
    for item in result:
        name: str = item.get("instrument_name", "")
        if not name:
            continue

        parts = name.split("-")
        if len(parts) != 4:
            continue

        try:
            strike = float(parts[2])
            opt_type = parts[3]  # "C" or "P"
        except ValueError:
            continue

        exp_ts = item.get("expiration_timestamp")
        mark_iv = item.get("mark_iv")
        oi = item.get("open_interest", 0)
        greeks = item.get("greeks") or {}
        underlying = item.get("underlying_price") or item.get("underlying_index_price")

        if mark_iv is None or (oi or 0) == 0:
            continue

        chain.append(
            {
                "instrument": name,
                "strike": strike,
                "type": opt_type,
                "exp_ts": int(exp_ts) if exp_ts else 0,
                "mark_iv": float(mark_iv),
                "oi": float(oi),
                "underlying": float(underlying) if underlying else None,
                "delta": float(greeks.get("delta") or 0),
                "gamma": float(greeks.get("gamma") or 0),
                "vega": float(greeks.get("vega") or 0),
            }
        )

    return chain


def parse_term_structure(chain: list[dict], spot: float) -> list[dict]:
    """
    Returns IV at ATM for each expiry, sorted by DTE.
    Each entry: {exp_ts, dte_days, atm_iv}
    """
    now_ms = time.time() * 1000
    by_exp: dict[int, list] = {}
    for opt in chain:
        exp = opt["exp_ts"]
        if exp < now_ms:
            continue
        by_exp.setdefault(exp, []).append(opt)

    term = []
    for exp, opts in sorted(by_exp.items()):
        dte = (exp - now_ms) / (1000 * 86400)
        if dte < 0.04:  # skip sub-1h expiries
            continue
        # Find ATM call (closest strike to spot)
        calls = [o for o in opts if o["type"] == "C"]
        if not calls:
            continue
        atm = min(calls, key=lambda o: abs(o["strike"] - spot))
        term.append({"exp_ts": exp, "dte_days": dte, "atm_iv": atm["mark_iv"], "strike": atm["strike"]})

    return term


def get_25d_skew(chain: list[dict], spot: float) -> Optional[float]:
    """
    Returns put_25d_iv - call_25d_iv for the front (nearest) expiry.
    Positive = put skew elevated (bearish sentiment).
    """
    now_ms = time.time() * 1000
    future_exps = sorted(
        {o["exp_ts"] for o in chain if o["exp_ts"] > now_ms and (o["exp_ts"] - now_ms) > 3600 * 1000}
    )
    if not future_exps:
        return None

    front_exp = future_exps[0]
    front_opts = [o for o in chain if o["exp_ts"] == front_exp]

    puts_25d = [o for o in front_opts if o["type"] == "P" and abs(o["delta"] + 0.25) < 0.1]
    calls_25d = [o for o in front_opts if o["type"] == "C" and abs(o["delta"] - 0.25) < 0.1]

    if not puts_25d or not calls_25d:
        # Fallback: OTM options by distance from spot
        otm_dist = spot * 0.05
        puts_25d = [o for o in front_opts if o["type"] == "P" and spot - otm_dist < o["strike"] < spot]
        calls_25d = [o for o in front_opts if o["type"] == "C" and spot < o["strike"] < spot + otm_dist]

    if not puts_25d or not calls_25d:
        return None

    put_iv = min(puts_25d, key=lambda o: abs(abs(o["delta"]) - 0.25))["mark_iv"]
    call_iv = min(calls_25d, key=lambda o: abs(abs(o["delta"]) - 0.25))["mark_iv"]

    return put_iv - call_iv
