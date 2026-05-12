"""
Deribit public REST API client.

All endpoints are unauthenticated (public data only).

Architecture note:
  get_book_summary_by_currency → mark_iv, open_interest, underlying_price
  get_instruments              → expiration_timestamp, strike, option_type
  These are joined by instrument_name. Greeks are computed via Black-Scholes
  since the book summary endpoint does not return them.
"""
import logging
import math
import time
from typing import Optional
import requests

log = logging.getLogger(__name__)

BASE = "https://www.deribit.com/api/v2/public"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "eth-reversal-alerts/1.0"})


def _get(path: str, params: dict | None = None, timeout: int = 15) -> Optional[dict]:
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


# ── Black-Scholes greeks (no external deps) ───────────────────────────

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _bs_greeks(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> dict:
    """Returns delta and gamma via Black-Scholes (T in years, sigma as decimal)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0}
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    delta = _norm_cdf(d1) if opt_type == "C" else _norm_cdf(d1) - 1.0
    return {"delta": round(delta, 4), "gamma": gamma}


# ── Public getters ────────────────────────────────────────────────────

def get_eth_spot() -> Optional[float]:
    """ETH index price in USD from Deribit."""
    result = _get("/get_index_price", {"index_name": "eth_usd"})
    if result:
        return float(result.get("index_price", 0) or 0) or None
    return None


def get_dvol() -> Optional[float]:
    """Current ETH DVOL (30-day implied vol index, annualized %)."""
    result = _get("/get_index_price", {"index_name": "ethdvol_usdc"})
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


def _fetch_instruments() -> dict[str, dict]:
    """Returns {instrument_name: {expiration_timestamp, strike, option_type}} for active ETH options."""
    result = _get("/get_instruments", {"currency": "ETH", "kind": "option"})
    if not result:
        return {}
    out = {}
    for inst in result:
        name = inst.get("instrument_name", "")
        exp_ts = inst.get("expiration_timestamp")
        strike = inst.get("strike")
        opt_type_raw = inst.get("option_type", "")
        if name and exp_ts and strike is not None:
            out[name] = {
                "exp_ts": int(exp_ts),
                "strike": float(strike),
                "type": "C" if opt_type_raw == "call" else "P",
            }
    return out


def get_options_chain() -> list[dict]:
    """
    Fetches ETH options chain by combining:
      - get_book_summary_by_currency (mark_iv, OI, underlying price)
      - get_instruments (expiry timestamp, strike, type)
    Greeks (delta, gamma) are computed via Black-Scholes from mark_iv.
    Returns list of option dicts with all fields populated.
    """
    instruments = _fetch_instruments()
    if not instruments:
        return []

    summary_result = _get("/get_book_summary_by_currency", {"currency": "ETH", "kind": "option"})
    if not summary_result:
        return []

    now_ms = time.time() * 1000
    risk_free = 0.05  # approximate annual risk-free rate

    chain = []
    for item in summary_result:
        name: str = item.get("instrument_name", "")
        inst_meta = instruments.get(name)
        if not inst_meta:
            continue

        mark_iv = item.get("mark_iv")
        oi = item.get("open_interest", 0)
        underlying = item.get("underlying_price")

        if mark_iv is None or (oi or 0) == 0:
            continue

        exp_ts = inst_meta["exp_ts"]
        if exp_ts <= now_ms:
            continue  # expired

        strike = inst_meta["strike"]
        opt_type = inst_meta["type"]
        S = float(underlying) if underlying else 0.0
        sigma = float(mark_iv) / 100.0
        T = (exp_ts - now_ms) / (1000 * 365.25 * 86400)  # years

        greeks = _bs_greeks(S, strike, T, risk_free, sigma, opt_type) if S > 0 else {"delta": 0.0, "gamma": 0.0}

        chain.append(
            {
                "instrument": name,
                "strike": strike,
                "type": opt_type,
                "exp_ts": exp_ts,
                "dte_years": T,
                "mark_iv": float(mark_iv),
                "oi": float(oi),
                "underlying": S,
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
            }
        )

    return chain


def parse_term_structure(chain: list[dict], spot: float) -> list[dict]:
    """
    Returns IV at ATM for each expiry, sorted by DTE.
    Each entry: {exp_ts, dte_days, atm_iv, strike}
    """
    by_exp: dict[int, list] = {}
    for opt in chain:
        exp = opt["exp_ts"]
        by_exp.setdefault(exp, []).append(opt)

    term = []
    for exp, opts in sorted(by_exp.items()):
        dte_days = opt["dte_years"] * 365.25  # re-use precomputed dte_years
        dte_days = (exp - time.time() * 1000) / (1000 * 86400)
        if dte_days < 0.04:  # skip sub-1h expiries
            continue
        calls = [o for o in opts if o["type"] == "C"]
        if not calls:
            continue
        atm = min(calls, key=lambda o: abs(o["strike"] - spot))
        term.append({"exp_ts": exp, "dte_days": dte_days, "atm_iv": atm["mark_iv"], "strike": atm["strike"]})

    return term


def get_25d_skew(chain: list[dict], spot: float) -> Optional[float]:
    """
    Returns put_25d_iv − call_25d_iv for the front (nearest) expiry.
    Positive = put skew elevated (fear/bearish sentiment).
    Negative = call skew elevated (greed/bullish sentiment).
    """
    now_ms = time.time() * 1000
    future_exps = sorted(
        {o["exp_ts"] for o in chain if (o["exp_ts"] - now_ms) > 3600 * 1000}
    )
    if not future_exps:
        return None

    front_exp = future_exps[0]
    front_opts = [o for o in chain if o["exp_ts"] == front_exp]

    # Prefer options closest to ±0.25 delta
    puts_25d = [o for o in front_opts if o["type"] == "P" and abs(o["delta"] + 0.25) < 0.12]
    calls_25d = [o for o in front_opts if o["type"] == "C" and abs(o["delta"] - 0.25) < 0.12]

    if not puts_25d or not calls_25d:
        # Fallback: OTM options by strike distance
        otm_dist = spot * 0.05
        puts_25d = [o for o in front_opts if o["type"] == "P" and spot - otm_dist < o["strike"] < spot]
        calls_25d = [o for o in front_opts if o["type"] == "C" and spot < o["strike"] < spot + otm_dist]

    if not puts_25d or not calls_25d:
        return None

    put_iv = min(puts_25d, key=lambda o: abs(abs(o["delta"]) - 0.25))["mark_iv"]
    call_iv = min(calls_25d, key=lambda o: abs(abs(o["delta"]) - 0.25))["mark_iv"]

    return put_iv - call_iv
