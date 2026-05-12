import time
import requests

BASE_URL = "https://www.deribit.com/api/v2/public"


def get_eth_options_summary():
    resp = requests.get(
        f"{BASE_URL}/get_book_summary_by_currency",
        params={"currency": "ETH", "kind": "option"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]


def get_dvol_history(days=30):
    """
    Returns DVOL daily candles as list of [timestamp_ms, open, high, low, close].
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    resp = requests.get(
        f"{BASE_URL}/get_volatility_index_data",
        params={
            "currency": "ETH",
            "start_timestamp": start_ms,
            "end_timestamp": now_ms,
            "resolution": "1D",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]["data"]


def get_top_10_by_oi(options):
    valid = [o for o in options if o.get("open_interest", 0) > 0]
    return sorted(valid, key=lambda x: x["open_interest"], reverse=True)[:10]


def parse_direction(instrument_name):
    suffix = instrument_name.split("-")[-1]
    if suffix == "C":
        return "BULLISH"
    if suffix == "P":
        return "BEARISH"
    return None


def get_net_flow_direction(options):
    call_oi = sum(o["open_interest"] for o in options if parse_direction(o["instrument_name"]) == "BULLISH")
    put_oi = sum(o["open_interest"] for o in options if parse_direction(o["instrument_name"]) == "BEARISH")
    if call_oi + put_oi == 0:
        return "NEUTRAL"
    return "BULLISH" if call_oi >= put_oi else "BEARISH"
