#!/usr/bin/env python3
"""
One-time setup: discovers your Telegram chat ID and saves it,
then sends a test alert to confirm delivery.

Usage:
  1. Open Telegram, search for @volakoy_bot, send any message
  2. Run: python3 setup_telegram.py
"""

import json
import time
import requests
from pathlib import Path

BOT_TOKEN  = "8765446376:AAE9CpY4nX6zhH90GAKZAOhCsjELZs38fn4"
STATE_FILE = Path(__file__).parent / ".eth_alert_state.json"


def tg(method, **params):
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        params=params, timeout=10,
    )
    return r.json()


def main():
    print("Checking bot identity…")
    me = tg("getMe")
    if not me.get("ok"):
        print(f"ERROR: {me}")
        return
    print(f"Bot: @{me['result']['username']} (id={me['result']['id']})")

    print("\nFetching updates…")
    updates = tg("getUpdates", limit=20, timeout=0)
    msgs = updates.get("result", [])

    chat_id = None
    for upd in reversed(msgs):
        msg  = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            chat_id = str(chat["id"])
            name = chat.get("first_name") or chat.get("title") or "?"
            print(f"Found chat: {name} (id={chat_id})")
            break

    if not chat_id:
        print("\n⚠  No messages found.")
        print("   → Open Telegram, search @volakoy_bot, send any message, then re-run this script.")
        return

    # Save to state file
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    state["chat_id"] = chat_id
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"\nChat ID saved to {STATE_FILE}")

    # Send test message
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": (
                "✅ <b>ETH Reversal Alert Bot — Setup Complete</b>\n\n"
                "Your bot is configured. You will receive alerts every time "
                "ETH/USDT shows a high-probability reversal signal on the 1H+4H charts.\n\n"
                "🔍 Indicators monitored:\n"
                "• RSI (oversold/overbought + divergence)\n"
                "• MACD crossover + histogram momentum\n"
                "• Bollinger Bands (band touch + squeeze)\n"
                "• Stochastic %K/%D crossover\n"
                "• EMA 9/21 golden/death cross\n"
                "• Volume surge confirmation\n"
                "• Candlestick patterns (Hammer, Engulfing, Morning/Evening Star…)\n\n"
                "🔄 Checks every 5 min | 1-hour alert cooldown per direction\n"
                "📊 Minimum confluence score: ±4 across 1H+4H\n\n"
                "⚠️ <i>Not financial advice. Always use stop-loss.</i>"
            ),
            "parse_mode": "HTML",
        },
        timeout=10,
    )
    data = r.json()
    if data.get("ok"):
        print("Test message sent! Check Telegram.")
    else:
        print(f"Send failed: {data.get('description')}")


if __name__ == "__main__":
    main()
