from __future__ import annotations

import html
from datetime import datetime
from typing import Any


MAX_MSG_LEN = 4000


def risk_emoji(score: float) -> str:
    if score >= 7:
        return "🔴"
    elif score >= 4:
        return "🟡"
    return "🟢"


def format_php(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"₱{amount / 1_000_000_000:.1f}B"
    elif amount >= 1_000_000:
        return f"₱{amount / 1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"₱{amount / 1_000:.0f}K"
    return f"₱{amount:,.0f}"


def format_anomaly_report(crew_output: Any, region: str | None = None) -> list[str]:
    text = str(crew_output)
    header = (
        "<b>🇵🇭 PH Spending Anomaly Report</b>\n"
        f"{'<i>Region: ' + html.escape(region) + '</i>' if region else '<i>Nationwide</i>'}\n"
        f"<i>Generated: {datetime.now().strftime('%b %d, %Y %H:%M')} PHT</i>\n\n"
    )
    body = html.escape(text)
    full = header + body
    return chunk_message(full)


def format_status(stats: dict) -> str:
    budget = stats.get("budget_items", 0)
    congress = stats.get("congressmen", 0)
    anomalies = stats.get("anomalies", 0)
    last_refresh = stats.get("last_refresh")
    source = stats.get("data_source", "unknown")

    refresh_str = (
        last_refresh.strftime("%b %d %Y %H:%M UTC") if isinstance(last_refresh, datetime)
        else str(last_refresh or "Never")
    )

    source_icon = "📦 Mock" if source == "mock" else "🌐 Live"

    return (
        "<b>📊 System Status</b>\n\n"
        f"Data source: {source_icon}\n"
        f"Budget records: {budget:,}\n"
        f"Congressmen: {congress:,}\n"
        f"Detected anomalies: {anomalies:,}\n"
        f"Last refresh: {refresh_str}\n\n"
        "<i>Use /refresh to force a data update.</i>"
    )


def format_help() -> str:
    return (
        "<b>🔍 PH Spending Anomaly Bot</b>\n\n"
        "I analyze Philippine government budget data to flag suspicious spending patterns "
        "and link them to congressmen.\n\n"
        "<b>Commands:</b>\n"
        "/anomalies — Top 10 anomalies nationwide\n"
        "/anomalies [region] — Filter by region (e.g. <code>Region III</code>)\n"
        "/congressman [name] — Risk profile for a rep\n"
        "/agency [name] — Anomalies for an agency (e.g. <code>DPWH</code>)\n"
        "/regions — List all regions\n"
        "/top_risk — Top 5 highest-risk congressmen\n"
        "/status — Database and refresh status\n"
        "/refresh — Force data re-download\n"
        "/help — This message\n\n"
        "<i>⚠️ Flags indicate statistical patterns in public data, "
        "not proven wrongdoing. Always verify with primary sources.</i>"
    )


def format_regions() -> str:
    regions = [
        "NCR - National Capital Region",
        "Region I - Ilocos",
        "Region II - Cagayan Valley",
        "Region III - Central Luzon",
        "Region IV-A - CALABARZON",
        "Region IV-B - MIMAROPA",
        "Region V - Bicol",
        "Region VI - Western Visayas",
        "Region VII - Central Visayas",
        "Region VIII - Eastern Visayas",
        "Region IX - Zamboanga Peninsula",
        "Region X - Northern Mindanao",
        "Region XI - Davao",
        "Region XII - SOCCSKSARGEN",
        "Region XIII - Caraga",
        "CAR - Cordillera Administrative Region",
        "BARMM - Bangsamoro Autonomous Region",
    ]
    lines = "\n".join(f"• <code>{r}</code>" for r in regions)
    return f"<b>📍 Philippine Regions</b>\n\n{lines}\n\n<i>Use region name with /anomalies [region]</i>"


def format_error(msg: str) -> str:
    return f"❌ <b>Error</b>\n\n{html.escape(msg)}\n\n<i>Try /status to check data availability.</i>"


def chunk_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find a clean break point
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
