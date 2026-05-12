from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agents.crew import build_crew
from bot.formatters import (
    format_anomaly_report,
    format_document_list,
    format_error,
    format_help,
    format_regions,
    format_scrape_result,
    format_search_results,
    format_status,
    chunk_message,
)
from config import settings
from data.ingestion import run_ingestion_pipeline, run_document_scraping
from data.pipeline import get_db_stats, search_documents, list_documents

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3)
_crew_lock = asyncio.Lock()

# Per-user rate limiting: timestamps of last requests
_user_last_request: dict[int, float] = defaultdict(float)
RATE_LIMIT_SECONDS = 30


def _check_rate_limit(user_id: int) -> bool:
    now = time.time()
    last = _user_last_request[user_id]
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _user_last_request[user_id] = now
    return True


def _is_admin(user_id: int) -> bool:
    return not settings.admin_telegram_user_ids or user_id in settings.admin_telegram_user_ids


async def _send_chunks(update: Update, chunks: list[str]) -> None:
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)


async def _run_crew_async(region: str | None, congressman: str | None, agency: str | None) -> str:
    loop = asyncio.get_event_loop()
    crew = build_crew(region=region, congressman=congressman, agency=agency)
    result = await loop.run_in_executor(_executor, crew.kickoff)
    return str(result)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>🇵🇭 Philippine Spending Anomaly Bot</b>\n\n"
        "I use AI agents and machine learning to analyze government budget data "
        "and flag suspicious spending patterns linked to congressional districts.\n\n"
        "<b>What I do:</b>\n"
        "• Download public GAA budget data\n"
        "• Run Isolation Forest + Z-score anomaly detection\n"
        "• Link anomalies to sitting congressmen\n"
        "• Cross-check with news and COA audit reports\n\n"
        "Type /help to see all commands.\n\n"
        "<i>Data source: DBM GAA, BetterGov.PH, Open Congress API\n"
        "⚠️ For informational purposes. Always verify with primary sources.</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(format_help(), parse_mode="HTML")


