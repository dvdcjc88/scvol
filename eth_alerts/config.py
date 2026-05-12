import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Telegram ──────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Polling ───────────────────────────────────────────────────────
    POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "60"))
    COOLDOWN_MINUTES: int = int(os.getenv("COOLDOWN_MINUTES", "30"))
    MIN_SIGNALS: int = int(os.getenv("MIN_SIGNALS_FOR_ALERT", "3"))  # out of 7

    # ── 1. DVOL / IV Term Structure ───────────────────────────────────
    DVOL_SPIKE_PCT: float = float(os.getenv("DVOL_SPIKE_PCT", "10.0"))
    DVOL_BACKWARDATION_PTS: float = float(os.getenv("DVOL_BACKWARDATION_PTS", "2.0"))

    # ── 2. VRP ────────────────────────────────────────────────────────
    # Fire when RV/IV ratio >= threshold (RV catching up to IV)
    VRP_RATIO_THRESHOLD: float = float(os.getenv("VRP_RATIO_THRESHOLD", "80.0"))

    # ── 3. Perp Funding + OI ─────────────────────────────────────────
    FUNDING_NEGATIVE: float = float(os.getenv("FUNDING_NEGATIVE", "-0.0001"))  # ≤ −0.01%
    FUNDING_POSITIVE: float = float(os.getenv("FUNDING_POSITIVE", "0.0002"))   # ≥ +0.02%
    OI_RISING_PCT: float = float(os.getenv("OI_RISING_PCT", "1.5"))

    # ── 4. CVD / Spot-Perp Basis ──────────────────────────────────────
    CVD_NEGATIVE_USD: float = float(os.getenv("CVD_NEGATIVE_USD", "-300000"))
    CVD_POSITIVE_USD: float = float(os.getenv("CVD_POSITIVE_USD", "300000"))
    BASIS_DISCOUNT_USD: float = float(os.getenv("BASIS_DISCOUNT_USD", "5.0"))
    BASIS_PREMIUM_USD: float = float(os.getenv("BASIS_PREMIUM_USD", "5.0"))

    # ── 5. Liquidations ───────────────────────────────────────────────
    LIQ_WINDOW_MIN: int = int(os.getenv("LIQ_WINDOW_MIN", "15"))
    LIQ_THRESHOLD_USD: float = float(os.getenv("LIQ_THRESHOLD_USD", "1000000"))

    # ── 6. Skew ───────────────────────────────────────────────────────
    # Put skew = put_25d_iv - call_25d_iv. Compression = drop from recent high.
    SKEW_COMPRESSION_PTS: float = float(os.getenv("SKEW_COMPRESSION_PTS", "4.0"))
    SKEW_HIGH_WINDOW: int = int(os.getenv("SKEW_HIGH_WINDOW", "20"))  # polls to look back

    # ── 7. GEX (bonus) ────────────────────────────────────────────────
    GEX_PROXIMITY_PCT: float = float(os.getenv("GEX_PROXIMITY_PCT", "1.5"))
