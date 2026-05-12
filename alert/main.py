from .classifier import classify_news_sentiment
from .deribit import (
    get_dvol_history,
    get_eth_options_summary,
    get_net_flow_direction,
    get_top_10_by_oi,
    parse_direction,
)
from .filters import filter_by_iv_rank, filter_by_trend_alignment
from .news import fetch_recent_eth_news
from .price import get_eth_price_trend_7d
from .telegram_bot import send_alert


def run():
    print("[main] Starting ETH options alert pipeline...")

    # Step 1 — Deribit: top 10 by OI + net flow direction
    print("[main] Fetching Deribit ETH options...")
    all_options = get_eth_options_summary()
    top10 = get_top_10_by_oi(all_options)
    flow_direction = get_net_flow_direction(all_options)
    print(f"[main] {len(top10)} top instruments. Market flow: {flow_direction}")

    # Step 2 — IV rank filter
    print("[main] Fetching DVOL history...")
    dvol_data = get_dvol_history(days=30)
    filtered, iv_rank = filter_by_iv_rank(top10, dvol_data, max_rank=70)
    iv_rank_str = f"{iv_rank:.1f}" if iv_rank is not None else "N/A"
    print(f"[main] Market IV rank: {iv_rank_str}. After IV filter: {len(filtered)} instruments")

    if not filtered:
        print("[main] Market IV rank > 70 — options overpriced. No alert sent.")
        return

    # Step 3 — Price trend alignment filter
    print("[main] Fetching ETH 7-day price trend...")
    price_direction, price_pct = get_eth_price_trend_7d()
    print(f"[main] Price trend: {price_direction} ({price_pct:+.2f}%)")

    filtered = filter_by_trend_alignment(filtered, price_direction)
    print(f"[main] After trend alignment filter: {len(filtered)} instruments")

    if not filtered:
        print("[main] No instruments align with price trend. No alert sent.")
        return

    # Confirm market flow aligns with price trend
    if flow_direction not in (price_direction, "NEUTRAL"):
        print(f"[main] Net flow ({flow_direction}) conflicts with price trend ({price_direction}). No alert sent.")
        return

    # Step 4 — News sentiment via Gemini
    print("[main] Fetching recent ETH news...")
    articles = fetch_recent_eth_news(hours=24)
    print(f"[main] {len(articles)} ETH articles found. Classifying with Gemini...")
    sentiment, bull_count, bear_count, neutral_count = classify_news_sentiment(articles)
    print(f"[main] Sentiment: {sentiment} (bull:{bull_count} bear:{bear_count} neutral:{neutral_count})")

    if sentiment not in (price_direction, "NEUTRAL"):
        print(f"[main] News sentiment ({sentiment}) conflicts with flow/trend ({price_direction}). No alert sent.")
        return

    # Step 5 — Send Telegram alert
    print("[main] All signals aligned. Sending Telegram alert...")
    send_alert(filtered, price_direction, price_pct, iv_rank, articles, sentiment, bull_count, bear_count)
    print("[main] Done.")


if __name__ == "__main__":
    run()
