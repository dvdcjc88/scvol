#!/usr/bin/env python3
"""
ETH/USDT Reversal Alert System
Monitors Ethereum for high-probability daily reversal setups using multi-indicator
confluence across 1h and 4h timeframes and fires Telegram alerts.
"""

import time
import json
import math
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = "8765446376:AAE9CpY4nX6zhH90GAKZAOhCsjELZs38fn4"
TELEGRAM_CHAT_ID   = None          # auto-discovered on first run

SYMBOL             = "ETH-USDT"
CHECK_INTERVAL_SEC = 300           # scan every 5 minutes
ALERT_COOLDOWN_SEC = 3600          # 1-hour cooldown between same-direction alerts
STATE_FILE         = Path(__file__).parent / ".eth_alert_state.json"

# Indicator thresholds
RSI_OVERSOLD       = 32
RSI_OVERBOUGHT     = 68
STOCH_OVERSOLD     = 22
STOCH_OVERBOUGHT   = 78
MIN_SCORE          = 4             # minimum confluence score to fire alert (max ~14)
VOLUME_MULT        = 1.5           # volume must be Nx above 20-bar average

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_bull": 0, "last_bear": 0, "chat_id": None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def tg_get(method: str, params: dict = {}) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    r = requests.get(url, params=params, timeout=10)
    return r.json()


def discover_chat_id() -> str | None:
    """Return chat ID of the most recent message sent to the bot."""
    result = tg_get("getUpdates", {"limit": 10, "timeout": 0})
    updates = result.get("result", [])
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            return str(chat["id"])
    return None


