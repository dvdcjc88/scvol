import logging
import requests
from config import Config

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send(text: str) -> bool:
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set — printing alert to stdout instead")
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60 + "\n")
        return True

    url = TELEGRAM_API.format(token=Config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": Config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def send_sell_puts_alert(ctx: dict) -> None:
    spot = ctx.get("spot", 0)
    dvol = ctx.get("dvol", 0)
    dvol_chg = ctx.get("dvol_1h_chg_pct", 0)
    rv = ctx.get("rv_pct", 0)
    iv = ctx.get("atm_iv", 0)
    funding = ctx.get("funding_rate", 0)
    oi_chg = ctx.get("oi_chg_pct", 0)
    cvd = ctx.get("cvd_usd", 0)
    basis = ctx.get("basis_usd", 0)
    liq_total = ctx.get("liq_15m_usd", 0)
    skew = ctx.get("skew_pts", 0)
    skew_high = ctx.get("skew_high", skew)
    gex_wall = ctx.get("gex_wall_strike", None)
    gex_type = ctx.get("gex_wall_type", "")
    signals = ctx.get("active_signals", [])
    score = ctx.get("score", 0)

    lines = [
        "<b>🔔 ETH REVERSAL — SELL PUTS SETUP</b>",
        f"Score: <b>{score}/7</b>  |  Signals: {', '.join(signals)}",
        "",
        f"<b>Market Snapshot</b>",
        f"ETH Spot:   <code>${spot:,.2f}</code>",
        f"DVOL:       <code>{dvol:.1f}%</code>  ({dvol_chg:+.1f}% past hour)",
        f"ATM IV:     <code>{iv:.1f}%</code>  |  Realized Vol: <code>{rv:.1f}%</code>",
        f"Funding:    <code>{funding*100:.4f}%</code>  |  OI Δ: <code>{oi_chg:+.2f}%</code>",
        f"CVD (perp): <code>${cvd:,.0f}</code>  |  Basis: <code>${basis:+.2f}</code>",
        f"Liqs 15m:   <code>${liq_total:,.0f}</code>",
        f"Put Skew:   <code>{skew:+.1f}pts</code>  (high: {skew_high:+.1f})",
    ]
    if gex_wall:
        lines.append(f"GEX Wall:   <code>${gex_wall:,.0f}</code> ({gex_type})")

    lines += [
        "",
        "<b>Interpretation</b>",
        "Bearish exhaustion across multiple signals. Consider selling",
        "near-dated OTM ETH put spreads to collect elevated IV premium",
        "into a likely reversal. Verify IV rank and liquidity before entry.",
    ]

    send("\n".join(lines))


def send_sell_calls_alert(ctx: dict) -> None:
    spot = ctx.get("spot", 0)
    dvol = ctx.get("dvol", 0)
    dvol_chg = ctx.get("dvol_1h_chg_pct", 0)
    rv = ctx.get("rv_pct", 0)
    iv = ctx.get("atm_iv", 0)
    funding = ctx.get("funding_rate", 0)
    oi_chg = ctx.get("oi_chg_pct", 0)
    cvd = ctx.get("cvd_usd", 0)
    basis = ctx.get("basis_usd", 0)
    liq_total = ctx.get("liq_15m_usd", 0)
    skew = ctx.get("skew_pts", 0)
    gex_wall = ctx.get("gex_wall_strike", None)
    gex_type = ctx.get("gex_wall_type", "")
    signals = ctx.get("active_signals", [])
    score = ctx.get("score", 0)

    lines = [
        "<b>🔔 ETH REVERSAL — SELL CALLS SETUP</b>",
        f"Score: <b>{score}/7</b>  |  Signals: {', '.join(signals)}",
        "",
        f"<b>Market Snapshot</b>",
        f"ETH Spot:   <code>${spot:,.2f}</code>",
        f"DVOL:       <code>{dvol:.1f}%</code>  ({dvol_chg:+.1f}% past hour)",
        f"ATM IV:     <code>{iv:.1f}%</code>  |  Realized Vol: <code>{rv:.1f}%</code>",
        f"Funding:    <code>{funding*100:.4f}%</code>  |  OI Δ: <code>{oi_chg:+.2f}%</code>",
        f"CVD (perp): <code>${cvd:,.0f}</code>  |  Basis: <code>${basis:+.2f}</code>",
        f"Liqs 15m:   <code>${liq_total:,.0f}</code>",
        f"Call Skew:  <code>{-skew:+.1f}pts</code>",
    ]
    if gex_wall:
        lines.append(f"GEX Wall:   <code>${gex_wall:,.0f}</code> ({gex_type})")

    lines += [
        "",
        "<b>Interpretation</b>",
        "Bullish exhaustion across multiple signals. Consider selling",
        "near-dated OTM ETH call spreads. Verify IV rank and liquidity.",
    ]

    send("\n".join(lines))


def send_heartbeat(spot: float, dvol: float, score_puts: int, score_calls: int) -> None:
    msg = (
        f"<b>ETH Alert Monitor — Heartbeat</b>\n"
        f"Spot: <code>${spot:,.2f}</code>  DVOL: <code>{dvol:.1f}%</code>\n"
        f"Sell-puts score: {score_puts}/7  |  Sell-calls score: {score_calls}/7"
    )
    send(msg)
