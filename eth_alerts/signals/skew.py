"""
Signal 6: 0DTE / front-expiry skew compression or elevation.

Put skew = put_25d_iv − call_25d_iv (positive = puts bid up = fear).

Sell-puts:  Sudden PUT SKEW COMPRESSION after a flush.
            Skew drops significantly from its recent high → market
            stops buying downside protection → bearish exhaustion.

Sell-calls: Elevated CALL SKEW (call_iv > put_iv, i.e., negative skew_pts)
            → Crowd chasing upside → blow-off top risk.
"""
from typing import Optional
from config import Config


class SkewSignal:
    name = "Skew"

    def check(
        self,
        skew_pts: Optional[float],  # put_25d_iv - call_25d_iv
        skew_recent_high: Optional[float],
    ) -> tuple[int, int, dict]:
        detail: dict = {
            "skew_pts": skew_pts,
            "skew_recent_high": skew_recent_high,
            "skew_drop": None,
        }

        score_puts = 0
        score_calls = 0

        if skew_pts is None:
            return 0, 0, detail

        # Put skew compression: current significantly below recent high
        if skew_recent_high is not None:
            drop = skew_recent_high - skew_pts
            detail["skew_drop"] = round(drop, 2)
            if drop >= Config.SKEW_COMPRESSION_PTS and skew_pts >= 0:
                score_puts = 1  # put skew compressing after flush

        # Call skew elevated (negative put_25d - call_25d)
        if skew_pts is not None and skew_pts <= -Config.SKEW_COMPRESSION_PTS:
            score_calls = 1  # calls bid up relative to puts

        return score_puts, score_calls, detail
