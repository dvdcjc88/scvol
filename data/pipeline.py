from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select, text

from data.models import Base, DataIngestionLog
from config import settings


_engine = create_async_engine(settings.database_url, echo=False)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def init_db_sync() -> None:
    asyncio.run(init_db())


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with _async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def is_data_fresh(source: str, max_age_hours: int | None = None) -> bool:
    max_age = max_age_hours or settings.data_refresh_interval_hours
    cutoff = datetime.utcnow() - timedelta(hours=max_age)
    async with get_session() as session:
        result = await session.execute(
            select(DataIngestionLog)
            .where(DataIngestionLog.source == source)
            .where(DataIngestionLog.status == "success")
            .where(DataIngestionLog.finished_at >= cutoff)
            .order_by(DataIngestionLog.finished_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row is not None


async def log_ingestion_start(source: str, is_mock: bool = False) -> DataIngestionLog:
    async with get_session() as session:
        log = DataIngestionLog(source=source, status="running", is_mock=is_mock)
        session.add(log)
        await session.flush()
        await session.refresh(log)
        return log


async def log_ingestion_finish(log_id: str, rows: int, error: str | None = None) -> None:
    async with get_session() as session:
        result = await session.execute(
            select(DataIngestionLog).where(DataIngestionLog.id == log_id)
        )
        log = result.scalar_one_or_none()
        if log:
            log.status = "failed" if error else "success"
            log.rows_processed = rows
            log.finished_at = datetime.utcnow()
            log.error_msg = error


async def get_db_stats() -> dict:
    async with get_session() as session:
        budget_count = (await session.execute(text("SELECT COUNT(*) FROM budget_items"))).scalar() or 0
        congress_count = (await session.execute(text("SELECT COUNT(*) FROM congressmen"))).scalar() or 0
        anomaly_count = (await session.execute(text("SELECT COUNT(*) FROM anomalies"))).scalar() or 0

        # Document stats (table may not exist yet)
        try:
            doc_count = (await session.execute(text("SELECT COUNT(*) FROM documents"))).scalar() or 0
            doc_ok = (await session.execute(text("SELECT COUNT(*) FROM documents WHERE status='ok'"))).scalar() or 0
            doc_rows = (await session.execute(text("SELECT COUNT(*) FROM document_rows"))).scalar() or 0
        except Exception:
            doc_count = doc_ok = doc_rows = 0

        latest_log = (await session.execute(
            text("SELECT source, finished_at, is_mock FROM ingestion_logs WHERE status='success' ORDER BY finished_at DESC LIMIT 1")
        )).fetchone()

    return {
        "budget_items": budget_count,
        "congressmen": congress_count,
        "anomalies": anomaly_count,
        "documents": doc_count,
        "documents_ok": doc_ok,
        "document_rows": doc_rows,
        "last_refresh": latest_log[1] if latest_log else None,
        "data_source": "mock" if (latest_log and latest_log[2]) else "live",
    }


async def search_documents(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across document raw_text."""
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT id, source, doc_type, table_code, title, fiscal_year,
                       page_count, status, scraped_at,
                       SUBSTR(raw_text, 1, 300) as excerpt
                FROM documents
                WHERE status = 'ok'
                  AND (raw_text LIKE :q OR title LIKE :q OR table_code LIKE :q2)
                LIMIT :limit
            """),
            {"q": f"%{query}%", "q2": f"%{query.upper()}%", "limit": limit}
        )
        rows = result.fetchall()

    return [dict(r._mapping) for r in rows]


async def list_documents(source: str | None = None, limit: int = 30) -> list[dict]:
    """List all ingested documents."""
    where = "WHERE status='ok'" + (f" AND source='{source}'" if source else "")
    async with get_session() as session:
        result = await session.execute(
            text(f"""
                SELECT source, doc_type, table_code, title, fiscal_year,
                       page_count, file_size_bytes, scraped_at
                FROM documents {where}
                ORDER BY source, table_code
                LIMIT {limit}
            """)
        )
        rows = result.fetchall()
    return [dict(r._mapping) for r in rows]
