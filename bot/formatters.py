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

    docs = stats.get("documents", 0)
    docs_ok = stats.get("documents_ok", 0)
    doc_rows = stats.get("document_rows", 0)

    return (
        "<b>📊 System Status</b>\n\n"
        f"Data source: {source_icon}\n"
        f"Budget records: {budget:,}\n"
        f"Congressmen: {congress:,}\n"
        f"Detected anomalies: {anomalies:,}\n"
        f"Documents: {docs_ok:,} / {docs:,} scraped ({doc_rows:,} rows)\n"
        f"Last refresh: {refresh_str}\n\n"
        "<i>/refresh — reload budget data\n"
        "/scrape — download DBM/BetterGov/PhilGEPS documents\n"
        "/docs — browse documents\n"
        "/search [query] — search documents</i>"
    )


def format_help() -> str:
    return (
        "<b>🔍 PH Spending Anomaly Bot</b>\n\n"
        "I analyze Philippine government budget data to flag suspicious spending patterns "
        "and link them to congressmen.\n\n"
        "<b>Analysis Commands:</b>\n"
        "/anomalies — Top 10 anomalies nationwide\n"
        "/anomalies [region] — Filter by region (e.g. <code>Region III</code>)\n"
        "/congressman [name] — Risk profile for a rep\n"
        "/agency [name] — Anomalies for an agency (e.g. <code>DPWH</code>)\n"
        "/regions — List all regions\n"
        "/top_risk — Top 5 highest-risk congressmen\n\n"
        "<b>Document Commands:</b>\n"
        "/docs — List all scraped government documents\n"
        "/docs [source] — Filter: <code>dbm_besf</code> <code>dbm_gaa</code> <code>bettergov</code> <code>philgeps</code>\n"
        "/search [query] — Full-text search across all documents\n"
        "/scrape — Trigger full document scraping (admin only)\n\n"
        "<b>System:</b>\n"
        "/status — Database and refresh status\n"
        "/refresh — Force budget data re-download\n"
        "/help — This message\n\n"
        "<i>⚠️ Flags indicate statistical patterns in public data, "
        "not proven wrongdoing. Always verify with primary sources.</i>"
    )


def format_document_list(docs: list[dict]) -> list[str]:
    if not docs:
        return ["<b>📄 No documents ingested yet.</b>\n\nUse /scrape to download DBM, BetterGov, and PhilGEPS documents."]

    source_icons = {
        "dbm_besf": "📊", "dbm_gaa": "📋", "bettergov": "🌐",
        "philgeps": "🏛️",
    }
    source_labels = {
        "dbm_besf": "DBM — BESF Tables",
        "dbm_gaa": "DBM — General Appropriations Act",
        "bettergov": "BetterGov.PH Open Data",
        "philgeps": "PhilGEPS Procurement Awards",
    }

    lines = ["<b>📄 Ingested Government Documents</b>\n"]
    current_source = None
    for doc in docs:
        source = doc.get("source", "")
        if source != current_source:
            current_source = source
            icon = source_icons.get(source, "📄")
            label = source_labels.get(source, source.upper())
            lines.append(f"\n{icon} <b>{label}</b>")

        code = str(doc.get("table_code") or "")
        title = str(doc.get("title") or code)
        fy = doc.get("fiscal_year") or ""
        pages = doc.get("page_count") or ""
        size = doc.get("file_size_bytes") or 0
        size_str = f"{size/1024:.0f}KB" if size else ""
        scraped = doc.get("scraped_at")
        scraped_str = str(scraped or "")[:10]

        detail_parts = [x for x in [f"FY{fy}" if fy else "", f"{pages}pp" if pages else "", size_str, scraped_str] if x]
        detail = " · ".join(detail_parts)

        lines.append(f"  <code>{code:6s}</code> {html.escape(title[:70])}"
                     + (f" <i>({detail})</i>" if detail else ""))

    return chunk_message("\n".join(lines))


def format_search_results(results: list[dict], query: str) -> list[str]:
    if not results:
        return [f"<b>🔍 No results for:</b> <code>{html.escape(query)}</code>\n\n"
                "<i>Try /docs to see what's been ingested, or /scrape to download new documents.</i>"]

    lines = [f"<b>🔍 Search: </b><code>{html.escape(query)}</code> — {len(results)} result(s)\n"]
    for r in results:
        code = str(r.get("table_code") or "")
        title = str(r.get("title") or code)
        fy = r.get("fiscal_year") or ""
        excerpt = str(r.get("excerpt") or "")[:200].replace("\n", " ")
        lines.append(
            f"\n<b>{html.escape(title[:60])}</b>"
            + (f" <i>(FY{fy})</i>" if fy else "")
            + f"\n<code>{code}</code> · {html.escape(excerpt)}"
        )

    return chunk_message("\n".join(lines))


def format_scrape_result(results: dict) -> str:
    lines = ["<b>🔄 Document Scraping Complete</b>\n"]

    dbm = results.get("dbm", {})
    if isinstance(dbm, dict) and not dbm.get("error"):
        besf = dbm.get("besf", {})
        gaa = dbm.get("gaa", {})
        lines.append(f"📊 <b>DBM BESF</b>: {besf.get('downloaded', 0)} downloaded, "
                     f"{besf.get('parsed', 0)} parsed, {besf.get('failed', 0)} not found")
        lines.append(f"📋 <b>DBM GAA</b>: {gaa.get('downloaded', 0)} downloaded, "
                     f"{gaa.get('parsed', 0)} parsed")
    elif isinstance(dbm, dict) and dbm.get("error"):
        lines.append(f"📊 DBM: ❌ {html.escape(str(dbm['error'])[:100])}")

    bg = results.get("bettergov", {})
    if isinstance(bg, dict) and not bg.get("error"):
        bgv = bg.get("bettergov", {})
        pgv = bg.get("philgeps", {})
        lines.append(f"🌐 <b>BetterGov</b>: {bgv.get('downloaded', 0)} downloaded, "
                     f"{bgv.get('failed', 0)} failed")
        lines.append(f"🏛️ <b>PhilGEPS</b>: {pgv.get('downloaded', 0)} downloaded, "
                     f"{pgv.get('failed', 0)} failed")
    elif isinstance(bg, dict) and bg.get("error"):
        lines.append(f"🌐 BetterGov: ❌ {html.escape(str(bg['error'])[:100])}")

    lines.append("\n<i>Use /docs to browse, /search [term] to query.</i>")
    return "\n".join(lines)


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
