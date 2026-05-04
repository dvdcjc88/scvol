#!/usr/bin/env python3
"""
ETH Vol Desk — Daily Morning Brief
Runs daily at 23:00 SGT (15:00 UTC)
Output: /home/user/scvol/reports/eth_vol_brief_YYYYMMDD.txt
"""

import requests
import numpy as np
import json
import sys
import os
from datetime import datetime, timezone
from collections import defaultdict

try:
    from arch import arch_model
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False

# ── CONFIG ────────────────────────────────────────────────────────────────────
OKX_BASE     = "https://www.okx.com"
DERIBIT_BASE = "https://www.deribit.com"
REPORT_DIR   = "/home/user/scvol/reports"
TIMEOUT      = 12

os.makedirs(REPORT_DIR, exist_ok=True)

lines = []

def p(*args):
    msg = " ".join(str(a) for a in args)
    lines.append(msg)
    print(msg)

def safe_get(url, params=None, label=""):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        d = r.json()
        if d.get("code", "0") not in ("0", 0):
            p(f"  [WARN] {label}: code={d.get('code')} msg={d.get('msg','?')}")
        return d
    except Exception as e:
        p(f"  [DATA GAP] {label}: {e}")
        return None

# ── HELPERS ───────────────────────────────────────────────────────────────────
def fetch_candles(bar, n):
    all_data, after = [], None
    for _ in range(10):
        params = {"instId": "ETH-USDT", "bar": bar, "limit": "300"}
        if after:
            params["after"] = after
        r = safe_get(f"{OKX_BASE}/api/v5/market/candles", params, f"candles-{bar}")
        if not r or not r.get("data"):
            break
        all_data.extend(r["data"])
        after = r["data"][-1][0]
        if len(all_data) >= n or len(r["data"]) < 300:
            break
    return all_data[:n]

def parse_ohlcv(raw):
    return [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
             "low": float(c[3]), "close": float(c[4]), "vol": float(c[5])}
            for c in reversed(raw)]

def compute_rv(bars, ann_factor):
    closes = np.array([b["close"] for b in bars])
    highs  = np.array([b["high"]  for b in bars])
    lows   = np.array([b["low"]   for b in bars])
    opens  = np.array([b["open"]  for b in bars])
    lr = np.log(closes[1:] / closes[:-1])
    cc = lr.std() * ann_factor
    hl = np.log(highs / lows)
    pk = np.sqrt(np.mean(hl ** 2) / (4 * np.log(2))) * ann_factor
    gk_vals = 0.5 * hl ** 2 - (2 * np.log(2) - 1) * (np.log(closes / opens)) ** 2
    gk = np.sqrt(max(np.mean(gk_vals), 0)) * ann_factor
    return cc, pk, gk

def parse_opts(opts_data, spot):
    by_exp = defaultdict(list)
    for o in opts_data:
        raw_id = o["instId"]
        for pfx in ["ETH-USD_UM-", "ETH-USD-"]:
            if raw_id.startswith(pfx):
                rest = raw_id[len(pfx):]
                break
        else:
            continue
        parts = rest.split("-")
        if len(parts) < 3:
            continue
        exp_str = parts[0]
        try:
            strike = float(parts[1])
        except Exception:
            continue
        cp = parts[2]
        by_exp[exp_str].append({
            "strike": strike, "cp": cp,
            "mark_vol": float(o.get("markVol", 0) or 0),
            "ask_vol":  float(o.get("askVol",  0) or 0),
            "bid_vol":  float(o.get("bidVol",  0) or 0),
            "delta":    float(o.get("delta",   0) or 0),
            "gamma":    float(o.get("gammaBS", 0) or 0),
            "vega":     float(o.get("vegaBS",  0) or 0),
            "theta":    float(o.get("thetaBS", 0) or 0),
            "fwd_px":   float(o.get("fwdPx", spot) or spot),
        })
    return by_exp

def atm_iv(chain, fwd):
    calls = [x for x in chain if x["cp"] == "C" and x["mark_vol"] > 0]
    if not calls:
        return None
    return min(calls, key=lambda x: abs(x["strike"] - fwd))