def send_telegram(chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        data = r.json()
        if not data.get("ok"):
            log(f"Telegram error: {data.get('description')}")
            return False
        return True
    except Exception as e:
        log(f"Telegram send failed: {e}")
        return False


# ─── MARKET DATA ─────────────────────────────────────────────────────────────

KUCOIN_KLINES = "https://api.kucoin.com/api/v1/market/candles"

# Map generic interval names to KuCoin type strings
KUCOIN_INTERVALS = {
    "1h":  "1hour",
    "4h":  "4hour",
    "1d":  "1day",
    "15m": "15min",
    "1h":  "1hour",
}

def fetch_ohlcv(interval: str, limit: int = 200) -> pd.DataFrame:
    """Fetch OHLCV from KuCoin public API.
    KuCoin candle format: [time, open, close, high, low, volume, turnover]
    Data arrives newest-first, so we reverse it.
    """
    kc_type = KUCOIN_INTERVALS.get(interval, interval)
    # Compute startAt so we get ~limit bars ending now
    seconds_per_bar = {
        "1hour": 3600, "4hour": 14400, "1day": 86400,
        "15min": 900,  "30min": 1800,  "1min": 60,
    }
    bar_secs  = seconds_per_bar.get(kc_type, 3600)
    now_ts    = int(time.time())
    start_ts  = now_ts - (limit + 5) * bar_secs

    r = requests.get(
        KUCOIN_KLINES,
        params={
            "type":    kc_type,
            "symbol":  SYMBOL,
            "startAt": start_ts,
            "endAt":   now_ts,
        },
        timeout=15,
    )
    r.raise_for_status()
    raw = r.json().get("data", [])
    if not raw:
        raise ValueError(f"No data returned for {interval}")

    # KuCoin returns newest-first; reverse to chronological order
    raw = list(reversed(raw))

    df = pd.DataFrame(raw, columns=["ts", "open", "close", "high", "low", "volume", "turnover"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="s", utc=True)
    df.set_index("ts", inplace=True)
    return df[["open", "high", "low", "close", "volume"]].tail(limit)


# ─── TECHNICAL INDICATORS ────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(series: pd.Series, period: int = 20, std: float = 2.0):
    mid = series.rolling(period).mean()
    sigma = series.rolling(period).std()
    return mid + std * sigma, mid, mid - std * sigma


def stochastic(df: pd.DataFrame, k_period=14, d_period=3):
    low_min  = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


# ─── CANDLESTICK PATTERNS ─────────────────────────────────────────────────────

def detect_candle_patterns(df: pd.DataFrame) -> dict:
    """
    Returns dict with pattern name → signal direction (+1 bull, -1 bear, 0 none)
    evaluated on the most recent completed candle.
    """
    o, h, l, c = df["open"].iloc[-1], df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    o2, h2, l2, c2 = df["open"].iloc[-2], df["high"].iloc[-2], df["low"].iloc[-2], df["close"].iloc[-2]
    body   = abs(c - o)
    rng    = h - l
    body2  = abs(c2 - o2)
    rng2   = max(h2 - l2, 1e-9)
    avg_body = (df["close"] - df["open"]).abs().rolling(10).mean().iloc[-1]

    patterns = {}

    # Doji — indecision at extreme → potential reversal
    if rng > 0 and body / rng < 0.1:
        patterns["Doji"] = 0  # neutral until confirmed by context

    # Hammer / Inverted Hammer (bullish after downtrend)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if body > 0 and lower_wick >= 2 * body and upper_wick <= 0.3 * body:
        patterns["Hammer"] = +1
    if body > 0 and upper_wick >= 2 * body and lower_wick <= 0.3 * body:
        patterns["InvertedHammer"] = +1

    # Shooting Star (bearish after uptrend)
    if body > 0 and upper_wick >= 2 * body and lower_wick <= 0.3 * body and c < o:
        patterns["ShootingStar"] = -1

    # Bullish Engulfing
    if c2 < o2 and c > o and o <= c2 and c >= o2:
        patterns["BullEngulfing"] = +1

    # Bearish Engulfing
    if c2 > o2 and c < o and o >= c2 and c <= o2:
        patterns["BearEngulfing"] = -1

    # Bullish Harami
    if c2 < o2 and c > o and o > c2 and c < o2:
        patterns["BullHarami"] = +1

    # Bearish Harami
    if c2 > o2 and c < o and o < c2 and c > o2:
        patterns["BearHarami"] = -1

    # Morning Star / Evening Star (3-candle)
    if len(df) >= 3:
        o3, h3, l3, c3 = df["open"].iloc[-3], df["high"].iloc[-3], df["low"].iloc[-3], df["close"].iloc[-3]
        body3 = abs(c3 - o3)
        if (c3 < o3                          # first candle bearish
                and body2 < 0.3 * body3      # middle doji/small
                and c > o                    # last bullish
                and c > (o3 + c3) / 2):      # closes above midpoint
            patterns["MorningStar"] = +1
        if (c3 > o3
                and body2 < 0.3 * body3
                and c < o
                and c < (o3 + c3) / 2):
            patterns["EveningStar"] = -1

    return patterns


# ─── REVERSAL SCORER ─────────────────────────────────────────────────────────

class ReversalScore:
    def __init__(self):
        self.signals: list[tuple[str, int, str]] = []  # (name, score, detail)

    def add(self, name: str, score: int, detail: str = ""):
        self.signals.append((name, score, detail))

    @property
    def total(self) -> int:
        return sum(s for _, s, _ in self.signals)

    @property
    def direction(self) -> str:
        t = self.total
        if t >= MIN_SCORE:
            return "BULL"
        if t <= -MIN_SCORE:
            return "BEAR"
        return "NEUTRAL"

    def summary(self) -> str:
        lines = []
        for name, score, detail in self.signals:
            arrow = "▲" if score > 0 else ("▼" if score < 0 else "◆")
            lines.append(f"  {arrow} {name}: {'+' if score > 0 else ''}{score}  {detail}")
        return "\n".join(lines)


def analyze_timeframe(df: pd.DataFrame, tf_label: str) -> ReversalScore:
    score = ReversalScore()
    close = df["close"]
    prev_close = close.iloc[-2]
    curr_close = close.iloc[-1]

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi_s = rsi(close)
    rsi_val = rsi_s.iloc[-1]
    rsi_prev = rsi_s.iloc[-2]
    if rsi_val < RSI_OVERSOLD:
        pts = -2 if rsi_val < 20 else -1   # score is negative = oversold = bullish context
        # Flip: oversold → bullish reversal signal → +2/+1
        pts = abs(pts)
        score.add(f"RSI({tf_label})", pts, f"RSI={rsi_val:.1f} oversold")
    elif rsi_val > RSI_OVERBOUGHT:
        pts = 2 if rsi_val > 80 else 1
        score.add(f"RSI({tf_label})", -pts, f"RSI={rsi_val:.1f} overbought")

    # RSI divergence (price new low/high but RSI doesn't confirm)
    recent_low_price  = close.iloc[-10:].min()
    recent_high_price = close.iloc[-10:].max()
    recent_low_rsi    = rsi_s.iloc[-10:].min()
    recent_high_rsi   = rsi_s.iloc[-10:].max()

    if curr_close == recent_low_price and rsi_val > recent_low_rsi + 3:
        score.add(f"RSI-BullDiv({tf_label})", +2, "Bullish RSI divergence")
    if curr_close == recent_high_price and rsi_val < recent_high_rsi - 3:
        score.add(f"RSI-BearDiv({tf_label})", -2, "Bearish RSI divergence")

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_l, sig_l, hist = macd(close)
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        score.add(f"MACD({tf_label})", +2, "Bullish MACD crossover")
    elif hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
        score.add(f"MACD({tf_label})", -2, "Bearish MACD crossover")
    elif hist.iloc[-1] > hist.iloc[-2] and macd_l.iloc[-1] < 0:
        score.add(f"MACD-Mom({tf_label})", +1, "MACD histogram rising below zero")
    elif hist.iloc[-1] < hist.iloc[-2] and macd_l.iloc[-1] > 0:
        score.add(f"MACD-Mom({tf_label})", -1, "MACD histogram falling above zero")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    price = curr_close
    if price <= bb_lower.iloc[-1]:
        score.add(f"BB({tf_label})", +2, f"Price at/below lower band ${bb_lower.iloc[-1]:.2f}")
    elif price >= bb_upper.iloc[-1]:
        score.add(f"BB({tf_label})", -2, f"Price at/above upper band ${bb_upper.iloc[-1]:.2f}")

    # BB squeeze → potential breakout
    band_width = (bb_upper - bb_lower) / bb_mid
    if band_width.iloc[-1] < band_width.rolling(20).mean().iloc[-1] * 0.6:
        score.add(f"BB-Squeeze({tf_label})", 0, "Bollinger squeeze (breakout incoming)")

    # ── Stochastic ────────────────────────────────────────────────────────────
    stoch_k, stoch_d = stochastic(df)
    k_now, d_now = stoch_k.iloc[-1], stoch_d.iloc[-1]
    k_prev, d_prev = stoch_k.iloc[-2], stoch_d.iloc[-2]
    if k_now < STOCH_OVERSOLD and d_now < STOCH_OVERSOLD:
        score.add(f"Stoch({tf_label})", +1, f"Stoch oversold K={k_now:.1f} D={d_now:.1f}")
        if k_prev < d_prev and k_now > d_now:
            score.add(f"Stoch-Cross({tf_label})", +1, "Stoch bullish K/D crossover")
    if k_now > STOCH_OVERBOUGHT and d_now > STOCH_OVERBOUGHT:
        score.add(f"Stoch({tf_label})", -1, f"Stoch overbought K={k_now:.1f} D={d_now:.1f}")
        if k_prev > d_prev and k_now < d_now:
            score.add(f"Stoch-Cross({tf_label})", -1, "Stoch bearish K/D crossover")

    # ── EMA Crossovers ────────────────────────────────────────────────────────
    ema9  = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)
    if ema9.iloc[-1] > ema21.iloc[-1] and ema9.iloc[-2] <= ema21.iloc[-2]:
        score.add(f"EMA-Cross({tf_label})", +2, "EMA 9 crossed above 21 (golden)")
    elif ema9.iloc[-1] < ema21.iloc[-1] and ema9.iloc[-2] >= ema21.iloc[-2]:
        score.add(f"EMA-Cross({tf_label})", -2, "EMA 9 crossed below 21 (death)")

    # Price vs EMA50 — context
    if curr_close > ema50.iloc[-1]:
        trend_bias = +0.5
    else:
        trend_bias = -0.5

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_avg = df["volume"].rolling(20).mean().iloc[-1]
    vol_now = df["volume"].iloc[-1]
    vol_surge = vol_now >= VOLUME_MULT * vol_avg
    if vol_surge:
        # Directional volume: is the surge on a bullish or bearish candle?
        if curr_close > df["open"].iloc[-1]:
            score.add(f"Vol({tf_label})", +1, f"Vol surge {vol_now/vol_avg:.1f}x on green candle")
        else:
            score.add(f"Vol({tf_label})", -1, f"Vol surge {vol_now/vol_avg:.1f}x on red candle")

    # ── Candlestick Patterns ──────────────────────────────────────────────────
    patterns = detect_candle_patterns(df)
    for pname, psig in patterns.items():
        if psig != 0:
            score.add(f"Candle-{pname}({tf_label})", psig, "")

    # ── ATR / Volatility context ──────────────────────────────────────────────
    atr_val = atr(df).iloc[-1]
    daily_move = abs(curr_close - df["open"].iloc[-1])

    return score


def analyze_support_resistance(df_daily: pd.DataFrame) -> tuple[float, float]:
    """Simple S/R via recent 20-day swing highs/lows."""
    recent = df_daily.tail(20)
    support    = recent["low"].rolling(3, center=True).min().dropna().max()
    resistance = recent["high"].rolling(3, center=True).max().dropna().min()
    return support, resistance


# ─── COMPOSITE SIGNAL ────────────────────────────────────────────────────────

def build_alert_message(
    direction: str,
    score_1h: ReversalScore,
    score_4h: ReversalScore,
    price: float,
    support: float,
    resistance: float,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = score_1h.total + score_4h.total

    if direction == "BULL":
        emoji  = "🟢"
        action = "POTENTIAL BUY / LONG REVERSAL"
        context = (
            f"⚡ Price is near support <b>${support:.2f}</b>\n"
            f"🎯 Target zone: <b>${resistance:.2f}</b>"
        )
    else:
        emoji  = "🔴"
        action = "POTENTIAL SELL / SHORT REVERSAL"
        context = (
            f"⚡ Price is near resistance <b>${resistance:.2f}</b>\n"
            f"🎯 Target zone: <b>${support:.2f}</b>"
        )

    msg = f"""{emoji} <b>ETH/USDT — {action}</b>

📅 {now}
💲 Price: <b>${price:,.2f}</b>
📊 Confluence Score: <b>{total:+d}</b> (threshold ±{MIN_SCORE})

<b>── 1H Signals ({score_1h.total:+d}) ──</b>
{score_1h.summary()}

<b>── 4H Signals ({score_4h.total:+d}) ──</b>
{score_4h.summary()}

{context}

⚠️ <i>Not financial advice. Always use stop-loss.</i>"""
    return msg


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def get_chat_id(state: dict) -> str | None:
    if state.get("chat_id"):
        return state["chat_id"]
    cid = discover_chat_id()
    if cid:
        state["chat_id"] = cid
        save_state(state)
        log(f"Discovered Telegram chat_id: {cid}")
    return cid


def run_once(state: dict) -> dict:
    """Run one analysis cycle. Returns updated state."""
    now_ts = time.time()

    try:
        df_1h = fetch_ohlcv("1h", 200)
        df_4h = fetch_ohlcv("4h", 200)
        df_1d = fetch_ohlcv("1d",  60)
    except Exception as e:
        log(f"Data fetch error: {e}")
        return state

    price = df_1h["close"].iloc[-1]

    score_1h = analyze_timeframe(df_1h, "1H")
    score_4h = analyze_timeframe(df_4h, "4H")

    combined = score_1h.total + score_4h.total
    support, resistance = analyze_support_resistance(df_1d)

    # Direction check — both timeframes must agree
    dir_1h = score_1h.direction
    dir_4h = score_4h.direction
    # Require same direction on both, or one neutral + one strong
    if dir_1h == dir_4h and dir_1h != "NEUTRAL":
        direction = dir_1h
    elif dir_1h != "NEUTRAL" and dir_4h == "NEUTRAL" and abs(score_1h.total) >= MIN_SCORE + 2:
        direction = dir_1h
    elif dir_4h != "NEUTRAL" and dir_1h == "NEUTRAL" and abs(score_4h.total) >= MIN_SCORE + 2:
        direction = dir_4h
    else:
        direction = "NEUTRAL"

    log(f"ETH=${price:,.2f} | 1H={score_1h.total:+d} ({dir_1h}) | 4H={score_4h.total:+d} ({dir_4h}) | Combined={combined:+d} | Dir={direction}")

    if direction == "NEUTRAL":
        return state

    # Cooldown check
    last_key = "last_bull" if direction == "BULL" else "last_bear"
    if now_ts - state.get(last_key, 0) < ALERT_COOLDOWN_SEC:
        remaining = int(ALERT_COOLDOWN_SEC - (now_ts - state.get(last_key, 0)))
        log(f"Cooldown active for {direction}: {remaining}s remaining")
        return state

    # Fire alert
    chat_id = get_chat_id(state)
    if not chat_id:
        log("No Telegram chat_id available — send any message to your bot first")
        return state

    msg = build_alert_message(direction, score_1h, score_4h, price, support, resistance)
    success = send_telegram(chat_id, msg)

    if success:
        log(f"Alert sent: {direction} | score={combined:+d} | price=${price:,.2f}")
        state[last_key] = now_ts
        save_state(state)
    else:
        log("Alert send failed")

    return state


def main():
    log("ETH Reversal Alert System starting…")
    log(f"Symbol={SYMBOL} | Interval={CHECK_INTERVAL_SEC}s | MinScore=±{MIN_SCORE}")

    state = load_state()

    # Try to resolve chat ID immediately
    cid = get_chat_id(state)
    if not cid:
        log("⚠  Could not auto-discover chat_id.")
        log("   Send any message to your Telegram bot then restart, OR set TELEGRAM_CHAT_ID at top of script.")
    else:
        log(f"Telegram target: {cid}")
        send_telegram(cid, (
            "🤖 <b>ETH Reversal Alert Bot online</b>\n"
            f"Monitoring <b>ETH/USDT</b> every {CHECK_INTERVAL_SEC//60} min.\n"
            f"Alert fires when confluence score ≥ ±{MIN_SCORE} on 1H + 4H."
        ))

    while True:
        try:
            state = run_once(state)
        except KeyboardInterrupt:
            log("Stopped by user.")
            break
        except Exception as e:
            log(f"Unexpected error: {e}")

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
