from datetime import datetime, timedelta
from typing import Optional
from config import Config


class State:
    """Mutable runtime state shared across poll cycles."""

    def __init__(self) -> None:
        self._last_sell_puts: Optional[datetime] = None
        self._last_sell_calls: Optional[datetime] = None

        # Previous-cycle values used for delta calculations
        self.prev_oi: Optional[float] = None
        self.prev_dvol: Optional[float] = None

        # Rolling history for trend detection
        self.skew_history: list[float] = []   # put-call skew readings
        self.dvol_history: list[float] = []   # DVOL readings (1 per poll)
        self.price_history: list[float] = []  # spot price (1 per poll)

        # Accumulated liquidations from WebSocket thread
        self.liq_events: list[dict] = []      # appended by liquidation thread
        self._liq_lock_token = 0              # simple flip-lock indicator

    # ── Alert cooldowns ───────────────────────────────────────────────

    def can_alert_sell_puts(self) -> bool:
        if self._last_sell_puts is None:
            return True
        return datetime.utcnow() - self._last_sell_puts >= timedelta(minutes=Config.COOLDOWN_MINUTES)

    def can_alert_sell_calls(self) -> bool:
        if self._last_sell_calls is None:
            return True
        return datetime.utcnow() - self._last_sell_calls >= timedelta(minutes=Config.COOLDOWN_MINUTES)

    def mark_sell_puts(self) -> None:
        self._last_sell_puts = datetime.utcnow()

    def mark_sell_calls(self) -> None:
        self._last_sell_calls = datetime.utcnow()

    # ── Helpers ───────────────────────────────────────────────────────

    def push_skew(self, value: float) -> None:
        self.skew_history.append(value)
        if len(self.skew_history) > Config.SKEW_HIGH_WINDOW:
            self.skew_history.pop(0)

    def skew_recent_high(self) -> Optional[float]:
        return max(self.skew_history) if len(self.skew_history) >= 3 else None

    def push_dvol(self, value: float) -> Optional[float]:
        prev = self.dvol_history[-1] if self.dvol_history else None
        self.dvol_history.append(value)
        if len(self.dvol_history) > 120:
            self.dvol_history.pop(0)
        return prev

    def push_price(self, price: float) -> None:
        self.price_history.append(price)
        if len(self.price_history) > 120:
            self.price_history.pop(0)

    def price_change_pct(self, lookback: int = 10) -> float:
        if len(self.price_history) < lookback + 1:
            return 0.0
        old = self.price_history[-(lookback + 1)]
        new = self.price_history[-1]
        return (new - old) / old * 100.0

    def get_recent_liqs(self, minutes: int) -> list[dict]:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        return [e for e in self.liq_events if e["ts"] >= cutoff]

    def prune_liqs(self, keep_minutes: int = 60) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=keep_minutes)
        self.liq_events = [e for e in self.liq_events if e["ts"] >= cutoff]
