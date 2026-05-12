"""DBM (Department of Budget and Management) document scraper.

Downloads and parses:
1. BESF (Budget of Expenditures and Sources of Financing) FY 2024 PDF tables
2. GAA (General Appropriations Act) FY 2024 volumes (I-A, I-B, I-C)

All files are cached locally in data/raw/dbm/ to avoid re-downloading.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from data.models import Document, DocumentRow
from data.pipeline import get_session, init_db
from data.pdf_parser import parse_pdf, table_rows_to_dicts, infer_region, extract_amounts

log = logging.getLogger(__name__)

RAW_DIR = Path(__file__).parent / "raw" / "dbm"
RAW_DIR.mkdir(parents=True, exist_ok=True)

BESF_BASE = "https://www.dbm.gov.ph/wp-content/uploads/BESF/BESF2024"
SELECTED_BESF_BASE = f"{BESF_BASE}/Selected-BESF"

# ── BESF Table Catalogue ────────────────────────────────────────────────────
BESF_TABLES = {
    "A1": "Macroeconomic Parameters 2022-2026",
    "A2": "National Government Fiscal Program",
    "A3": "Consolidated Public Sector Financial Position",
    "A4": "National Government Revenue Program",
    "A5": "National Government Expenditures by Sector",
    "A6": "Public Sector Debt",
    "A7": "Other Government Entities Financial Position",
    "B1": "Summary of Expenditures by Allotment Class",
    "B2": "Social Services Expenditures",
    "B3": "Economic Services Expenditures",
    "B4": "General Public Services Expenditures",
    "B5": "Sectoral Allocation (NEP)",
    "B5a": "Sectoral Allocation (GAA) - National Government",
    "B6": "Regional Allocation of Expenditures",
    "B7": "Expenditure Program by Department",
    "B8": "Expenditures by Implementation Mode",
    "B9": "Expenditures by Source of Funds",
    "B10": "Expenditures by Type",
    "B11": "Personnel Services by Department",
    "B12": "MOOE by Sub-Account",
    "B12a": "Multi-Year Contractual Authorities (MYCA)",
    "B13": "Capital Outlays by Department",
    "B14": "Infrastructure Outlays by Sector",
    "B15": "Earmarked Revenues",
    "C1": "Revenue Program by Source",
    "C2": "Tax Revenue Program",
    "C3": "Non-Tax Revenue Program",
    "C4": "Grants and Assistance",
    "C5": "Revenue Measures",
    "D1": "Financing Program",
    "D2": "Domestic Financing",
    "D3": "Foreign Financing",
    "D4": "Privatization Receipts",
    "D5": "Financing Summary",
    "E1": "Debt Service Program",
    "E2": "National Government Debt Stock",
    "E3": "Contingent Liabilities",
    "F1": "Comparison of Allocation to LGUs",
    "F2": "Share of LGUs from National Taxes",
    "F3": "Special Education Fund",
    "F4": "Calamity Fund Allocation",
    "F5": "Local Government Unit Shares",
    "F6": "Allocation by Province",
    "F7": "Allocation by City",
    "F8": "Allocation by Municipality",
    "F9": "Barangay Fund Allocation",
    "F10": "Statement of Receipts and Expenditures by Province",
    "G1": "GOCC Subsidy Requirements",
    "G2": "Equity Contributions to GOCCs",
    "G3": "Net Lending Program",
    "H1": "Foreign-Assisted Projects",
    "H2": "ODA Loan Portfolio",
    "H3": "Grant-Funded Projects",
    "I1": "Performance Targets by Agency",
    "I2": "Agency Performance Indicators",
    "I3": "Gender and Development Budget",
    "J1": "Tax Expenditures Summary",
    "J1a": "Income Tax Expenditures",
    "J1b": "VAT Expenditures",
    "J1c": "Investment Tax Expenditures",
    "J2": "Fiscal Incentives Cost",
    "J3": "Tax Expenditures by Sector",
}

# GAA 2024 volume pages — we need to scrape the page to find PDF links
GAA_PAGES = {
    "gaa_vol_1a": {
        "url": "https://www.dbm.gov.ph/index.php?view=article&id=2522:general-appropriations-act-gaa-volume-i-a-fy-2024&catid=167",
        "title": "General Appropriations Act FY 2024 Volume I-A",
    },
    "gaa_vol_1b": {
        "url": "https://www.dbm.gov.ph/index.php?view=article&id=2523:general-appropriations-act-gaa-volume-i-b-fy-2024&catid=167",
        "title": "General Appropriations Act FY 2024 Volume I-B",
    },
    "gaa_vol_1c": {
        "url": "https://www.dbm.gov.ph/index.php?view=article&id=2524:general-appropriations-act-gaa-volume-i-c-fy-2024&catid=167",
        "title": "General Appropriations Act FY 2024 Volume I-C",
    },
}

# Browser-like headers — DBM sometimes checks User-Agent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.dbm.gov.ph/",
    "Connection": "keep-alive",
}


# ── HTTP helpers ─────────────────────────────────────────────────────────────

async def _probe_url(client: httpx.AsyncClient, url: str) -> bool:
    """Check if a URL exists (HTTP 200) without downloading the full body."""
    try:
        r = await client.head(url, headers=HEADERS, timeout=10, follow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


async def _download_file(client: httpx.AsyncClient, url: str, dest: Path,
                         max_bytes: int = 60 * 1024 * 1024) -> bool:
    """Download a file to dest. Returns True on success."""
    try:
        async with client.stream("GET", url, headers=HEADERS, timeout=120,
                                 follow_redirects=True) as r:
            if r.status_code != 200:
                return False
            content_type = r.headers.get("content-type", "")
            # Only accept PDF or Excel
            if not any(t in content_type for t in ("pdf", "excel", "spreadsheet", "octet-stream")):
                return False
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        log.warning(f"File too large, truncating: {url}")
                        break
        return dest.stat().st_size > 100
    except Exception as e:
        log.warning(f"Download failed {url}: {e}")
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


async def _scrape_page_for_links(client: httpx.AsyncClient, url: str) -> list[str]:
    """Scrape an HTML page and extract PDF/xlsx links."""
    try:
        r = await client.get(url, headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return []
        import re
        # Extract href attributes pointing to PDF or Excel files
        links = re.findall(r'href=["\']([^"\']+\.(?:pdf|xlsx|xls))["\']', r.text, re.IGNORECASE)
        base = "https://www.dbm.gov.ph"
        full_links = []
        for link in links:
            if link.startswith("http"):
                full_links.append(link)
            elif link.startswith("/"):
                full_links.append(base + link)
        return list(dict.fromkeys(full_links))  # deduplicate, preserve order
    except Exception as e:
        log.warning(f"Page scrape failed {url}: {e}")
        return []


# ── Document storage ─────────────────────────────────────────────────────────

async def _upsert_document(source: str, doc_type: str, table_code: Optional[str],
                           title: str, url: str, fiscal_year: int) -> Document:
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Document).where(Document.url == url).limit(1)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            doc = Document(
                source=source, doc_type=doc_type, table_code=table_code,
                title=title, url=url, fiscal_year=fiscal_year, status="pending",
            )
            session.add(doc)
            await session.flush()
            await session.refresh(doc)
    return doc


async def _save_document_content(doc_id: str, parsed: dict, file_size: int) -> int:
    """Save extracted text + rows to DB. Returns row count."""
    async with get_session() as session:
        from sqlalchemy import select, delete
        result = await session.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return 0

        doc.raw_text = parsed.get("raw_text", "")[:500_000]
        doc.page_count = parsed.get("page_count", 0)
        doc.file_size_bytes = file_size
        doc.status = "ok" if not parsed.get("error") else "failed"
        doc.error_msg = parsed.get("error")
        doc.scraped_at = datetime.utcnow()
        if parsed.get("title") and not doc.title:
            doc.title = parsed["title"]

        # Clear old rows
        await session.execute(delete(DocumentRow).where(DocumentRow.document_id == doc_id))

        # Save structured rows from tables
        row_index = 0
        for table in parsed.get("tables", []):
            for row_dict in table_rows_to_dicts(table):
                row_text = json.dumps(row_dict)
                region = infer_region(row_text)
                amounts = extract_amounts(row_text)
                amount = max(amounts) if amounts else None
                session.add(DocumentRow(
                    document_id=doc_id,
                    row_index=row_index,
                    row_data=row_text,
                    region=region,
                    agency=None,
                    amount_php=amount,
                    fiscal_year=doc.fiscal_year,
                ))
                row_index += 1
                if row_index >= 5000:  # cap rows per document
                    break

    return row_index


# ── Main scraper routines ────────────────────────────────────────────────────

async def scrape_besf_tables(fiscal_year: int = 2024,
                             force: bool = False) -> dict:
    """Discover and download all available BESF tables for the given FY."""
    stats = {"probed": 0, "downloaded": 0, "parsed": 0, "failed": 0, "skipped": 0}
    base = f"https://www.dbm.gov.ph/wp-content/uploads/BESF/BESF{fiscal_year}"

    async with httpx.AsyncClient(timeout=30) as client:
        for code, description in BESF_TABLES.items():
            url = f"{base}/{code}.pdf"
            dest = RAW_DIR / f"besf_{fiscal_year}_{code}.pdf"

            # Skip if already downloaded and not forced
            if dest.exists() and dest.stat().st_size > 100 and not force:
                stats["skipped"] += 1
                await _ensure_doc_in_db(url, code, description, fiscal_year)
                continue

            # Probe first to avoid 404 noise
            stats["probed"] += 1
            exists = await _probe_url(client, url)
            if not exists:
                # Try alternative path (some tables use lowercase or different naming)
                alt_url = f"{base}/{code.lower()}.pdf"
                exists = await _probe_url(client, alt_url)
                if exists:
                    url = alt_url
                else:
                    log.debug(f"Not found: {url}")
                    stats["failed"] += 1
                    continue

            # Download
            ok = await _download_file(client, url, dest)
            if not ok:
                log.warning(f"Download failed: {url}")
                stats["failed"] += 1
                continue

            stats["downloaded"] += 1
            log.info(f"Downloaded BESF {code}: {dest.stat().st_size:,} bytes")

            # Parse and store
            n_rows = await _parse_and_store(
                path=dest, url=url, source="dbm_besf", doc_type="besf_table",
                table_code=code, title=f"Table {code}: {description}", fiscal_year=fiscal_year,
            )
            stats["parsed"] += 1
            log.info(f"  → {n_rows} rows extracted from {code}")

            # Polite delay
            await asyncio.sleep(0.5)

    return stats


async def scrape_gaa_volumes(fiscal_year: int = 2024, force: bool = False) -> dict:
    """Scrape GAA volume pages for PDF links, then download and parse."""
    stats = {"probed": 0, "downloaded": 0, "parsed": 0, "failed": 0}

    async with httpx.AsyncClient(timeout=60) as client:
        for vol_key, vol_info in GAA_PAGES.items():
            dest_prefix = RAW_DIR / f"gaa_{fiscal_year}_{vol_key}"

            # Try scraping the page for PDF links
            links = await _scrape_page_for_links(client, vol_info["url"])
            if not links:
                # Fall back to guessing the direct URL
                links = [
                    f"https://www.dbm.gov.ph/wp-content/uploads/GAA/GAA{fiscal_year}/GAA{fiscal_year}-{vol_key.upper().replace('_', '-')}.pdf"
                ]
            log.info(f"Found {len(links)} links for {vol_key}: {links[:3]}")

            for i, link in enumerate(links[:5]):  # process up to 5 links per volume
                dest = Path(str(dest_prefix) + f"_{i}.pdf")
                if dest.exists() and not force:
                    stats["skipped"] = stats.get("skipped", 0) + 1
                    continue

                ok = await _download_file(client, link, dest, max_bytes=100 * 1024 * 1024)
                if not ok:
                    stats["failed"] += 1
                    continue

                stats["downloaded"] += 1
                n_rows = await _parse_and_store(
                    path=dest, url=link, source="dbm_gaa", doc_type="gaa_volume",
                    table_code=vol_key, title=vol_info["title"], fiscal_year=fiscal_year,
                )
                stats["parsed"] += 1
                await asyncio.sleep(1.0)

    return stats


async def _ensure_doc_in_db(url: str, code: str, description: str, fiscal_year: int) -> None:
    """Ensure a document record exists in DB (for already-downloaded files)."""
    from sqlalchemy import select
    async with get_session() as session:
        result = await session.execute(select(Document).where(Document.url == url).limit(1))
        if not result.scalar_one_or_none():
            session.add(Document(
                source="dbm_besf", doc_type="besf_table", table_code=code,
                title=f"Table {code}: {description}", url=url,
                fiscal_year=fiscal_year, status="cached",
            ))


async def _parse_and_store(path: Path, url: str, source: str, doc_type: str,
                           table_code: Optional[str], title: str, fiscal_year: int) -> int:
    parsed = parse_pdf(path)
    doc = await _upsert_document(source, doc_type, table_code, title, url, fiscal_year)
    n_rows = await _save_document_content(doc.id, parsed, path.stat().st_size)
    return n_rows


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_dbm_scraper(fiscal_year: int = 2024, force: bool = False) -> dict:
    """Run the full DBM scraping pipeline."""
    await init_db()
    log.info(f"Starting DBM scraper for FY {fiscal_year}...")

    besf_stats = await scrape_besf_tables(fiscal_year=fiscal_year, force=force)
    log.info(f"BESF: {besf_stats}")

    gaa_stats = await scrape_gaa_volumes(fiscal_year=fiscal_year, force=force)
    log.info(f"GAA: {gaa_stats}")

    return {"besf": besf_stats, "gaa": gaa_stats}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_dbm_scraper(fiscal_year=2024, force=False))
