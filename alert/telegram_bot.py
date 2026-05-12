import os
from datetime import datetime

import pytz
import requests

TELEGRAM_API = "https://api.telegram.org"


def send_alert(instruments, price_direction, price_pct, iv_rank, articles, sentiment, bull_count, bear_count):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    sgt = pytz.timezone("Asia/Singapore")
    now_sgt = datetime.now(sgt).strftime("%d %b %Y")

    arrow = "📈" if price_direction == "BULLISH" else "📉"
    play = "Long CALLS" if price_direction == "BULLISH" else "Long PUTS"

    inst_lines = []
    strikes = []
    for inst in instruments[:5]:
        name = inst["instrument_name"]
        oi = inst.get("open_interest", 0)
        iv = inst.get("mark_iv", 0)
        typ = "CALL" if name.endswith("-C") else "PUT"
        inst_iv_rank = inst.get("iv_rank", iv_rank or 0)
        try:
            strike = int(name.split("-")[2])
            strikes.append(strike)
        except (IndexError, ValueError):
            pass
        inst_lines.append(
            f"  • {name} | OI: {oi:,.0f} ETH | IV: {iv:.1f}% | {typ} | IVR: {inst_iv_rank:.0f}"
        )

    strikes_str = ", ".join(str(s) for s in sorted(set(strikes))) if strikes else "N/A"
    inst_block = "\n".join(inst_lines) if inst_lines else "  • No instruments"

    news_lines = [f"  • [{a['source']}] {a['title'][:80]}" for a in articles[:5]]
    news_block = "\n".join(news_lines) if news_lines else "  • No recent ETH news"
    total_classified = bull_count + bear_count + 0

    iv_rank_display = f"{iv_rank:.0f}" if iv_rank is not None else "N/A"

    msg = (
        f"🚨 ETH OPTIONS ALERT — {now_sgt} 5AM SGT\n\n"
        f"DIRECTION: {price_direction} {arrow}\n\n"
        f"📊 TOP INSTITUTIONAL FLOW:\n{inst_block}\n\n"
        f"{arrow} PRICE TREND (7d): {price_pct:+.2f}% → {price_direction}\n\n"
        f"📰 NEWS SENTIMENT: {sentiment} "
        f"({bull_count} bullish / {bear_count} bearish / {total_classified} total)\n"
        f"{news_block}\n\n"
        f"⚡ TRADE SETUP:\n"
        f"  • Play: {play}\n"
        f"  • Key strikes: {strikes_str}\n"
        f"  • Market IV Rank: {iv_rank_display} (≤70 ✓)\n\n"
        f"⚠️ Not financial advice. Do your own research."
    )

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=30)
    resp.raise_for_status()
    print(f"[telegram] Alert sent. Message ID: {resp.json()['result']['message_id']}")