async def regions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("NCR", callback_data="anomaly:NCR"),
         InlineKeyboardButton("Region I", callback_data="anomaly:Region I")],
        [InlineKeyboardButton("Region II", callback_data="anomaly:Region II"),
         InlineKeyboardButton("Region III", callback_data="anomaly:Region III")],
        [InlineKeyboardButton("Region IV-A", callback_data="anomaly:Region IV-A"),
         InlineKeyboardButton("Region IV-B", callback_data="anomaly:Region IV-B")],
        [InlineKeyboardButton("Region V", callback_data="anomaly:Region V"),
         InlineKeyboardButton("Region VI", callback_data="anomaly:Region VI")],
        [InlineKeyboardButton("Region VII", callback_data="anomaly:Region VII"),
         InlineKeyboardButton("Region VIII", callback_data="anomaly:Region VIII")],
        [InlineKeyboardButton("Region IX", callback_data="anomaly:Region IX"),
         InlineKeyboardButton("Region X", callback_data="anomaly:Region X")],
        [InlineKeyboardButton("Region XI", callback_data="anomaly:Region XI"),
         InlineKeyboardButton("Region XII", callback_data="anomaly:Region XII")],
        [InlineKeyboardButton("Region XIII", callback_data="anomaly:Region XIII"),
         InlineKeyboardButton("CAR", callback_data="anomaly:CAR")],
        [InlineKeyboardButton("BARMM", callback_data="anomaly:BARMM"),
         InlineKeyboardButton("🇵🇭 Nationwide", callback_data="anomaly:all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        format_regions(),
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def region_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data  # "anomaly:Region III" or "anomaly:all"
    region = None if data == "anomaly:all" else data.split(":", 1)[1]
    user_id = query.from_user.id

    if not _check_rate_limit(user_id):
        await query.message.reply_text(
            f"⏳ Please wait {RATE_LIMIT_SECONDS}s between queries."
        )
        return

    msg = await query.message.reply_text(
        f"🔍 Analyzing {'nationwide' if not region else region} budget data... This may take a minute."
    )
    try:
        output = await _run_crew_async(region=region, congressman=None, agency=None)
        chunks = format_anomaly_report(output, region=region)
        await msg.delete()
        for chunk in chunks:
            await query.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.exception("Crew failed")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def anomalies_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _check_rate_limit(user_id):
        await update.message.reply_text(f"⏳ Please wait {RATE_LIMIT_SECONDS}s between queries.")
        return

    region = " ".join(context.args) if context.args else None

    msg = await update.message.reply_text(
        f"🔍 Analyzing {'nationwide' if not region else region} budget data... This may take a minute."
    )
    try:
        output = await _run_crew_async(region=region, congressman=None, agency=None)
        chunks = format_anomaly_report(output, region=region)
        await msg.delete()
        await _send_chunks(update, chunks)
    except Exception as e:
        logger.exception("Crew failed in anomalies_handler")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def congressman_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _check_rate_limit(user_id):
        await update.message.reply_text(f"⏳ Please wait {RATE_LIMIT_SECONDS}s between queries.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /congressman [name]\nExample: /congressman dela Cruz",
            parse_mode="HTML",
        )
        return

    name = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Investigating congressman {name}...")
    try:
        output = await _run_crew_async(region=None, congressman=name, agency=None)
        chunks = format_anomaly_report(output, region=None)
        await msg.delete()
        await _send_chunks(update, chunks)
    except Exception as e:
        logger.exception("Crew failed in congressman_handler")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def agency_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _check_rate_limit(user_id):
        await update.message.reply_text(f"⏳ Please wait {RATE_LIMIT_SECONDS}s between queries.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /agency [name]\nExample: /agency DPWH", parse_mode="HTML"
        )
        return

    agency = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Analyzing {agency} budget anomalies...")
    try:
        output = await _run_crew_async(region=None, congressman=None, agency=agency)
        chunks = format_anomaly_report(output, region=None)
        await msg.delete()
        await _send_chunks(update, chunks)
    except Exception as e:
        logger.exception("Crew failed in agency_handler")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def top_risk_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _check_rate_limit(user_id):
        await update.message.reply_text(f"⏳ Please wait {RATE_LIMIT_SECONDS}s between queries.")
        return

    msg = await update.message.reply_text("🔍 Finding top risk congressmen nationwide...")
    try:
        output = await _run_crew_async(region=None, congressman=None, agency=None)
        chunks = format_anomaly_report(output, region=None)
        await msg.delete()
        await _send_chunks(update, chunks)
    except Exception as e:
        logger.exception("Crew failed in top_risk_handler")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        stats = await get_db_stats()
        await update.message.reply_text(format_status(stats), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(format_error(str(e)), parse_mode="HTML")


async def refresh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        await update.message.reply_text("⛔ Admin-only command.")
        return

    msg = await update.message.reply_text("🔄 Refreshing data from source systems...")
    try:
        results = await run_ingestion_pipeline(force=True)
        budget_r = results.get("budget", {})
        congress_r = results.get("congressmen", {})
        text = (
            "<b>✅ Data refresh complete</b>\n\n"
            f"Budget: {budget_r.get('rows', 0)} rows "
            f"({'mock' if budget_r.get('mock') else 'live'})\n"
            f"Congressmen: {congress_r.get('rows', 0)} records "
            f"({'mock' if congress_r.get('mock') else 'live'})"
        )
        await msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.exception("Refresh failed")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def docs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    source = context.args[0] if context.args else None
    try:
        docs = await list_documents(source=source, limit=60)
        chunks = format_document_list(docs)
        await _send_chunks(update, chunks)
    except Exception as e:
        await update.message.reply_text(format_error(str(e)), parse_mode="HTML")


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /search [query]\nExample: /search DPWH Region III infrastructure",
            parse_mode="HTML",
        )
        return

    query = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 Searching documents for: <code>{query}</code>...", parse_mode="HTML")
    try:
        results = await search_documents(query, limit=8)
        chunks = format_search_results(results, query)
        await msg.delete()
        await _send_chunks(update, chunks)
    except Exception as e:
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def scrape_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        await update.message.reply_text("⛔ Admin-only command.")
        return

    force = "force" in (context.args or [])
    msg = await update.message.reply_text(
        "🔄 Starting document scraping from DBM, BetterGov, and PhilGEPS...\n"
        "<i>This will take several minutes. Downloading 50+ PDFs.</i>",
        parse_mode="HTML",
    )
    try:
        results = await run_document_scraping(force=force)
        text = format_scrape_result(results)
        await msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        logger.exception("Scrape failed")
        await msg.edit_text(format_error(str(e)), parse_mode="HTML")


async def text_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.lower()

    # Simple intent routing via keywords
    region_keywords = {
        "ncr": "NCR", "manila": "NCR",
        "ilocos": "Region I", "region i": "Region I",
        "cagayan": "Region II", "region ii": "Region II",
        "central luzon": "Region III", "pampanga": "Region III",
        "bulacan": "Region III", "region iii": "Region III",
        "calabarzon": "Region IV-A", "cavite": "Region IV-A",
        "bicol": "Region V", "albay": "Region V",
        "western visayas": "Region VI", "iloilo": "Region VI",
        "cebu": "Region VII", "central visayas": "Region VII",
        "leyte": "Region VIII", "samar": "Region VIII",
        "zamboanga": "Region IX",
        "mindanao": "Region X", "bukidnon": "Region X",
        "davao": "Region XI",
        "cotabato": "Region XII",
        "caraga": "Region XIII", "surigao": "Region XIII",
        "barmm": "BARMM", "bangsamoro": "BARMM",
        "car": "CAR", "benguet": "CAR", "cordillera": "CAR",
    }

    detected_region = None
    for keyword, region_name in region_keywords.items():
        if keyword in text:
            detected_region = region_name
            break

    if any(kw in text for kw in ["anomal", "corrupt", "irregul", "suspicious", "spending", "budget"]):
        context.args = [detected_region] if detected_region else []
        await anomalies_handler(update, context)
    elif any(kw in text for kw in ["congressman", "rep ", "representative"]):
        words = update.message.text.split()
        idx = next((i for i, w in enumerate(words) if w.lower() in ("congressman", "rep", "representative")), -1)
        name_parts = words[idx + 1:] if idx >= 0 else words
        context.args = name_parts
        await congressman_handler(update, context)
    elif any(kw in text for kw in ["dpwh", "deped", "doh", "agency", "department"]):
        context.args = update.message.text.split()
        await agency_handler(update, context)
    else:
        await update.message.reply_text(
            "I can analyze Philippine government spending anomalies. Try:\n"
            "• /anomalies Region III\n"
            "• /congressman dela Cruz\n"
            "• /agency DPWH\n"
            "• /help for all commands",
            parse_mode="HTML",
        )


def build_application() -> Application:
    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("regions", regions_handler))
    app.add_handler(CommandHandler("anomalies", anomalies_handler))
    app.add_handler(CommandHandler("congressman", congressman_handler))
    app.add_handler(CommandHandler("agency", agency_handler))
    app.add_handler(CommandHandler("top_risk", top_risk_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("refresh", refresh_handler))
    app.add_handler(CommandHandler("docs", docs_handler))
    app.add_handler(CommandHandler("search", search_handler))
    app.add_handler(CommandHandler("scrape", scrape_handler))
    app.add_handler(CallbackQueryHandler(region_callback_handler, pattern=r"^anomaly:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_query_handler))

    return app
