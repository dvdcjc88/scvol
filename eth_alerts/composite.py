"""
Composite signal scorer.

Aggregates individual signal scores into a single directional verdict.
Returns a context dict consumed by telegram_bot for alert formatting.
"""
from typing import Optional


def build_context(
    spot: float,
    dvol: float,
    dvol_1h_chg_pct: Optional[float],
    atm_iv: Optional[float],
    rv_pct: Optional[float],
    funding_rate: Optional[float],
    oi_chg_pct: Optional[float],
    cvd_usd: Optional[float],
    basis_usd: Optional[float],
    liq_15m_usd: float,
    skew_pts: Optional[float],
    skew_high: Optional[float],
    gex_wall_strike: Optional[float],
    gex_wall_type: Optional[str],
    total_gex_m: float,
    zero_gex_strike: Optional[float],
) -> dict:
    return {
        "spot": spot,
        "dvol": dvol,
        "dvol_1h_chg_pct": dvol_1h_chg_pct or 0.0,
        "atm_iv": atm_iv or 0.0,
        "rv_pct": rv_pct or 0.0,
        "funding_rate": funding_rate or 0.0,
        "oi_chg_pct": oi_chg_pct or 0.0,
        "cvd_usd": cvd_usd or 0.0,
        "basis_usd": basis_usd or 0.0,
        "liq_15m_usd": liq_15m_usd,
        "skew_pts": skew_pts or 0.0,
        "skew_high": skew_high or 0.0,
        "gex_wall_strike": gex_wall_strike,
        "gex_wall_type": gex_wall_type or "",
        "total_gex_m": total_gex_m,
        "zero_gex_strike": zero_gex_strike,
    }


def score_signals(
    dvol_res: tuple,
    vrp_res: tuple,
    funding_res: tuple,
    cvd_res: tuple,
    liq_res: tuple,
    skew_res: tuple,
    gex_res: tuple,
) -> tuple[int, int, list[str], list[str]]:
    """
    Aggregates (score_puts, score_calls, detail) tuples from each signal.
    Returns (total_puts, total_calls, active_puts_names, active_calls_names).
    """
    signal_names = [
        "DVOL+TermStruct", "VRP", "Funding+OI", "CVD+Basis", "LiqSweep", "Skew", "GEX"
    ]
    results = [dvol_res, vrp_res, funding_res, cvd_res, liq_res, skew_res, gex_res]

    total_puts = 0
    total_calls = 0
    active_puts: list[str] = []
    active_calls: list[str] = []

    for name, res in zip(signal_names, results):
        sp, sc = res[0], res[1]
        total_puts += sp
        total_calls += sc
        if sp:
            active_puts.append(name)
        if sc:
            active_calls.append(name)

    return total_puts, total_calls, active_puts, active_calls
