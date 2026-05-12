"""
ETH Reversal Alert Monitor
==========================
Polls Deribit + Binance every POLL_INTERVAL seconds, evaluates 7 reversal
signals, and fires Telegram messages when ≥ MIN_SIGNALS align.

Usage:
    pip install -r requirements.txt
    cp .env.example .env          # fill in tokens
    python main.py

Required env vars:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Optional:           COINGLASS_API_KEY (not yet used), all threshold overrides
"""
import logging
import sys
import time
from datetime import datetime, timezone

import schedule

from composite import build_context, score_signals
from config import Config
from data import binance, deribit
from data.binance import LiquidationTracker
from signals.cvd_basis import CvdBasisSignal
from signals.dvol_term import DvolTermSignal
from signals.funding_oi import FundingOiSignal
from signals.gex import GexSignal, calc_gex_profile
from signals.liquidations import LiquidationSignal
from signals.skew import SkewSignal
from signals.vrp import VrpSignal
from state import State
import telegram_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

# ── Singleton signal checkers ─────────────────────────────────────────
_dvol_sig = DvolTermSignal()
_vrp_sig = VrpSignal()
_fund_sig = FundingOiSignal()
_cvd_sig = CvdBasisSignal()
_liq_sig = LiquidationSignal()
_skew_sig = SkewSignal()
_gex_sig = GexSignal()