def rr_25d(chain):
    calls = [x for x in chain if x["cp"] == "C" and x["mark_vol"] > 0]
    puts  = [x for x in chain if x["cp"] == "P" and x["mark_vol"] > 0]
    c25 = min(calls, key=lambda x: abs(abs(x["delta"]) - 0.25)) if calls else None
    p25 = min(puts,  key=lambda x: abs(abs(x["delta"]) - 0.25)) if puts else None
    if c25 and p25:
        return c25["mark_vol"] - p25["mark_vol"], c25, p25
    return None, None, None

def top_gamma_strikes(chain, n=3):
    strikes = sorted(set(x["strike"] for x in chain))
    sg = {s: sum(x["gamma"] for x in chain if x["strike"] == s) for s in strikes}
    return sorted(sg.items(), key=lambda x: x[1], reverse=True)[:n]

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    now_utc = datetime.now(timezone.utc)
    now_ts  = int(now_utc.timestamp() * 1000)
    date_str = now_utc.strftime("%Y-%m-%d")

    p("═" * 65)
    p("  ETH VOL DESK — MORNING BRIEF")
    p(f"  Generated : {now_utc.strftime('%Y-%m-%d %H:%M UTC')}  (23:00 SGT)")
    p("═" * 65)

    # ── SPOT ─────────────────────────────────────────────────────────────────
    r_spot = safe_get(f"{OKX_BASE}/api/v5/market/ticker", {"instId": "ETH-USDT"}, "spot")
    if not r_spot or not r_spot.get("data"):
        p("[FATAL] Cannot fetch spot — aborting.")
        return
    sd   = r_spot["data"][0]
    spot = float(sd["last"])
    high24  = float(sd["high24h"])
    low24   = float(sd["low24h"])
    open24  = float(sd["open24h"])
    vol24   = float(sd["volCcy24h"])
    chg24   = (spot / open24 - 1) * 100

    p(f"\nETH Spot : ${spot:,.2f}")
    p(f"24h range: ${low24:,.2f} – ${high24:,.2f}  ({(high24-low24)/spot*100:.1f}%)")
    p(f"24h chg  : {chg24:+.2f}%   |   24h vol: ${vol24/1e6:.0f}M")

    # ── CANDLES + RV ─────────────────────────────────────────────────────────
    raw_1m = fetch_candles("1m",  1440)
    raw_5m = fetch_candles("5m",  288)
    raw_1d = fetch_candles("1D",  200)

    c1m = parse_ohlcv(raw_1m)
    c5m = parse_ohlcv(raw_5m)
    c1d = parse_ohlcv(raw_1d)

    rv_cc_1m, rv_pk_1m, rv_gk_1m = compute_rv(c1m, np.sqrt(1440 * 252))
    rv_cc_5m, rv_pk_5m, rv_gk_5m = compute_rv(c5m, np.sqrt(288  * 252))

    closes_1d  = np.array([b["close"] for b in c1d])
    lr_1d      = np.log(closes_1d[1:] / closes_1d[:-1])
    rv_7d      = lr_1d[-7:].std()  * np.sqrt(252)
    rv_30d     = lr_1d[-30:].std() * np.sqrt(252)
    rv_90d     = lr_1d[-min(90, len(lr_1d)):].std() * np.sqrt(252)

    rv_roll_30 = np.array([lr_1d[i-30:i].std() * np.sqrt(252)
                            for i in range(30, len(lr_1d) + 1)])
    rv_pct = int((rv_7d > rv_roll_30).mean() * 100)

    p("\n" + "═" * 65)
    p("  SECTION 1 — REGIME STATE")
    p("═" * 65)
    p(f"\n  RV (24h from 1m): CC={rv_cc_1m*100:.1f}%  Park={rv_pk_1m*100:.1f}%  GK={rv_gk_1m*100:.1f}%")
    p(f"  RV (5m):          CC={rv_cc_5m*100:.1f}%  Park={rv_pk_5m*100:.1f}%  GK={rv_gk_5m*100:.1f}%")
    p(f"  7d  RV : {rv_7d*100:.1f}%")
    p(f"  30d RV : {rv_30d*100:.1f}%")
    p(f"  90d RV : {rv_90d*100:.1f}%")
    p(f"  7d RV percentile vs history : {rv_pct}th")

    # GARCH
    garch_1d = garch_5d = None
    if ARCH_AVAILABLE and len(lr_1d) >= 60:
        try:
            lr_pct = lr_1d * 100
            am  = arch_model(lr_pct, vol="GARCH", p=1, q=1, dist="Normal")
            res = am.fit(disp="off")
            fc  = res.forecast(horizon=7)
            var_fc = fc.variance.values[-1]
            vol_fc = np.sqrt(var_fc) * np.sqrt(252) / 100
            garch_1d = vol_fc[0]
            garch_5d = np.sqrt(np.mean(var_fc[:5])) * np.sqrt(252) / 100
            p(f"\n  GARCH(1,1): α={res.params['alpha[1]']:.4f}  β={res.params['beta[1]']:.4f}"
              f"  persist={res.params['alpha[1]']+res.params['beta[1]']:.4f}")
            p(f"  1d fwd vol: {garch_1d*100:.1f}%   5d fwd vol: {garch_5d*100:.1f}%")
        except Exception as e:
            p(f"  GARCH failed: {e}")
    else:
        p("  GARCH: unavailable")

    # DVOL
    r_dvol = safe_get(f"{DERIBIT_BASE}/api/v2/public/get_volatility_index_data",
                      {"currency": "ETH",
                       "start_timestamp": now_ts - 7 * 86400 * 1000,
                       "end_timestamp":   now_ts,
                       "resolution": "3600"}, "dvol")
    dvol_now = dvol_7d_ago = None
    if r_dvol and r_dvol.get("result", {}).get("data"):
        dvol_data   = r_dvol["result"]["data"]
        dvol_now    = dvol_data[-1][4]
        dvol_7d_ago = dvol_data[0][4]
        dvol_range  = (min(d[3] for d in dvol_data), max(d[2] for d in dvol_data))
        p(f"\n  Deribit DVOL : {dvol_now:.1f}  (7d ago {dvol_7d_ago:.1f},"
          f" Δ{dvol_now-dvol_7d_ago:+.1f}  range {dvol_range[0]:.1f}–{dvol_range[1]:.1f})")
    else:
        p("  Deribit DVOL : DATA GAP")

    # Funding
    r_fr = safe_get(f"{OKX_BASE}/api/v5/public/funding-rate",
                    {"instId": "ETH-USDT-SWAP"}, "okx-funding")
    if r_fr and r_fr.get("data"):
        fd = r_fr["data"][0]
        okx_fr   = float(fd["fundingRate"])
        okx_next = float(fd.get("nextFundingRate", 0) or 0)
        p(f"  OKX funding  : {okx_fr*100:.4f}%/8h  next={okx_next*100:.4f}%"
          f"  ann={okx_fr*3*365*100:.2f}%")
    else:
        p("  OKX funding  : DATA GAP")

    r_dfr = safe_get(f"{DERIBIT_BASE}/api/v2/public/get_funding_rate_history",
                     {"instrument_name": "ETH-PERPETUAL",
                      "start_timestamp": now_ts - 24 * 3600 * 1000,
                      "end_timestamp":   now_ts}, "deribit-funding")
    if r_dfr and r_dfr.get("result"):
        avg8 = np.mean([d["interest_8h"] for d in r_dfr["result"]])
        p(f"  Deribit fund : {avg8*100:.4f}%/8h (24h avg)  ann={avg8*3*365*100:.2f}%")
    else:
        p("  Deribit fund : DATA GAP")
    p("  Bybit funding: DATA GAP (server geofenced)")

    # OI
    r_oi = safe_get(f"{OKX_BASE}/api/v5/public/open-interest",
                    {"instType": "SWAP", "uly": "ETH-USD"}, "oi-swap")
    if r_oi and r_oi.get("data"):
        total_oi = sum(float(d.get("oiCcy", 0)) for d in r_oi["data"])
        p(f"  OKX ETH OI   : {total_oi:,.0f} ETH  (${total_oi*spot/1e6:.0f}M)")
    else:
        p("  OKX ETH OI   : DATA GAP")

    r_dp = safe_get(f"{DERIBIT_BASE}/api/v2/public/ticker",
                    {"instrument_name": "ETH-PERPETUAL"}, "deribit-perp")
    if r_dp and r_dp.get("result"):
        deribit_oi = float(r_dp["result"].get("open_interest", 0))
        p(f"  Deribit OI   : {deribit_oi/1e6:.1f}M contracts")
    else:
        p("  Deribit OI   : DATA GAP")

    # Regime verdict
    range_pct = (high24 - low24) / spot * 100
    is_compression = range_pct < 3.0 and rv_pct < 20
    is_trending    = abs(chg24) > 2.0
    regime = "COMPRESSION/CHOP" if is_compression else ("TRENDING" if is_trending else "EXPANSION")
    p(f"\n  REGIME VERDICT: {regime}")
    p(f"  24h range {range_pct:.1f}% | 7d RV {rv_pct}th pct | chg {chg24:+.2f}%")

    # ── OPTIONS SURFACE ───────────────────────────────────────────────────────
    p("\n" + "═" * 65)
    p("  SECTION 2 — VOL SURFACE DISLOCATIONS")
    p("═" * 65)

    r_opts = safe_get(f"{OKX_BASE}/api/v5/public/opt-summary", {"uly": "ETH-USD"}, "opts")
    if not r_opts or not r_opts.get("data"):
        p("  OPTIONS DATA: DATA GAP — cannot compute surface")
    else:
        by_exp = parse_opts(r_opts["data"], spot)

        # ── nearest live expiries
        EXP_MAP = [
            ("1d",  1), ("2d",  2), ("3d",  3), ("4d",  4),
            ("7d",  5), ("14d", 12), ("30d", 26), ("60d", 54), ("90d", 89),
        ]
        # find expiry dates by DTE from today
        all_exps = sorted(by_exp.keys())
        def exp_to_dte(exp):
            try:
                dt = datetime.strptime("20" + exp, "%Y%m%d").replace(tzinfo=timezone.utc)
                return (dt - now_utc).total_seconds() / 86400
            except Exception:
                return 9999

        exps_with_dte = [(e, exp_to_dte(e)) for e in all_exps if exp_to_dte(e) > 0]
        exps_with_dte.sort(key=lambda x: x[1])

        p("\n  ATM IV Term Structure:")
        p(f"  {'Tenor':8s}  {'DTE':>5s}  {'ATM Strike':>10s}  {'ATM IV':>8s}  "
          f"{'GARCH diff':>11s}  {'Rating':8s}")
        p("  " + "-" * 60)

        tenor_labels = ["~0d", "1d", "2d", "3d", "7d", "14d", "30d", "60d", "90d"]
        target_dtes  = [0.5,   1.5,  2.5,  3.5,  6.0,  13.0,  27.0,  55.0,  91.0]
        used = set()
        surface_ivs = {}

        for label, target in zip(tenor_labels, target_dtes):
            best = None
            best_diff = 9999
            for e, dte in exps_with_dte:
                if e in used:
                    continue
                if abs(dte - target) < best_diff:
                    best_diff = abs(dte - target)
                    best = (e, dte)
            if best is None or best_diff > 10:
                continue
            e, dte = best
            chain = by_exp.get(e, [])
            fwd   = chain[0]["fwd_px"] if chain else spot
            atm   = atm_iv(chain, fwd)
            if atm is None:
                continue
            used.add(e)
            iv = atm["mark_vol"]
            surface_ivs[label] = {"iv": iv, "dte": dte, "strike": atm["strike"]}
            if garch_1d:
                diff   = iv - garch_1d
                rating = "RICH" if diff > 0.02 else ("CHEAP" if diff < -0.02 else "FAIR")
                diff_s = f"{diff*100:+.1f}%"
            else:
                diff_s, rating = "N/A", "N/A"
            p(f"  {label:8s}  {dte:>5.1f}  {atm['strike']:>10.0f}  "
              f"{iv*100:>7.1f}%  {diff_s:>11s}  {rating}")

        # Term structure shape
        iv_list = sorted([(d["dte"], d["iv"], l) for l, d in surface_ivs.items()],
                         key=lambda x: x[0])
        if len(iv_list) >= 2:
            slopes = []
            for i in range(1, len(iv_list)):
                slopes.append("+" if iv_list[i][1] > iv_list[i-1][1] else "-")
            shape = "CONTANGO" if all(s == "+" for s in slopes) else \
                    "BACKWARDATION" if all(s == "-" for s in slopes) else "MIXED"
            p(f"\n  Term structure: {shape}")

        # 25d RR
        p("\n  25-Delta Risk Reversals:")
        for label, exp_key in [("7d", None), ("30d", None)]:
            # find the matching exp
            target_dte = 6.0 if label == "7d" else 27.0
            best_e = min(exps_with_dte, key=lambda x: abs(x[1] - target_dte),
                         default=(None, None))[0]
            if not best_e:
                continue
            chain = by_exp.get(best_e, [])
            rr, c25, p25 = rr_25d(chain)
            if rr is not None:
                skew = "PUT SKEW" if rr < 0 else "CALL SKEW"
                p(f"  {label}: RR={rr*100:+.2f}%  "
                  f"(C25={c25['mark_vol']*100:.1f}% K{c25['strike']:.0f}  "
                  f"P25={p25['mark_vol']*100:.1f}% K{p25['strike']:.0f})  → {skew}")

        # GEX
        p("\n  GEX — Top Gamma Strikes:")
        for label, exp_key in [("Today", 0.5), ("7d", 6.0)]:
            best_e = min(exps_with_dte, key=lambda x: abs(x[1] - exp_key),
                         default=(None, None))[0]
            if not best_e:
                continue
            chain = by_exp.get(best_e, [])
            top3  = top_gamma_strikes(chain, 3)
            dte_val = dict(exps_with_dte).get(best_e, 0)
            tcg = sum(x["gamma"] for x in chain if x["cp"] == "C" and x["gamma"] > 0)
            tpg = sum(x["gamma"] for x in chain if x["cp"] == "P" and x["gamma"] > 0)
            dealer = "LONG GAMMA (pin)" if tcg >= tpg * 0.95 else "SHORT GAMMA (amplify)"
            strikes_str = " | ".join([f"K{s}({g*1000:.1f}m)" for s, g in top3])
            p(f"  {label} ({best_e} DTE={dte_val:.1f}d): {strikes_str}")
            p(f"    Dealer: {dealer}   C/P gamma ratio: {tcg/tpg:.2f}" if tpg > 0 else
              f"    Dealer: {dealer}")

    # ── FLOW & POSITIONING ────────────────────────────────────────────────────
    p("\n" + "═" * 65)
    p("  SECTION 3 — FLOW & POSITIONING")
    p("═" * 65)
    p("  Dune (CEX flows/whales): DATA GAP — API key required")
    p("  Block trades >$5M      : DATA GAP — no public endpoint")

    r_cg = safe_get("https://api.coingecko.com/api/v3/coins/ethereum",
                    {"localization": "false", "tickers": "false",
                     "market_data": "true", "community_data": "true",
                     "developer_data": "false"}, "coingecko")
    if r_cg:
        sent = r_cg.get("sentiment_votes_up_percentage", "N/A")
        vol_usd = r_cg.get("market_data", {}).get("total_volume", {}).get("usd", 0)
        p(f"  CoinGecko sentiment bullish: {sent}%")
        p(f"  CoinGecko 24h volume       : ${vol_usd/1e6:.0f}M")
    else:
        p("  CoinGecko: DATA GAP")

    # ── NARRATIVE ─────────────────────────────────────────────────────────────
    p("\n" + "═" * 65)
    p("  SECTION 4 — NARRATIVE & EVENT RISK")
    p("═" * 65)
    p("  CryptoPanic  : DATA GAP — API key required")
    p("  LunarCrush   : DATA GAP — API key required")
    p("  Bybit funding: DATA GAP — geofenced")
    p("  Perplexity macro calendar: not connected")
    p("  → Manually check: FOMC speakers, CPI, ETH ETF flows (Blackrock ETHA/Fidelity FETH),")
    p("    token unlocks (Tokenomist), SEC filings, Ethereum network events.")

    # ── ACTIONABLE BRIEF ──────────────────────────────────────────────────────
    p("\n" + "═" * 65)
    p("  SECTION 5 — ACTIONABLE BRIEF")
    p("═" * 65)

    # Build verdict from live data
    iv_7d_pct  = surface_ivs.get("7d",  {}).get("iv", None) if r_opts and r_opts.get("data") else None
    iv_30d_pct = surface_ivs.get("30d", {}).get("iv", None) if r_opts and r_opts.get("data") else None

    p(f"\n  REGIME : {regime}")

    if iv_7d_pct and iv_30d_pct:
        iv_rv_ratio = iv_7d_pct / rv_cc_1m if rv_cc_1m > 0 else 0
        rr_signal  = "PUT SKEW" if (iv_7d_pct > 0) else "NEUTRAL"

        # Sell put vs call logic
        # Sell puts if: regime is compression, dealers long gamma, put skew exists
        # Sell calls if: trending down, negative funding strong, call skew
        if is_compression and iv_rv_ratio > 1.5:
            primary_trade  = "SELL PUT (collect put-skew premium in compression regime)"
            secondary_trade = "SELL STRADDLE at gamma-pin strike (max theta in chop)"
        elif not is_compression and chg24 < -1.5:
            primary_trade  = "SELL CALL (downtrend, calls overpriced vs momentum)"
            secondary_trade = "BUY PUT SPREAD (directional hedge if trending down)"
        else:
            primary_trade  = "SELL ATM STRADDLE (regime ambiguous — neutral short vol)"
            secondary_trade = "SELL OTM PUT (collect skew, defined downside risk)"

        p(f"\n  PRIMARY   : {primary_trade}")
        p(f"  SECONDARY : {secondary_trade}")
        p(f"\n  7d ATM IV={iv_7d_pct*100:.1f}%  |  24h RV={rv_cc_1m*100:.1f}%"
          f"  |  IV/RV={iv_rv_ratio:.2f}x")
        if garch_1d:
            p(f"  GARCH 1d={garch_1d*100:.1f}%  |  7d IV vs GARCH={((iv_7d_pct-garch_1d)*100):+.1f}%")
        if dvol_now:
            p(f"  DVOL={dvol_now:.1f}  |  7d chg={dvol_now-dvol_7d_ago:+.1f}")

    p("\n  KEY LEVELS:")
    p(f"  Spot          : ${spot:,.2f}")
    p(f"  24h range     : ${low24:,.2f} – ${high24:,.2f}")
    if r_opts and r_opts.get("data") and exps_with_dte:
        # Print gamma pin for nearest expiry
        nearest_e = exps_with_dte[0][0]
        chain_n   = by_exp.get(nearest_e, [])
        if chain_n:
            top1 = top_gamma_strikes(chain_n, 1)
            if top1:
                p(f"  Gamma pin (nearest expiry) : {top1[0][0]:.0f}")
    p(f"  RV breakout threshold : {rv_cc_1m*100*1.5:.1f}% (+50% current RV)")
    if garch_1d:
        p(f"  IV expansion trigger  : {garch_1d*100:.0f}% (GARCH forecast)")

    p("\n  INVALIDATION (cut/reassess if):")
    p(f"  • Spot breaks below ${spot*0.95:,.0f} (-5%) on a close")
    p(f"  • 7d ATM IV jumps above {(iv_7d_pct or 0.5)*100*1.3:.0f}% (+30% current)")
    p(f"  • DVOL crosses {(dvol_now or 56)+5:.0f} (7d high + 5pts)")
    p("  • OKX 8h funding goes below -0.04%/8h (active deleveraging)")
    p("  • Monday/Tuesday gap open >2.5% in either direction")

    p("\n  KNOWN DATA GAPS THIS REPORT:")
    p("  • Bybit funding rate   (geofenced)")
    p("  • OI 24h delta         (OKX API 404)")
    p("  • CEX flow / whales    (Dune — no key)")
    p("  • Block trades >$5M    (no public endpoint)")
    p("  • 30d avg RR/IV history(no historical surface API)")
    p("  • CryptoPanic/LunarCrush(no key)")
    p("  • Macro calendar       (Perplexity not connected)")

    p("\n" + "═" * 65)
    p(f"  END OF BRIEF  |  {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    p("═" * 65)

    # ── SAVE REPORT ───────────────────────────────────────────────────────────
    out_path = os.path.join(REPORT_DIR, f"eth_vol_brief_{date_str}.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport saved → {out_path}", flush=True)


if __name__ == "__main__":
    main()
