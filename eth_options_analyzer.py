#!/usr/bin/env python3
"""
Ethereum Options Analyzer (Deribit)
Pulls live ETH options data from Deribit's public REST API (no API key needed),
identifies overpriced contracts (high IV premium over realized vol) and high-gamma
contracts, then sends a formatted report to Telegram.

Usage:
  python eth_options_analyzer.py              # run once
  python eth_options_analyzer.py --loop 30   # repeat every 30 minutes
"""

import os
import sys
import time
import math
import argparse
import requests
import numpy as np
from scipy.stats import norm
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Credentials (Deribit is public — no API key required for market data) ────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Config ────────────────────────────────────────────────────────────────────
DERIBIT_BASE       = "https://www.deribit.com/api/v2"
RISK_FREE_RATE     = 0.05     # annualized (~US T-bill)
IV_PREMIUM_THRESH  = 15.0     # vol points above realized → flag as overpriced
MIN_MARK_USD       = 1.0      # ignore dust options below this USD value
TOP_N              = 10       # results per category


# ── Deribit API helpers ───────────────────────────────────────────────────────

def deribit_get(method: str, params: dict = None, _retries: int = 5) -> any:
    for attempt in range(1, _retries + 1):
        try:
            r = requests.get(f"{DERIBIT_BASE}/{method}", params=params or {}, timeout=15)
            if r.status_code == 503:
                body = r.json() if r.content else {}
                code = body.get("error", {}).get("code")
                if code == 11051:
                    wait = attempt * 10
                    print(f"  Deribit in maintenance — retrying in {wait}s (attempt {attempt}/{_retries})...")
                    time.sleep(wait)
                    continue
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                raise RuntimeError(f"Deribit error [{method}]: {body['error']}")
            return body["result"]
        except requests.exceptions.HTTPError:
            if attempt == _retries:
                raise
    raise RuntimeError(f"Deribit unreachable after {_retries} attempts [{method}]")


def get_eth_spot() -> float:
    """Current ETH/USD index price."""
    return float(deribit_get("public/get_index_price", {"index_name": "eth_usd"})["index_price"])


def get_book_summaries() -> list[dict]:
    """All ETH option summaries: mark price, IVs, greeks, volume, OI."""
    return deribit_get("public/get_book_summary_by_currency", {"currency": "ETH", "kind": "option"})


def get_realized_vol() -> float:
    """
    30-day annualized realized volatility from Deribit's historical vol feed.
    Returns as a decimal (0.80 = 80 %).
    """
    data = deribit_get("public/get_historical_volatility", {"currency": "ETH"})
    # data is [[timestamp_ms, vol_pct], ...] — take the most recent value
    if data:
        return float(data[-1][1]) / 100.0
    return 0.80  # sensible fallback


# ── Symbol parser ─────────────────────────────────────────────────────────────

