"""
Signal 3: Perp funding rate + Open Interest delta.

Sell-puts setup (squeeze trigger):
  Funding negative (shorts paying longs) AND OI rising
  → Fresh shorts loading into a high-GEX zone = classic short-squeeze setup.
  Elevated put premium; reversal likely when longs hold.

Sell-calls setup (long exhaustion):
  Funding strongly positive (longs paying shorts) AND OI rising
  → Leveraged longs at risk; reversal from top.
"""
from typing import Optional
from config import Config


class FundingOiSignal:
    name = "Funding+OI"

    def check(
        self,
        funding_rate: Optional[float],
        oi_now: Optional[float],
        oi_prev: Optional[float],
    ) -> tuple[int, int, dict]:
        detail: dict = {
            "funding_rate": funding_rate,
            "oi_now": oi_now,
            "oi_prev": oi_prev,
            "oi_chg_pct": None,
        }

        if funding_rate is None or oi_now is None:
            return 0, 0, detail

        oi_chg_pct = 0.0
        if oi_prev and oi_prev > 0:
            oi_chg_pct = (oi_now - oi_prev) / oi_prev * 100.0
            detail["oi_chg_pct"] = round(oi_chg_pct, 3)

        oi_rising = oi_chg_pct >= Config.OI_RISING_PCT

        score_puts = 0
        score_calls = 0

        # Negative funding + rising OI = fresh shorts = potential squeeze
        if funding_rate <= Config.FUNDING_NEGATIVE and oi_rising:
            score_puts = 1

        # Positive funding + rising OI = leveraged longs = blow-off top risk
        if funding_rate >= Config.FUNDING_POSITIVE and oi_rising:
            score_calls = 1

        return score_puts, score_calls, detail