def poll(state: State) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    log.info("── Poll %s ──────────────────────────────────", ts)

    # ── 1. Fetch all market data ──────────────────────────────────────
    spot = deribit.get_eth_spot() or binance.get_spot_price()
    if spot is None:
        log.warning("Could not fetch ETH spot price — skipping cycle")
        return

    dvol = deribit.get_dvol()
    if dvol is None:
        log.warning("DVOL unavailable — skipping cycle")
        return

    # Parallel-ish fetches (sync, but fast enough at 60s interval)
    klines = binance.get_klines(interval="1m", limit=480)
    perp_price = binance.get_perp_price()
    funding_rate = binance.get_funding_rate()
    oi_now = binance.get_open_interest()
    agg_trades = binance.get_agg_trades(limit=1000)
    chain = deribit.get_options_chain()

    # Derived
    rv_pct = binance.calc_realized_vol_pct(klines, window=480) if klines else None
    cvd_usd = binance.calc_cvd_usd(agg_trades) if agg_trades else None
    basis_usd = (spot - perp_price) if (spot and perp_price) else None
    term_structure = deribit.parse_term_structure(chain, spot) if chain else []
    skew_pts = deribit.get_25d_skew(chain, spot) if chain else None

    atm_iv = term_structure[0]["atm_iv"] if term_structure else None

    # DVOL history: try API first, fall back to in-memory
    dvol_history = deribit.get_dvol_history(lookback_hours=2)
    dvol_1h_ago: float | None = None
    if len(dvol_history) >= 2:
        dvol_1h_ago = dvol_history[-2]["close"]  # previous hourly close
    elif len(state.dvol_history) >= 60:
        dvol_1h_ago = state.dvol_history[-60]    # 60 polls ago ≈ 1h at 60s interval

    # OI delta
    oi_prev = state.prev_oi
    oi_chg_pct: float | None = None
    if oi_now and oi_prev and oi_prev > 0:
        oi_chg_pct = (oi_now - oi_prev) / oi_prev * 100.0

    # Update state
    prev_dvol = state.push_dvol(dvol)
    state.push_price(spot)
    state.prev_oi = oi_now
    if skew_pts is not None:
        state.push_skew(skew_pts)
    state.prune_liqs(keep_minutes=60)

    price_chg_pct = state.price_change_pct(lookback=10)

    # GEX profile
    gex_profile = calc_gex_profile(chain, spot) if chain else {"by_strike": {}, "total_gex": 0, "top_walls": [], "zero_gex_strike": None}

    log.info(
        "spot=%.2f dvol=%.1f rv=%.1f iv=%.1f fund=%s oi_chg=%s cvd=%s basis=%s skew=%s",
        spot, dvol,
        rv_pct or -1, atm_iv or -1,
        f"{funding_rate:.5f}" if funding_rate is not None else "N/A",
        f"{oi_chg_pct:+.2f}%" if oi_chg_pct is not None else "N/A",
        f"${cvd_usd:,.0f}" if cvd_usd is not None else "N/A",
        f"{basis_usd:+.2f}" if basis_usd is not None else "N/A",
        f"{skew_pts:+.1f}" if skew_pts is not None else "N/A",
    )

    # ── 2. Evaluate signals ───────────────────────────────────────────
    liq_events = state.get_recent_liqs(minutes=Config.LIQ_WINDOW_MIN)
    skew_high = state.skew_recent_high()
    dvol_1h_chg_pct = ((dvol - dvol_1h_ago) / dvol_1h_ago * 100) if dvol_1h_ago and dvol_1h_ago > 0 else None

    dvol_res = _dvol_sig.check(dvol, dvol_1h_ago, term_structure, price_chg_pct)
    vrp_res = _vrp_sig.check(rv_pct, atm_iv, price_chg_pct)
    fund_res = _fund_sig.check(funding_rate, oi_now, oi_prev)
    cvd_res = _cvd_sig.check(cvd_usd, spot, perp_price)
    liq_res = _liq_sig.check(liq_events)
    skew_res = _skew_sig.check(skew_pts, skew_high)
    gex_res = _gex_sig.check(gex_profile, spot)

    # ── 3. Score ──────────────────────────────────────────────────────
    score_puts, score_calls, active_puts, active_calls = score_signals(
        dvol_res, vrp_res, fund_res, cvd_res, liq_res, skew_res, gex_res
    )

    log.info("Scores → sell-puts: %d/7  sell-calls: %d/7", score_puts, score_calls)
    if active_puts:
        log.info("  Puts signals:  %s", ", ".join(active_puts))
    if active_calls:
        log.info("  Calls signals: %s", ", ".join(active_calls))

    # GEX detail for alert message
    gex_detail = gex_res[2]
    liq_total_usd = sum(e["usd"] for e in liq_events)

    ctx = build_context(
        spot=spot,
        dvol=dvol,
        dvol_1h_chg_pct=dvol_1h_chg_pct,
        atm_iv=atm_iv,
        rv_pct=rv_pct,
        funding_rate=funding_rate,
        oi_chg_pct=oi_chg_pct,
        cvd_usd=cvd_usd,
        basis_usd=basis_usd,
        liq_15m_usd=liq_total_usd,
        skew_pts=skew_pts,
        skew_high=skew_high,
        gex_wall_strike=gex_detail.get("gex_wall_strike"),
        gex_wall_type=gex_detail.get("gex_wall_type"),
        total_gex_m=gex_profile.get("total_gex", 0),
        zero_gex_strike=gex_profile.get("zero_gex_strike"),
    )

    # ── 4. Fire alerts ────────────────────────────────────────────────
    if score_puts >= Config.MIN_SIGNALS and state.can_alert_sell_puts():
        log.info("*** SELL PUTS ALERT firing (score=%d) ***", score_puts)
        ctx["score"] = score_puts
        ctx["active_signals"] = active_puts
        telegram_bot.send_sell_puts_alert(ctx)
        state.mark_sell_puts()

    if score_calls >= Config.MIN_SIGNALS and state.can_alert_sell_calls():
        log.info("*** SELL CALLS ALERT firing (score=%d) ***", score_calls)
        ctx["score"] = score_calls
        ctx["active_signals"] = active_calls
        telegram_bot.send_sell_calls_alert(ctx)
        state.mark_sell_calls()


def main() -> None:
    log.info("ETH Reversal Alert Monitor starting up")
    log.info(
        "Config: poll=%ds cooldown=%dm min_signals=%d",
        Config.POLL_INTERVAL, Config.COOLDOWN_MINUTES, Config.MIN_SIGNALS,
    )

    if not Config.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set — alerts will print to stdout")
    if not Config.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID not set — alerts will print to stdout")

    state = State()

    # Start liquidation WebSocket tracker in background
    liq_tracker = LiquidationTracker(state)
    liq_tracker.start()

    # Run first poll immediately
    poll(state)

    # Schedule subsequent polls
    schedule.every(Config.POLL_INTERVAL).seconds.do(poll, state=state)

    # Heartbeat every hour so you know the bot is alive
    schedule.every(1).hour.do(
        lambda: telegram_bot.send_heartbeat(
            state.price_history[-1] if state.price_history else 0,
            state.dvol_history[-1] if state.dvol_history else 0,
            0, 0,  # live scores not cached; heartbeat is informational
        )
    )

    log.info("Scheduler running. Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down.")
        liq_tracker.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
