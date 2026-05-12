import requests

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart"


def get_eth_price_trend_7d():
    """
    Returns (direction, pct_change): direction is BULLISH or BEARISH,
    pct_change is the 7-day percentage move.
    """
    resp = requests.get(
        COINGECKO_URL,
        params={"vs_currency": "usd", "days": "7"},
        timeout=30,
    )
    resp.raise_for_status()
    prices = resp.json()["prices"]  # [[timestamp_ms, price], ...]
    if not prices or len(prices) < 2:
        return "NEUTRAL", 0.0
    start_price = prices[0][1]
    end_price = prices[-1][1]
    pct_change = (end_price - start_price) / start_price * 100
    direction = "BULLISH" if pct_change >= 0 else "BEARISH"
    return direction, pct_change
