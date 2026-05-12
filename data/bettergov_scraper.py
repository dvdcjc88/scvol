"""BetterGov.PH and PhilGEPS open data scrapers.

Sources:
1. data.bettergov.ph  — GAA budget CSV datasets
2. open.philgeps.gov.ph — Procurement award notices
3. philgeps.bettergov.ph — BetterGov's PhilGEPS explorer (best-effort)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

from data.models import Document, DocumentRow, BudgetItem, Congressman
from data.pipeline import get_session, init_db

log = logging.getLogger(__name__)

RAW_DIR = Path(__file__).parent / "raw" / "bettergov"
RAW_DIR.mkdir(parents=True, exist_ok=True)

PHILGEPS_RAW_DIR = Path(__file__).parent / "raw" / "philgeps"
PHILGEPS_RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,text/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://data.bettergov.ph/",
}

# ── BetterGov data sources ───────────────────────────────────────────────────
# Resource IDs from data.bettergov.ph — these are the direct CSV download endpoints
BETTERGOV_DATASETS = [
    {
        "id": "bg_gaa_2024",
        "title": "BetterGov GAA FY 2024",
        "url": "https://data.bettergov.ph/datasets/8/resources/26/download",
        "fallback_urls": [
            "https://data.bettergov.ph/datasets/8/resources/26",
            "https://raw.githubusercontent.com/bettergovph/open-data-visualization/main/data/gaa-2024.csv",
            "https://raw.githubusercontent.com/bettergovph/open-budget/main/data/gaa_2024.csv",
        ],
        "format": "csv",
        "fiscal_year": 2024,
    },
    {
        "id": "bg_gaa_2023",
        "title": "BetterGov GAA FY 2023",
        "url": "https://data.bettergov.ph/datasets/7/resources/25/download",
        "fallback_urls": [
            "https://raw.githubusercontent.com/bettergovph/open-budget/main/data/gaa_2023.csv",
        ],
        "format": "csv",
        "fiscal_year": 2023,
    },
    {
        "id": "bg_congress",
        "title": "BetterGov Open Congress Representatives",
        "url": "https://open-congress-api.bettergov.ph/api/legislators?per_page=300&chamber=house",
        "fallback_urls": [],
        "format": "json",
        "fiscal_year": None,
    },
]

# PhilGEPS open data endpoints
PHILGEPS_ENDPOINTS = [
    {
        "id": "philgeps_awards_2024",
        "title": "PhilGEPS Award Notices 2024",
        "url": "https://open.philgeps.gov.ph/api/awards?year=2024&format=csv",
        "fallback_urls": [
            "https://open.philgeps.gov.ph/downloads/awards-2024.csv",
            "https://open.philgeps.gov.ph/data/awards/2024",
        ],
        "format": "csv",
        "fiscal_year": 2024,
    },
    {
        "id": "philgeps_awards_2023",
        "title": "PhilGEPS Award Notices 2023",
        "url": "https://open.philgeps.gov.ph/api/awards?year=2023&format=csv",
        "fallback_urls": [
            "https://open.philgeps.gov.ph/downloads/awards-2023.csv",
        ],
        "format": "csv",
        "fiscal_year": 2023,
    },
]


async def _try_download_csv(client: httpx.AsyncClient, urls: list[str],
                            dest: Path) -> Optional[str]:
    """Try multiple URLs, return the working one."""
    for url in urls:
        try:
            r = await client.get(url, headers=HEADERS, timeout=60, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 500:
                content_type = r.headers.get("content-type", "")
                # Accept CSV, JSON, or octet-stream
                with open(dest, "wb") as f:
                    f.write(r.content)
                log.info(f"Downloaded {len(r.content):,} bytes from {url}")
                return url
        except Exception as e:
            log.debug(f"Failed {url}: {e}")
    return None


async def scrape_bettergov_datasets(force: bool = False) -> dict:
    """Download all BetterGov datasets."""
    stats = {"downloaded": 0, "parsed": 0, "failed": 0, "skipped": 0}

    async with httpx.AsyncClient(timeout=60) as client:
        for dataset in BETTERGOV_DATASETS:
            dest = RAW_DIR / f"{dataset['id']}.{'csv' if dataset['format'] == 'csv' else 'json'}"

            if dest.exists() and dest.stat().st_size > 500 and not force:
                stats["skipped"] += 1
                log.info(f"Skipping {dataset['id']} (cached)")
                continue

            urls = [dataset["url"]] + dataset.get("fallback_urls", [])
            working_url = await _try_download_csv(client, urls, dest)

            if not working_url:
                log.warning(f"All URLs failed for {dataset['id']}")
                stats["failed"] += 1
                await _register_failed_doc(dataset)
                continue

            stats["downloaded"] += 1
            n = await _ingest_bettergov_file(dest, dataset, working_url)
            stats["parsed"] += n
            log.info(f"Ingested {n} records from {dataset['id']}")
            await asyncio.sleep(0.5)

    return stats


async def scrape_philgeps(force: bool = False) -> dict:
    """Download PhilGEPS open procurement data."""
    stats = {"downloaded": 0, "parsed": 0, "failed": 0, "skipped": 0}

    async with httpx.AsyncClient(timeout=60) as client:
        for endpoint in PHILGEPS_ENDPOINTS:
            dest = PHILGEPS_RAW_DIR / f"{endpoint['id']}.csv"

            if dest.exists() and dest.stat().st_size > 500 and not force:
                stats["skipped"] += 1
                continue

            urls = [endpoint["url"]] + endpoint.get("fallback_urls", [])
            working_url = await _try_download_csv(client, urls, dest)

            if not working_url:
                log.warning(f"PhilGEPS download failed for {endpoint['id']}")
                stats["failed"] += 1
                await _register_failed_doc(endpoint)
                continue

            stats["downloaded"] += 1
            n = await _ingest_philgeps_file(dest, endpoint, working_url)
            stats["parsed"] += n
            log.info(f"Ingested {n} PhilGEPS records from {endpoint['id']}")

    return stats


async def _ingest_bettergov_file(path: Path, dataset: dict, url: str) -> int:
    """Parse a BetterGov file and write to DB."""
    fiscal_year = dataset.get("fiscal_year")
    fmt = dataset.get("format", "csv")

    try:
        if fmt == "json":
            return await _ingest_congress_json(path, dataset, url)

        df = pd.read_csv(path, low_memory=False, encoding="utf-8", on_bad_lines="skip")
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Store as Document
        doc = await _upsert_doc(
            source="bettergov", doc_type="budget_csv",
            table_code=dataset["id"], title=dataset["title"],
            url=url, fiscal_year=fiscal_year,
        )

        # Try to map to BudgetItem schema
        n = await _df_to_budget_items(df, fiscal_year or 2024, "live")
        log.info(f"  → {n} BudgetItem rows from {dataset['id']}")

        # Also store raw rows in DocumentRow
        rows_stored = 0
        async with get_session() as session:
            for i, row in df.head(2000).iterrows():
                session.add(DocumentRow(
                    document_id=doc.id,
                    row_index=i,
                    row_data=json.dumps(row.to_dict(), default=str),
                    region=_col_val(row, ["region", "REGION"]),
                    agency=_col_val(row, ["agency", "department", "AGENCY"]),
                    amount_php=_num_val(row, ["allocation", "amount", "allotment"]),
                    fiscal_year=fiscal_year,
                ))
                rows_stored += 1

        await _mark_doc_ok(doc.id, len(df))
        return rows_stored

    except Exception as e:
        log.error(f"Failed to parse {path}: {e}")
        return 0


async def _ingest_congress_json(path: Path, dataset: dict, url: str) -> int:
    """Parse Open Congress API JSON and upsert Congressman records."""
    try:
        with open(path) as f:
            data = json.load(f)

        items = data.get("data") or (data if isinstance(data, list) else [])
        if not items:
            return 0

        from sqlalchemy import delete
        async with get_session() as session:
            for item in items:
                name = item.get("name") or item.get("full_name", "")
                if not name:
                    continue
                party_raw = item.get("party", {})
                party = party_raw.get("name") if isinstance(party_raw, dict) else str(party_raw or "")
                dist = item.get("district") or {}
                mc = Congressman(
                    open_congress_id=str(item.get("id", "")),
                    name=name,
                    party=party,
                    district_code=item.get("district_code") or (dist.get("code") if isinstance(dist, dict) else ""),
                    province=item.get("province") or (dist.get("province") if isinstance(dist, dict) else ""),
                    region=item.get("region") or (dist.get("region") if isinstance(dist, dict) else ""),
                    district_label=item.get("district_label") or (dist.get("label") if isinstance(dist, dict) else ""),
                    profile_url=item.get("profile_url", ""),
                )
                session.add(mc)

        return len(items)
    except Exception as e:
        log.error(f"Congress JSON parse failed: {e}")
        return 0


async def _ingest_philgeps_file(path: Path, endpoint: dict, url: str) -> int:
    """Parse PhilGEPS CSV and store as DocumentRows."""
    try:
        df = pd.read_csv(path, low_memory=False, on_bad_lines="skip")
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        doc = await _upsert_doc(
            source="philgeps", doc_type="procurement_awards",
            table_code=endpoint["id"], title=endpoint["title"],
            url=url, fiscal_year=endpoint.get("fiscal_year"),
        )

        rows_stored = 0
        async with get_session() as session:
            for i, row in df.head(3000).iterrows():
                session.add(DocumentRow(
                    document_id=doc.id,
                    row_index=i,
                    row_data=json.dumps(row.to_dict(), default=str),
                    region=_col_val(row, ["region", "area", "location"]),
                    agency=_col_val(row, ["procuring_entity", "agency", "entity_name"]),
                    amount_php=_num_val(row, ["approved_budget", "contract_amount", "award_amount"]),
                    fiscal_year=endpoint.get("fiscal_year"),
                ))
                rows_stored += 1

        await _mark_doc_ok(doc.id, len(df))
        return rows_stored

    except Exception as e:
        log.error(f"PhilGEPS parse failed: {e}")
        return 0


async def _df_to_budget_items(df: pd.DataFrame, fiscal_year: int, source: str) -> int:
    """Try to map DataFrame columns to BudgetItem and upsert."""
    col_map = {
        "region": ["region", "region_name"],
        "province": ["province", "province_name"],
        "district_code": ["district", "district_code", "congressional_district"],
        "agency": ["agency", "agency_name", "implementing_agency"],
        "department": ["department", "dept"],
        "program": ["program", "project", "program_project"],
        "allocation_php": ["allotment", "allocation", "appropriation", "nep_amount", "gaa_amount"],
        "obligation_php": ["obligation", "obligations"],
        "disbursement_php": ["disbursement", "disbursements", "actual_disbursement"],
    }

    def find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
        for c in candidates:
            if c in df.columns:
                return c
            # fuzzy
            for col in df.columns:
                if c in col:
                    return col
        return None

    mapping = {field: find_col(df, candidates) for field, candidates in col_map.items()}

    if not mapping.get("agency") and not mapping.get("program"):
        return 0  # Can't map without agency/program

    rows_added = 0
    async with get_session() as session:
        for _, row in df.head(3000).iterrows():
            def v(field, default=""):
                col = mapping.get(field)
                return str(row[col]).strip() if col and pd.notna(row.get(col)) else default

            def num(field):
                col = mapping.get(field)
                if not col:
                    return 0.0
                try:
                    return float(str(row.get(col, "0")).replace(",", "").replace("(", "").replace(")", ""))
                except Exception:
                    return 0.0

            item = BudgetItem(
                year=fiscal_year,
                region=v("region", "Unknown"),
                province=v("province") or None,
                district_code=v("district_code") or None,
                agency=v("agency", "Unknown"),
                department=v("department") or None,
                program=v("program", "Unknown"),
                allocation_php=num("allocation_php"),
                obligation_php=num("obligation_php"),
                disbursement_php=num("disbursement_php"),
                source=source,
            )
            session.add(item)
            rows_added += 1

    return rows_added


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _upsert_doc(source: str, doc_type: str, table_code: str, title: str,
                      url: str, fiscal_year: Optional[int]) -> Document:
    from sqlalchemy import select
    async with get_session() as session:
        result = await session.execute(select(Document).where(Document.url == url).limit(1))
        doc = result.scalar_one_or_none()
        if not doc:
            doc = Document(source=source, doc_type=doc_type, table_code=table_code,
                           title=title, url=url, fiscal_year=fiscal_year, status="pending")
            session.add(doc)
            await session.flush()
            await session.refresh(doc)
    return doc


async def _mark_doc_ok(doc_id: str, row_count: int) -> None:
    from sqlalchemy import select
    async with get_session() as session:
        result = await session.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = "ok"
            doc.scraped_at = datetime.utcnow()


async def _register_failed_doc(dataset: dict) -> None:
    async with get_session() as session:
        session.add(Document(
            source=dataset.get("source", "bettergov"),
            doc_type="csv",
            table_code=dataset.get("id", ""),
            title=dataset.get("title", ""),
            url=dataset.get("url", ""),
            fiscal_year=dataset.get("fiscal_year"),
            status="failed",
            error_msg="All download URLs returned non-200",
            scraped_at=datetime.utcnow(),
        ))


def _col_val(row: pd.Series, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in row.index and pd.notna(row[c]):
            return str(row[c])[:64]
    return None


def _num_val(row: pd.Series, candidates: list[str]) -> Optional[float]:
    for c in candidates:
        if c in row.index:
            try:
                return float(str(row[c]).replace(",", "").replace("(", "").replace(")", ""))
            except Exception:
                pass
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_bettergov_scraper(force: bool = False) -> dict:
    await init_db()
    bg = await scrape_bettergov_datasets(force=force)
    pg = await scrape_philgeps(force=force)
    return {"bettergov": bg, "philgeps": pg}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bettergov_scraper(force=False))