def parse_symbol(name: str):
    """
    Deribit option symbol format: ETH-30MAY25-2000-C
    Returns (strike, flag, T_years, expiry_dt).
    """
    parts     = name.split("-")
    strike    = float(parts[2])
    flag      = parts[3]                                              # 'C' or 'P'
    expiry_dt = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
    T         = max((expiry_dt - datetime.now(timezone.utc)).total_seconds() / (365.25 * 86400), 0.0)
    return strike, flag, T, expiry_dt


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _d1d2(S, K, T, r, sigma):
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> float:
    if T <= 1e-8 or sigma <= 1e-8:
        return max(S - K, 0) if flag == "C" else max(K - S, 0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    if flag == "C":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> dict:
    if T <= 1e-8 or sigma <= 1e-8:
        return {"delta": 1.0 if (flag == "C" and S > K) else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2   = _d1d2(S, K, T, r, sigma)
    pdf_d1   = norm.pdf(d1)
    gamma    = pdf_d1 / (S * sigma * math.sqrt(T))
    vega     = S * pdf_d1 * math.sqrt(T) / 100          # per 1 vol-pt
    if flag == "C":
        delta = norm.cdf(d1)
        theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        delta = norm.cdf(d1) - 1.0
        theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze(spot: float, summaries: list[dict], rv: float) -> tuple[list, list]:
    """
    Returns (overpriced, high_gamma) sorted lists.

    Overpriced → mark IV substantially above realized vol (rich to sell).
    High gamma  → largest absolute gamma (explosive ATM near expiry).
    """
    rows = []

    for item in summaries:
        name = item.get("instrument_name", "")
        if not name.startswith("ETH-"):
            continue
        try:
            K, flag, T, expiry_dt = parse_symbol(name)
        except (ValueError, IndexError):
            continue

        # Deribit prices are in ETH — convert to USD
        mark_eth     = float(item.get("mark_price", 0) or 0)
        mark_usd     = mark_eth * spot
        underlying_p = float(item.get("underlying_price", spot) or spot)

        # IVs are in percent on Deribit (80.5 = 80.5 %)
        mark_iv_pct  = float(item.get("mark_iv",  0) or 0)
        bid_iv_pct   = float(item.get("bid_iv",   0) or 0)
        ask_iv_pct   = float(item.get("ask_iv",   0) or 0)

        if mark_usd < MIN_MARK_USD or mark_iv_pct <= 0 or T <= 0:
            continue

        mark_iv = mark_iv_pct / 100.0

        # Greeks — prefer Deribit-supplied, fall back to BS
        g = item.get("greeks") or {}
        delta = float(g.get("delta", 0) or 0)
        gamma = float(g.get("gamma", 0) or 0)
        theta = float(g.get("theta", 0) or 0)
        vega  = float(g.get("vega",  0) or 0)

        if gamma == 0:
            calc  = bs_greeks(underlying_p, K, T, RISK_FREE_RATE, mark_iv, flag)
            delta = delta or calc["delta"]
            gamma = calc["gamma"]
            theta = theta or calc["theta"]
            vega  = vega  or calc["vega"]

        # Theoretical price at realized vol
        theo_usd       = bs_price(underlying_p, K, T, RISK_FREE_RATE, rv, flag)
        iv_prem_pts    = mark_iv_pct - rv * 100
        price_prem_pct = ((mark_usd - theo_usd) / theo_usd * 100) if theo_usd > 0.01 else None

        rows.append({
            "symbol":         name,
            "strike":         K,
            "flag":           flag,
            "expiry":         expiry_dt.strftime("%Y-%m-%d"),
            "T_days":         round(T * 365.25, 1),
            "mark_usd":       round(mark_usd, 2),
            "mark_eth":       round(mark_eth, 6),
            "theo_usd":       round(theo_usd, 2),
            "price_prem_pct": round(price_prem_pct, 1) if price_prem_pct is not None else None,
            "mark_iv_pct":    round(mark_iv_pct, 2),
            "bid_iv_pct":     round(bid_iv_pct,  2),
            "ask_iv_pct":     round(ask_iv_pct,  2),
            "rv_pct":         round(rv * 100,     2),
            "iv_prem_pts":    round(iv_prem_pts,  2),
            "delta":          round(delta, 4),
            "gamma":          gamma,
            "theta":          round(theta, 4),
            "vega":           round(vega,  4),
            "volume":         float(item.get("volume",        0) or 0),
            "open_interest":  float(item.get("open_interest", 0) or 0),
        })

    # Only contracts expiring within 30 days
    rows = [r for r in rows if r["T_days"] <= 30]

    overpriced = sorted(
        [r for r in rows if r["iv_prem_pts"] >= IV_PREMIUM_THRESH],
        key=lambda x: x["mark_iv_pct"],   # highest absolute IV first
        reverse=True,
    )[:TOP_N]

    high_gamma = sorted(rows, key=lambda x: abs(x["gamma"]), reverse=True)[:TOP_N]

    return overpriced, high_gamma


# ── Report formatting ─────────────────────────────────────────────────────────

def format_report(spot: float, rv: float, overpriced: list, high_gamma: list) -> str:
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rv_pct = rv * 100

    def sym(s: str) -> str:
        return s[4:]  # "ETH-16MAY25-2500-C" → "16MAY25-2500-C"

    # ── Overpriced table ──────────────────────────────────────────────────────
    OV_HDR = (f"{'Contract':<16} {'DTE':>4}  "
              f"{'MarkIV':>6}  {'IV+pts':>6}  {'Mark($)':>9}  {'Delta':>7}")
    SEP_O  = "─" * len(OV_HDR)

    def ov_row(r: dict) -> str:
        price = f"${r['mark_usd']:,.2f}"
        return (
            f"{sym(r['symbol']):<16} {int(r['T_days']):>3}d  "
            f"{r['mark_iv_pct']:>5.1f}%  {r['iv_prem_pts']:>+6.1f}  "
            f"{price:>9}  {r['delta']:>+7.3f}"
        )

    ov_body = (
        "\n".join(ov_row(r) for r in overpriced)
        if overpriced
        else f"  No contracts with IV prem >= {IV_PREMIUM_THRESH:.0f}pts in <=30d window."
    )
    ov_table = f"```\n{OV_HDR}\n{SEP_O}\n{ov_body}\n{SEP_O}```"

    # ── High Gamma table ──────────────────────────────────────────────────────
    GM_HDR = (f"{'Contract':<16} {'DTE':>4}  "
              f"{'Gamma':>8}  {'MarkIV':>6}  {'Delta':>7}  {'Theta/d':>8}")
    SEP_G  = "─" * len(GM_HDR)

    def gm_row(r: dict) -> str:
        return (
            f"{sym(r['symbol']):<16} {int(r['T_days']):>3}d  "
            f"{r['gamma']:>8.5f}  {r['mark_iv_pct']:>5.1f}%  "
            f"{r['delta']:>+7.3f}  {r['theta']:>+8.2f}"
        )

    gm_body = (
        "\n".join(gm_row(r) for r in high_gamma)
        if high_gamma
        else "  No contracts found."
    )
    gm_table = f"```\n{GM_HDR}\n{SEP_G}\n{gm_body}\n{SEP_G}```"

    # ── Assemble ──────────────────────────────────────────────────────────────
    return "\n".join([
        f"📊 *ETH Options Report* — {ts}",
        f"Spot: *${spot:,.2f}*  |  30d RV: *{rv_pct:.1f}%*  |  Filter: ≤30d expiry",
        "",
        f"🔴 *OVERPRICED* — by highest IV  (prem ≥{IV_PREMIUM_THRESH:.0f}pts above RV)",
        ov_table,
        "",
        f"⚡ *HIGH GAMMA* — top {TOP_N} contracts  (≤30d)",
        gm_table,
        "",
        "⚠️ _Not financial advice. Always DYOR._",
    ])


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(token: str, chat_id: str, text: str) -> None:
    url    = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": chunk,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_once() -> None:
    print("[ETH Options Analyzer — Deribit]")

    chat_id = TELEGRAM_CHAT_ID.strip()
    if not chat_id:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env")
        sys.exit(1)

    print("Fetching ETH spot price...")
    spot = get_eth_spot()
    print(f"  ETH: ${spot:,.2f}")

    print("Fetching all ETH option summaries...")
    summaries = get_book_summaries()
    print(f"  {len(summaries)} contracts loaded")

    print("Fetching 30d realized volatility...")
    rv = get_realized_vol()
    print(f"  Realized vol: {rv * 100:.2f}%")

    print("Analyzing options...")
    overpriced, high_gamma = analyze(spot, summaries, rv)
    print(f"  Overpriced (IV prem >= {IV_PREMIUM_THRESH}pts): {len(overpriced)}")
    print(f"  High gamma (top {TOP_N}):                    {len(high_gamma)}")

    report = format_report(spot, rv, overpriced, high_gamma)

    print("Sending report to Telegram...")
    try:
        send_telegram(TELEGRAM_TOKEN, chat_id, report)
        print("  Report sent successfully.")
    except Exception as exc:
        print(f"  Telegram error: {exc}")
        print("\n--- REPORT (stdout fallback) ---")
        print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="ETH Options Analyzer (Deribit)")
    parser.add_argument(
        "--loop", type=int, metavar="MINUTES", default=0,
        help="Repeat every N minutes (0 = run once)",
    )
    args = parser.parse_args()

    if args.loop > 0:
        print(f"Loop mode: every {args.loop}m. Ctrl-C to stop.")
        while True:
            try:
                run_once()
            except Exception as exc:
                print(f"[ERROR] {exc}")
            print(f"Sleeping {args.loop}m...")
            time.sleep(args.loop * 60)
    else:
        run_once()


if __name__ == "__main__":
    main()
