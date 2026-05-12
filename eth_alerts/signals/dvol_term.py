"""
Signal 1: DVOL spike + IV term structure inversion (backwardation).

Sell-puts trigger:  DVOL spiked significantly AND front IV > back IV.
                    → Real panic move, not noise; IV likely to mean-revert.

Sell-calls trigger: DVOL spiked on the UP move AND term structure inverted.
                    → Euphoric/forced buying exhausting itself.
"""
from typing import Optional
from config import Config


class DvolTermSignal:
    name = "DVOL+TermStruct"

    # Returns (sell_puts_score, sell_calls_score, detail_dict)
    def check(
        self,
        dvol_now: float,
        dvol_1h_ago: Optional[float],
        term_structure: list[dict],  # [{dte_days, atm_iv}, ...] sorted by dte
        price_chg_pct: float,        # recent % price change (neg = dump, pos = pump)
    ) -> tuple[int, int, dict]:
        detail: dict = {
            "dvol_now": dvol_now,
            "dvol_1h_ago": dvol_1h_ago,
            "dvol_spike_pct": None,
            "backwardation": False,
            "front_iv": None,
            "back_iv": None,
        }

        # ── DVOL spike ────────────────────────────────────────────────
        spike_pct = 0.0
        if dvol_1h_ago and dvol_1h_ago > 0:
            spike_pct = (dvol_now - dvol_1h_ago) / dvol_1h_ago * 100
            detail["dvol_spike_pct"] = round(spike_pct, 2)
        is_spike = spike_pct >= Config.DVOL_SPIKE_PCT

        # ── Term structure (backwardation) ────────────────────────────
        is_backwardation = False
        if len(term_structure) >= 2:
            front = term_structure[0]["atm_iv"]
            back = term_structure[1]["atm_iv"]
            detail["front_iv"] = front
            detail["back_iv"] = back
            if front - back >= Config.DVOL_BACKWARDATION_PTS:
                is_backwardation = True
                detail["backwardation"] = True

        # ── Scoring ───────────────────────────────────────────────────
        # Both conditions together = strong signal; each alone = partial
        score_puts = 0
        score_calls = 0

        if is_spike and is_backwardation:
            if price_chg_pct < -1.0:   # dump scenario → sell puts
                score_puts = 1
            elif price_chg_pct > 1.0:  # pump scenario → sell calls
                score_calls = 1
            else:
                # Ambiguous direction — credit both mildly
                score_puts = 1
                score_calls = 1
        elif is_spike or is_backwardation:
            score_puts = 1 if price_chg_pct <= 0 else 0
            score_calls = 1 if price_chg_pct > 0 else 0

        return score_puts, score_calls, detail
