"""
Signal 5: Liquidation sweep.

A large cluster of long liquidations near a gamma wall is a classic
sweep-and-reverse pattern: stops are hunted, weak hands flushed,
and then price snaps back.

Long liqs (SELL side forced close) → longs wiped → sell-puts setup
Short liqs (BUY side forced close) → shorts wiped → sell-calls setup
"""
from config import Config


class LiquidationSignal:
    name = "LiqSweep"

    def check(
        self,
        liq_events: list[dict],  # from State.get_recent_liqs()
    ) -> tuple[int, int, dict]:
        total_usd = sum(e["usd"] for e in liq_events)
        long_liq_usd = sum(e["usd"] for e in liq_events if e.get("side") == "SELL")
        short_liq_usd = sum(e["usd"] for e in liq_events if e.get("side") == "BUY")

        detail: dict = {
            "total_liq_usd": total_usd,
            "long_liq_usd": long_liq_usd,
            "short_liq_usd": short_liq_usd,
            "event_count": len(liq_events),
        }

        score_puts = 0
        score_calls = 0
        threshold = Config.LIQ_THRESHOLD_USD

        if long_liq_usd >= threshold:
            score_puts = 1  # longs wiped → squeeze from here

        if short_liq_usd >= threshold:
            score_calls = 1  # shorts wiped → gravity reasserts

        return score_puts, score_calls, detail
