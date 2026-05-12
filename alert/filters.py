from .deribit import parse_direction


def calculate_iv_rank(dvol_data):
    """
    IV rank from DVOL close prices: (current - 30d_min) / (30d_max - 30d_min) * 100.
    """
    if not dvol_data or len(dvol_data) < 2:
        return None
    closes = [row[4] for row in dvol_data]
    current = closes[-1]
    low_30d = min(closes)
    high_30d = max(closes)
    if high_30d == low_30d:
        return 50.0
    return (current - low_30d) / (high_30d - low_30d) * 100


def filter_by_iv_rank(instruments, dvol_data, max_rank=70):
    """
    Returns (filtered_instruments, market_iv_rank).
    Removes instruments whose per-instrument IV rank (mark_iv vs DVOL range) > max_rank.
    If the whole market IV rank > max_rank, returns empty list immediately.
    """
    iv_rank = calculate_iv_rank(dvol_data)
    if iv_rank is None:
        return instruments, None

    if iv_rank > max_rank:
        return [], iv_rank

    closes = [row[4] for row in dvol_data]
    dvol_min = min(closes)
    dvol_max = max(closes)

    filtered = []
    for inst in instruments:
        mark_iv = inst.get("mark_iv", 0)
        if dvol_max > dvol_min:
            inst_iv_rank = (mark_iv - dvol_min) / (dvol_max - dvol_min) * 100
        else:
            inst_iv_rank = 50.0
        inst["iv_rank"] = inst_iv_rank
        if inst_iv_rank <= max_rank:
            filtered.append(inst)

    return filtered, iv_rank


def filter_by_trend_alignment(instruments, price_trend_direction):
    """
    Keep only instruments whose call/put type aligns with the price trend direction.
    """
    if price_trend_direction == "NEUTRAL":
        return instruments
    return [
        inst for inst in instruments
        if parse_direction(inst["instrument_name"]) == price_trend_direction
    ]
