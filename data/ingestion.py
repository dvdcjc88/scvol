from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select, delete

from data.models import BudgetItem, Congressman, DataIngestionLog
from data.pipeline import get_session, is_data_fresh, init_db
from config import settings

MOCK_DIR = Path(__file__).parent / "mock"


async def run_ingestion_pipeline(force: bool = False) -> dict:
    await init_db()
    results = {}

    if force or not await is_data_fresh("budget"):
        results["budget"] = await ingest_budget_data()
    else:
        results["budget"] = {"status": "skipped", "reason": "data is fresh"}

    if force or not await is_data_fresh("congressmen", max_age_hours=168):
        results["congressmen"] = await ingest_congressmen()
    else:
        results["congressmen"] = {"status": "skipped", "reason": "data is fresh"}

    # Document scraping (always run if not mock mode, or if forced)
    if not settings.use_mock_data or force:
        results["documents"] = await run_document_scraping(force=force)
    else:
        results["documents"] = {"status": "skipped", "reason": "mock mode active"}

    return results


async def run_document_scraping(force: bool = False) -> dict:
    """Run DBM BESF, GAA, BetterGov, and PhilGEPS scrapers."""
    import logging
    log = logging.getLogger(__name__)
    results = {}

    try:
        from data.dbm_scraper import run_dbm_scraper
        results["dbm"] = await run_dbm_scraper(fiscal_year=2024, force=force)
        log.info(f"DBM scraping done: {results['dbm']}")
    except Exception as e:
        log.error(f"DBM scraper error: {e}")
        results["dbm"] = {"error": str(e)}

    try:
        from data.bettergov_scraper import run_bettergov_scraper
        results["bettergov"] = await run_bettergov_scraper(force=force)
        log.info(f"BetterGov scraping done: {results['bettergov']}")
    except Exception as e:
        log.error(f"BetterGov scraper error: {e}")
        results["bettergov"] = {"error": str(e)}

    return results


async def ingest_budget_data() -> dict:
    log_id = str(uuid.uuid4())
    use_mock = settings.use_mock_data
    rows = 0
    error = None

    try:
        if use_mock:
            df = _load_mock_budget()
        else:
            df = await _fetch_live_budget()
            if df is None:
                df = _load_mock_budget()
                use_mock = True

        async with get_session() as session:
            await session.execute(delete(BudgetItem))
            for _, row in df.iterrows():
                item = BudgetItem(
                    year=int(row.get("year", 2024)),
                    region=str(row.get("region", "")),
                    province=str(row.get("province", "")) if pd.notna(row.get("province")) else None,
                    district_code=str(row.get("district_code", "")) if pd.notna(row.get("district_code")) else None,
                    agency=str(row.get("agency", "")),
                    department=str(row.get("department", "")) if pd.notna(row.get("department")) else None,
                    program=str(row.get("program", "")),
                    allocation_php=float(row.get("allocation_php", 0)),
                    obligation_php=float(row.get("obligation_php", 0)),
                    disbursement_php=float(row.get("disbursement_php", 0)),
                    source="mock" if use_mock else "live",
                )
                session.add(item)
                rows += 1

        log = DataIngestionLog(
            id=log_id,
            source="budget",
            status="success",
            rows_processed=rows,
            is_mock=use_mock,
        )
        async with get_session() as session:
            session.add(log)

    except Exception as e:
        error = str(e)
        log = DataIngestionLog(
            id=log_id,
            source="budget",
            status="failed",
            rows_processed=rows,
            error_msg=error,
            is_mock=use_mock,
        )
        async with get_session() as session:
            session.add(log)

    return {"status": "failed" if error else "success", "rows": rows, "mock": use_mock, "error": error}


