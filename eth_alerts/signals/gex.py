"""
Signal 7 (bonus): Gamma Exposure (GEX) wall proximity.

GEX per option = Gamma × OI × Spot² × 0.01
  Calls: positive GEX (dealers long gamma → suppresses vol at that strike)
  Puts:  negative GEX (dealers short gamma → amplifies moves below)

Gamma walls = strikes with highest |GEX|.

Positive GEX wall BELOW spot → strong support; reversal high-conviction.
Positive GEX wall ABOVE spot → strong resistance; sells calls setup.
"""
import math
from typing import Optional
from config import Config


def calc_gex_profile(chain: list[dict], spot: float) -> dict:
    """
    Returns {
      "by_strike": {strike: net_gex_usd, ...},
      "total_gex": float,
      "top_walls": [(strike, gex), ...],   # top 3 by |gex|
      "zero_gex_strike": float | None,      # nearest strike where gex flips sign
    }
    """
    by_strike: dict[float, float] = {}

    for opt in chain:
        gamma = opt.get("gamma", 0)
        oi = opt.get("oi", 0)
        opt_type = opt.get("type", "")
        if gamma <= 0 or oi <= 0:
            continue
        gex = gamma * oi * spot * spot * 0.01
        sign = 1 if opt_type == "C" else -1
        strike = opt["strike"]
        by_strike[strike] = by_strike.get(strike, 0.0) + sign * gex

    if not by_strike:
        return {"by_strike": {}, "total_gex": 0, "top_walls": [], "zero_gex_strike": None}

    total_gex = sum(by_strike.values())

    # Top walls by absolute GEX
    sorted_walls = sorted(by_strike.items(), key=lambda x: abs(x[1]), reverse=True)
    top_walls = sorted_walls[:5]

    # Zero GEX flip: find adjacent strikes that cross zero
    strikes_sorted = sorted(by_strike.keys())
    zero_strike: Optional[float] = None
    for i in range(len(strikes_sorted) - 1):
        s0, s1 = strikes_sorted[i], strikes_sorted[i + 1]
        g0, g1 = by_strike[s0], by_strike[s1]
        if g0 * g1 < 0:
            # Linear interpolation
            zero_strike = s0 + (s1 - s0) * abs(g0) / (abs(g0) + abs(g1))
            break

    return {
        "by_strike": by_strike,
        "total_gex": round(total_gex / 1e6, 2),  # in $M
        "top_walls": top_walls,
        "zero_gex_strike": round(zero_strike, 0) if zero_strike else None,
    }


class GexSignal:
    name = "GEX"

    def check(
        self,
        gex_profile: dict,
        spot: float,
    ) -> tuple[int, int, dict]:
        top_walls = gex_profile.get("top_walls", [])
        zero_strike = gex_profile.get("zero_gex_strike")
        total_gex = gex_profile.get("total_gex", 0)

        detail: dict = {
            "total_gex_m": total_gex,
            "zero_gex_strike": zero_strike,
            "top_walls": [(s, round(g / 1e6, 2)) for s, g in top_walls[:3]],
            "gex_wall_strike": None,
            "gex_wall_type": None,
        }

        score_puts = 0
        score_calls = 0

        prox = Config.GEX_PROXIMITY_PCT / 100.0

        for strike, gex in top_walls[:3]:
            distance_pct = abs(strike - spot) / spot
            if distance_pct > prox:
                continue

            detail["gex_wall_strike"] = strike
            is_positive_wall = gex > 0

            if strike < spot and is_positive_wall:
                # Strong positive GEX support just below — confirms reversal
                detail["gex_wall_type"] = "SUPPORT (+GEX)"
                score_puts = 1

            elif strike > spot and is_positive_wall:
                # Positive GEX resistance above — confirms ceiling
                detail["gex_wall_type"] = "RESISTANCE (+GEX)"
                score_calls = 1

            elif strike <= spot and not is_positive_wall:
                # Negative GEX below — move could accelerate further down; caution
                detail["gex_wall_type"] = "NEG-GEX (amplifier)"

            break  # use closest wall only

        return score_puts, score_calls, detail
