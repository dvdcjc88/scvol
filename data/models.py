from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class BudgetItem(Base):
    __tablename__ = "budget_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    year: Mapped[int] = mapped_column(Integer, index=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    province: Mapped[Optional[str]] = mapped_column(String(64))
    district_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    agency: Mapped[str] = mapped_column(String(128))
    department: Mapped[Optional[str]] = mapped_column(String(128))
    program: Mapped[str] = mapped_column(String(256))
    allocation_php: Mapped[float] = mapped_column(Float, default=0.0)
    obligation_php: Mapped[float] = mapped_column(Float, default=0.0)
    disbursement_php: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="mock")  # "live" | "mock"
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    anomalies: Mapped[List["Anomaly"]] = relationship(back_populates="budget_item")


class Congressman(Base):
    __tablename__ = "congressmen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    open_congress_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    party: Mapped[Optional[str]] = mapped_column(String(64))
    district_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    province: Mapped[Optional[str]] = mapped_column(String(64))
    region: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    district_label: Mapped[Optional[str]] = mapped_column(String(128))
    profile_url: Mapped[Optional[str]] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    anomaly_links: Mapped[List["AnomalyCongressman"]] = relationship(back_populates="congressman")


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    budget_item_id: Mapped[str] = mapped_column(ForeignKey("budget_items.id"), index=True)
    method: Mapped[str] = mapped_column(String(32))  # "isolation_forest" | "zscore" | "combined"
    zscore_value: Mapped[Optional[float]] = mapped_column(Float)
    if_score: Mapped[Optional[float]] = mapped_column(Float)
    disbursement_rate: Mapped[Optional[float]] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)  # 1–10
    anomaly_reason: Mapped[Optional[str]] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    budget_item: Mapped["BudgetItem"] = relationship(back_populates="anomalies")
    congressman_links: Mapped[List["AnomalyCongressman"]] = relationship(back_populates="anomaly")


class AnomalyCongressman(Base):
    __tablename__ = "anomaly_congressmen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    anomaly_id: Mapped[str] = mapped_column(ForeignKey("anomalies.id"), index=True)
    congressman_id: Mapped[str] = mapped_column(ForeignKey("congressmen.id"), index=True)

    anomaly: Mapped["Anomaly"] = relationship(back_populates="congressman_links")
    congressman: Mapped["Congressman"] = relationship(back_populates="anomaly_links")


class Document(Base):
    """Stores metadata and extracted content from scraped government documents."""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32), index=True)   # "dbm_besf" | "dbm_gaa" | "bettergov" | "philgeps"
    doc_type: Mapped[str] = mapped_column(String(64))              # "besf_table" | "gaa_volume" | "procurement_award"
    table_code: Mapped[Optional[str]] = mapped_column(String(16))  # "A1", "B5a", etc.
    title: Mapped[Optional[str]] = mapped_column(String(256))
    url: Mapped[Optional[str]] = mapped_column(String(512))
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)          # full extracted text
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # "ok" | "failed" | "pending"
    error_msg: Mapped[Optional[str]] = mapped_column(Text)
    scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    rows: Mapped[List["DocumentRow"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentRow(Base):
    """Stores structured rows extracted from document tables (BESF, GAA, PhilGEPS)."""
    __tablename__ = "document_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    row_data: Mapped[str] = mapped_column(Text)   # JSON: {"col1": "val1", ...}
    region: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    agency: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    amount_php: Mapped[Optional[float]] = mapped_column(Float)
    fiscal_year: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    document: Mapped["Document"] = relationship(back_populates="rows")


class DataIngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # "success" | "failed" | "skipped"
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
