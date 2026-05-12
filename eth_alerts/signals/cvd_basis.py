"""
Signal 4: CVD (Cumulative Volume Delta) + Spot-Perp basis.

Cleanest reversal print for sell-puts:
  Spot leading perps DOWN (basis negative) + strongly negative CVD
  → Aggressive taker selling into a GEX zone = exhaustion wick.

Sell-calls:
  Spot leading perps UP (basis positive) + strongly positive CVD
  → Aggressive taker buying into resistance = blow-off top.
"""
from typing import Optional
from config import Config


class CvdBasisSignal:
    name = "CVD+Basis"

    def check(
        self,
        cvd_usd: Optional[float],
        spot_price: Optional[float],
        perp_price: Optional[float],
    ) -> tuple[int, int, dict]:
        detail: dict = {
            "cvd_usd": cvd_usd,
            "spot": spot_price,
            "perp": perp_price,
            "basis_usd": None,
        }

        if cvd_usd is None or spot_price is None or perp_price is None:
            return 0, 0, detail

        basis = spot_price - perp_price  # negative → spot below perp
        detail["basis_usd"] = round(basis, 2)

        score_puts = 0
        score_calls = 0

        # Spot below perp (spot leading down) + aggressive selling
        if basis <= -Config.BASIS_DISCOUNT_USD and cvd_usd <= Config.CVD_NEGATIVE_USD:
            score_puts = 1

        # Spot above perp (spot leading up) + aggressive buying
        if basis >= Config.BASIS_PREMIUM_USD and cvd_usd >= Config.CVD_POSITIVE_USD:
            score_calls = 1

        return score_puts, score_calls, detail
