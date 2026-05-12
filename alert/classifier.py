import json
import os
import re

import google.generativeai as genai


def classify_news_sentiment(articles):
    """
    Classify ETH news articles via Gemini.
    Returns (direction, bullish_count, bearish_count, neutral_count).
    direction is BULLISH, BEARISH, or NEUTRAL.
    """
    if not articles:
        return "NEUTRAL", 0, 0, 0

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-1.5-flash")

    articles_text = "\n".join(
        f"{i + 1}. [{a['source']}] {a['title']}: {a['summary']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""You are a crypto market analyst. Classify each news article below as BULLISH, BEARISH, or NEUTRAL for Ethereum (ETH) price in the next 1-7 days.

Articles:
{articles_text}

Respond with ONLY a JSON array like:
[{{"index": 1, "sentiment": "BULLISH"}}, {{"index": 2, "sentiment": "BEARISH"}}, ...]

No explanation. JSON only."""

    response = model.generate_content(prompt)
    text = response.text.strip()

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return "NEUTRAL", 0, 0, 0

    results = json.loads(match.group())
    bullish = sum(1 for r in results if r.get("sentiment") == "BULLISH")
    bearish = sum(1 for r in results if r.get("sentiment") == "BEARISH")
    neutral = sum(1 for r in results if r.get("sentiment") == "NEUTRAL")

    if bullish > bearish:
        direction = "BULLISH"
    elif bearish > bullish:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    return direction, bullish, bearish, neutral