async def ingest_congressmen() -> dict:
    log_id = str(uuid.uuid4())
    use_mock = settings.use_mock_data
    rows = 0
    error = None

    try:
        if use_mock:
            data = _load_mock_congressmen()
        else:
            data = await _fetch_live_congressmen()
            if not data:
                data = _load_mock_congressmen()
                use_mock = True

        async with get_session() as session:
            await session.execute(delete(Congressman))
            for c in data:
                mc = Congressman(
                    open_congress_id=c.get("id") or c.get("open_congress_id"),
                    name=c.get("name", ""),
                    party=c.get("party"),
                    district_code=c.get("district_code"),
                    province=c.get("province"),
                    region=c.get("region"),
                    district_label=c.get("district_label"),
                    profile_url=c.get("profile_url"),
                )
                session.add(mc)
                rows += 1

        log = DataIngestionLog(
            id=log_id,
            source="congressmen",
            status="success",
            rows_processed=rows,
            is_mock=use_mock,
        )
        async with get_session() as session:
            session.add(log)

    except Exception as e:
        error = str(e)
        log = DataIngestionLog(
            id=log_id,
            source="congressmen",
            status="failed",
            rows_processed=rows,
            error_msg=error,
            is_mock=use_mock,
        )
        async with get_session() as session:
            session.add(log)

    return {"status": "failed" if error else "success", "rows": rows, "mock": use_mock, "error": error}


def _load_mock_budget() -> pd.DataFrame:
    return pd.read_csv(MOCK_DIR / "gaa_sample.csv")


def _load_mock_congressmen() -> list[dict]:
    with open(MOCK_DIR / "congressmen.json") as f:
        return json.load(f)


async def _fetch_live_budget() -> pd.DataFrame | None:
    """Attempt to fetch live GAA data from BetterGov.PH or DBM. Falls back to None."""
    try:
        import httpx
        # BetterGov published GAA CSV
        url = "https://data.bettergov.ph/dataset/general-appropriations-act/resource/gaa-2024.csv"
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and len(resp.content) > 1000:
                from io import StringIO
                df = pd.read_csv(StringIO(resp.text))
                # Normalize column names to our schema
                df = _normalize_budget_columns(df)
                return df
    except Exception:
        pass
    return None


def _normalize_budget_columns(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "Year": "year", "YEAR": "year",
        "Region": "region", "REGION": "region",
        "Province": "province", "PROVINCE": "province",
        "District": "district_code", "DISTRICT": "district_code",
        "Agency": "agency", "AGENCY": "agency",
        "Department": "department", "DEPARTMENT": "department",
        "Program": "program", "PROGRAM": "program",
        "Allocation": "allocation_php", "ALLOCATION": "allocation_php",
        "Obligation": "obligation_php", "OBLIGATION": "obligation_php",
        "Disbursement": "disbursement_php", "DISBURSEMENT": "disbursement_php",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    required = ["year", "region", "agency", "program", "allocation_php", "disbursement_php"]
    for col in required:
        if col not in df.columns:
            df[col] = "" if col in ("region", "agency", "program") else 0
    return df


async def _fetch_live_congressmen() -> list[dict] | None:
    """Fetch congressman profiles from Open Congress API."""
    try:
        import httpx
        base = settings.open_congress_api_base
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(f"{base}/api/legislators", params={"per_page": 300, "chamber": "house"})
            if resp.status_code == 200:
                payload = resp.json()
                items = payload.get("data") or payload if isinstance(payload, list) else []
                result = []
                for item in items:
                    result.append({
                        "open_congress_id": str(item.get("id", "")),
                        "name": item.get("name") or item.get("full_name", ""),
                        "party": item.get("party", {}).get("name") if isinstance(item.get("party"), dict) else item.get("party", ""),
                        "district_code": item.get("district_code") or item.get("district", {}).get("code", ""),
                        "province": item.get("province") or item.get("district", {}).get("province", ""),
                        "region": item.get("region") or item.get("district", {}).get("region", ""),
                        "district_label": item.get("district_label") or item.get("district", {}).get("label", ""),
                        "profile_url": item.get("profile_url", ""),
                    })
                return result if result else None
    except Exception:
        pass
    return None
