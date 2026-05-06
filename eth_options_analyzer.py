#!/usr/bin/env python3
"""
Ethereum Options Analyzer
Pulls live ETH options data from Binance EAPI, identifies overpriced contracts
(high IV premium over realized vol) and high-gamma contracts, then sends a
formatted report to Telegram.

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

# ── Credentials ──────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Config ────────────────────────────────────────────────────────────────────
EAPI_BASE          = "https://eapi.binance.com"
SPOT_BASE          = "https://api.binance.com"
RISK_FREE_RATE     = 0.05     # annualized, ~US T-bill
IV_PREMIUM_THRESH  = 15.0     # vol points above realized → overpriced
MIN_MARK_PRICE     = 0.001    # ignore dust options below this USDT price
TOP_N              = 10       # results per category


# ── Binance EAPI helpers ──────────────────────────────────────────────────────

def get_eth_spot_price() -> float:
    """ETH/USDT index price from the options exchange."""
    r = requests.get(f"{EAPI_BASE}/eapi/v1/index", params={"underlying": "ETH"}, timeout=10)
    r.raise_for_status()
    return float(r.json()["indexPrice"])


def get_mark_prices() -> list[dict]:
    """Mark prices + Binance-calculated Greeks for all ETH options."""
    r = requests.get(f"{EAPI_BASE}/eapi/v1/mark", timeout=10)
    r.raise_for_status()
    return [d for d in r.json() if d["symbol"].startswith("ETH")]


def get_ticker_data() -> dict[str, dict]:
    """24 h ticker stats keyed by symbol."""
    r = requests.get(f"{EAPI_BASE}/eapi/v1/ticker", timeout=10)
    r.raise_for_status()
    return {d["symbol"]: d for d in r.json() if d["symbol"].startswith("ETH")}


def get_realized_vol(days: int = 30) -> float:
    """Annualized realized volatility from the last `days` daily ETHUSDT closes."""
    r = requests.get(
        f"{SPOT_BASE}/api/v3/klines",
        params={"symbol": "ETHUSDT", "interval": "1d", "limit": days + 1},
        timeout=10,
    )
    r.raise_for_status()
    closes = np.array([float(k[4]) for k in r.json()])
    log_ret = np.diff(np.log(closes))
    return float(np.std(log_ret, ddof=1) * math.sqrt(365))


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def _d1d2(S: float, K: float, T: float, r: float, sigma: float):
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> float:
    """Black-Scholes price. flag = 'C' or 'P'."""
    if T <= 1e-8 or sigma <= 1e-8:
        return max(S - K, 0) if flag == "C" else max(K - S, 0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    if flag == "C":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> dict:
    """Return BS delta, gamma, theta, vega."""
    if T <= 1e-8 or sigma <= 1e-8:
        return {"delta": 1.0 if (flag == "C" and S > K) else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1d2(S, K, T, r, sigma)
    pdf_d1 = norm.pdf(d1)
    gamma  = pdf_d1 / (S * sigma * math.sqrt(T))
    vega   = S * pdf_d1 * math.sqrt(T) / 100          # per 1 vol point
    if flag == "C":
        delta = norm.cdf(d1)
        theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        delta = norm.cdf(d1) - 1
        theta = (-S * pdf_d1 * sigma / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ── Symbol parser ─────────────────────────────────────────────────────────────

def parse_symbol(symbol: str):
    """
    Parse Binance options symbol, e.g. ETH-250530-2000-C
    Returns (strike, flag, T_years, expiry_dt) or raises ValueError.
    """
    parts = symbol.split("-")
    strike     = float(parts[2])
    flag       = parts[3]                                        # 'C' or 'P'
    expiry_dt  = datetime.strptime("20" + parts[1], "%Y%m%d").replace(tzinfo=timezone.utc)
    T          = max((expiry_dt - datetime.now(timezone.utc)).total_seconds() / (365.25 * 86400), 0.0)
    return strike, flag, T, expiry_dt


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze(spot: float, marks: list[dict], tickers: dict, rv: float) -> tuple[list, list]:
    """
    Returns (overpriced, high_gamma) sorted lists.

    Overpriced  → mark IV substantially above realized vol (seller edge).
    High gamma  → largest absolute gamma (explosive delta near expiry / ATM).
    """
    rows = []

    for opt in marks:
        sym = opt["symbol"]
        try:
            K, flag, T, expiry_dt = parse_symbol(sym)
        except (ValueError, IndexError, KeyError):
            continue

        mark_price = float(opt.get("markPrice", 0) or 0)
        mark_iv    = float(opt.get("markIV",    0) or 0)   # already in decimal (0.8 = 80 %)
        ask_iv     = float(opt.get("askIV",     0) or 0)
        # Binance-supplied Greeks (use as primary; supplement with BS where missing)
        b_delta = float(opt.get("delta", 0) or 0)
        b_gamma = float(opt.get("gamma", 0) or 0)
        b_theta = float(opt.get("theta", 0) or 0)
        b_vega  = float(opt.get("vega",  0) or 0)

        if mark_price < MIN_MARK_PRICE or mark_iv <= 0 or T <= 0:
            continue

        # BS theoretical price at realized vol
        theo = bs_price(spot, K, T, RISK_FREE_RATE, rv, flag)

        # IV premium in vol points (percentage points)
        iv_prem_pts = (mark_iv - rv) * 100

        # Price premium over theoretical
        price_prem_pct = ((mark_price - theo) / theo * 100) if theo > 0.001 else None

        # Fall back to BS gamma if Binance didn't supply one
        gamma_val = b_gamma if b_gamma != 0 else bs_greeks(spot, K, T, RISK_FREE_RATE, mark_iv, flag)["gamma"]

        ticker    = tickers.get(sym, {})
        volume    = float(ticker.get("volume",       0) or 0)
        open_int  = float(ticker.get("openInterest", 0) or 0)

        rows.append({
            "symbol":          sym,
            "strike":          K,
            "flag":            flag,
            "expiry":          expiry_dt.strftime("%Y-%m-%d"),
            "T_days":          round(T * 365.25, 1),
            "mark_price":      mark_price,
            "theo_price":      round(theo, 4),
            "price_prem_pct":  round(price_prem_pct, 1) if price_prem_pct is not None else None,
            "mark_iv_pct":     round(mark_iv * 100, 2),
            "ask_iv_pct":      round(ask_iv * 100, 2),
            "rv_pct":          round(rv * 100, 2),
            "iv_prem_pts":     round(iv_prem_pts, 2),
            "delta":           round(b_delta, 4),
            "gamma":           gamma_val,
            "theta":           round(b_theta, 4),
            "vega":            round(b_vega, 4),
            "volume":          volume,
            "open_interest":   open_int,
        })

    overpriced = sorted(
        [r for r in rows if r["iv_prem_pts"] >= IV_PREMIUM_THRESH],
        key=lambda x: x["iv_prem_pts"],
        reverse=True,
    )[:TOP_N]

    high_gamma = sorted(rows, key=lambda x: abs(x["gamma"]), reverse=True)[:TOP_N]

    return overpriced, high_gamma


# ── Report formatting ─────────────────────────────────────────────────────────

def format_report(spot: float, rv: float, overpriced: list, high_gamma: list) -> str:
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rv_pct = round(rv * 100, 2)

    lines = [
        f"\U0001f4ca *ETH Options Analysis* — {ts}",
        "",
        f"\U0001f537 ETH Spot: *${spot:,.2f}*",
        f"\U0001f4c9 30d Realized Vol: *{rv_pct}%*",
        "",
        "━" * 22,
        f"\U0001f534 *OVERPRICED OPTIONS* (IV prem ≥ {IV_PREMIUM_THRESH:.0f} vol pts)",
        "_(High implied-vol premium → rich to sell)_",
        "",
    ]

    if overpriced:
        for r in overpriced:
            pp = f"{r['price_prem_pct']:+.1f}%" if r["price_prem_pct"] is not None else "n/a"
            lines += [
                f"▸ `{r['symbol']}`",
                f"  {r['flag']} | Strike ${r['strike']:,.0f} | Exp {r['expiry']} ({r['T_days']}d)",
                f"  Mark ${r['mark_price']:.4f}  Theo(RV) ${r['theo_price']:.4f}  PricePrem {pp}",
                f"  MarkIV {r['mark_iv_pct']}%  AskIV {r['ask_iv_pct']}%  RV {r['rv_pct']}%  *+{r['iv_prem_pts']} pts*",
                f"  Δ={r['delta']:+.3f}  Γ={r['gamma']:.6f}  Θ={r['theta']:.4f}  ν={r['vega']:.4f}",
                f"  Vol {r['volume']:,.0f}  OI {r['open_interest']:,.0f}",
                "",
            ]
    else:
        lines += [f"_No options with IV premium ≥ {IV_PREMIUM_THRESH:.0f} pts found._", ""]

    lines += [
        "━" * 22,
        f"⚡ *HIGH GAMMA OPTIONS* (Top {TOP_N})",
        "_(Near-ATM / short-dated → explosive delta risk)_",
        "",
    ]

    for r in high_gamma:
        lines += [
            f"▸ `{r['symbol']}`",
            f"  {r['flag']} | Strike ${r['strike']:,.0f} | Exp {r['expiry']} ({r['T_days']}d)",
            f"  Mark ${r['mark_price']:.4f}  IV {r['mark_iv_pct']}%",
            f"  *Γ={r['gamma']:.6f}*  Δ={r['delta']:+.3f}  Θ={r['theta']:.4f}  ν={r['vega']:.4f}",
            f"  Vol {r['volume']:,.0f}  OI {r['open_interest']:,.0f}",
            "",
        ]

    lines += [
        "━" * 22,
        "⚠️ _Not financial advice. Always DYOR._",
    ]

    return "\n".join(lines)


# ── Telegram ──────────────────────────────────────────────────────────────────

def auto_detect_chat_id(token: str) -> str | None:
    """Pull the most recent message's chat_id from getUpdates."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
        data = r.json()
        if data.get("ok") and data.get("result"):
            for update in reversed(data["result"]):
                msg = update.get("message") or update.get("channel_post")
                if msg and "chat" in msg:
                    return str(msg["chat"]["id"])
    except Exception:
        pass
    return None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    """Send a Markdown message; split if over Telegram's 4096-char limit."""
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks  = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_once() -> None:
    print("[ETH Options Analyzer]")

    # Resolve Telegram chat_id
    chat_id = TELEGRAM_CHAT_ID.strip()
    if not chat_id:
        print("TELEGRAM_CHAT_ID not set — attempting auto-detect via getUpdates...")
        chat_id = auto_detect_chat_id(TELEGRAM_TOKEN)
        if chat_id:
            print(f"  Auto-detected chat_id: {chat_id}")
            print(f"  Tip: add TELEGRAM_CHAT_ID={chat_id} to your .env to skip this step.")
        else:
            print(
                "ERROR: Could not detect Telegram chat_id.\n"
                "  1. Open Telegram and send any message to your bot.\n"
                "  2. Then re-run this script, or set TELEGRAM_CHAT_ID=<your_id> in .env"
            )
            sys.exit(1)

    print("Fetching ETH spot price...")
    spot = get_eth_spot_price()
    print(f"  ETH: ${spot:,.2f}")

    print("Fetching mark prices and Greeks for all ETH options...")
    marks = get_mark_prices()
    print(f"  {len(marks)} contracts loaded")

    print("Fetching 24h ticker data...")
    tickers = get_ticker_data()

    print("Calculating 30d realized volatility from daily closes...")
    rv = get_realized_vol(30)
    print(f"  Realized vol: {rv * 100:.2f}%")

    print("Analyzing options...")
    overpriced, high_gamma = analyze(spot, marks, tickers, rv)
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
    parser = argparse.ArgumentParser(description="ETH Options Analyzer")
    parser.add_argument(
        "--loop", type=int, metavar="MINUTES", default=0,
        help="Repeat analysis every N minutes (0 = run once)",
    )
    args = parser.parse_args()

    if args.loop > 0:
        print(f"Running in loop mode — every {args.loop} minute(s). Ctrl-C to stop.")
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
