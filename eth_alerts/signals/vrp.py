"""
Signal 2: Volatility Risk Premium (VRP) compression.

VRP = ATM IV − Realized Vol (both annualized %).

When RV/IV ratio is high (≥ threshold), the "gamma suppression" from
dealer hedging is breaking — the move has been fully priced in and vol
is ripe to mean-revert. This improves premium-selling conditions.

High RV/IV on a dump → sell puts (IV elevated, RV catching up = reversal).
High RV/IV on a pump → sell calls.
"""
from typing import Optional
from config import Config


class VrpSignal:
    name = "VRP"

    def check(
        self,
        rv_pct: Optional[float],
        atm_iv: Optional[float],
        price_chg_pct: float,
    ) -> tuple[int, int, dict]:
        detail: dict = {
            "rv_pct": rv_pct,
            "atm_iv": atm_iv,
            "vrp_pts": None,
            "rv_iv_ratio": None,
        }

        if rv_pct is None or atm_iv is None or atm_iv <= 0:
            return 0, 0, detail

        vrp = atm_iv - rv_pct
        ratio = rv_pct / atm_iv * 100.0
        detail["vrp_pts"] = round(vrp, 2)
        detail["rv_iv_ratio"] = round(ratio, 1)

        triggered = ratio >= Config.VRP_RATIO_THRESHOLD

        score_puts = 0
        score_calls = 0
        if triggered:
            if price_chg_pct < -0.5:
                score_puts = 1
            elif price_chg_pct > 0.5:
                score_calls = 1
            else:
                score_puts = 1

        return score_puts, score_calls, detail
