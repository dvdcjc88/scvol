"""PDF parsing utilities using pdfplumber.

Extracts both raw text and structured tables from government budget PDFs.
Handles common issues: merged cells, headers spanning rows, numeric formatting.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def parse_pdf(path: str | Path) -> dict:
    """Parse a PDF and return extracted text + tables.

    Returns:
        {
            "page_count": int,
            "raw_text": str,          # all text joined
            "tables": [               # one entry per detected table
                {
                    "page": int,
                    "headers": [str, ...],
                    "rows": [[str, ...], ...]
                }
            ],
            "title": str | None,      # first non-empty line
        }
    """
    try:
        import pdfplumber
    except ImportError:
        return {"error": "pdfplumber not installed", "page_count": 0, "raw_text": "", "tables": []}

    result = {"page_count": 0, "raw_text": "", "tables": [], "title": None}

    try:
        with pdfplumber.open(str(path)) as pdf:
            result["page_count"] = len(pdf.pages)
            all_text_parts = []
            all_tables = []

            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract text
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                if text:
                    all_text_parts.append(text)

                # Extract tables with generous tolerance for merged cells
                tables = page.extract_tables({
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 5,
                    "join_tolerance": 3,
                })
                for table in (tables or []):
                    cleaned = _clean_table(table)
                    if cleaned and len(cleaned) >= 2:
                        headers = cleaned[0]
                        rows = cleaned[1:]
                        all_tables.append({
                            "page": page_num,
                            "headers": headers,
                            "rows": rows[:200],   # cap at 200 rows per table
                        })

            raw_text = "\n\n".join(all_text_parts)
            result["raw_text"] = raw_text[:500_000]  # cap at 500KB of text

            # Infer title from first non-empty line
            for line in raw_text.splitlines():
                line = line.strip()
                if len(line) > 10:
                    result["title"] = line[:200]
                    break

            result["tables"] = all_tables

    except Exception as e:
        result["error"] = str(e)

    return result


def _clean_table(raw_table: list) -> list[list[str]]:
    """Clean raw pdfplumber table rows: strip None, normalize whitespace."""
    cleaned = []
    for row in raw_table:
        if row is None:
            continue
        clean_row = []
        for cell in row:
            if cell is None:
                clean_row.append("")
            else:
                clean_row.append(re.sub(r"\s+", " ", str(cell)).strip())
        # Skip rows that are entirely empty
        if any(c for c in clean_row):
            cleaned.append(clean_row)
    return cleaned


def extract_amounts(text: str) -> list[float]:
    """Extract numeric amounts (in pesos) from text."""
    # Matches patterns like: 1,234,567 or 1,234,567.89 or (1,234)
    pattern = r"\(?([\d,]+(?:\.\d+)?)\)?"
    amounts = []
    for match in re.finditer(pattern, text):
        raw = match.group(1).replace(",", "")
        try:
            amounts.append(float(raw))
        except ValueError:
            pass
    return amounts


def table_rows_to_dicts(table: dict) -> list[dict]:
    """Convert a parsed table's rows to list of dicts keyed by headers."""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    result = []
    for row in rows:
        d = {}
        for i, cell in enumerate(row):
            key = headers[i] if i < len(headers) else f"col_{i}"
            d[key if key else f"col_{i}"] = cell
        result.append(d)
    return result


def infer_region(text: str) -> Optional[str]:
    """Try to extract a Philippine region name from text."""
    region_patterns = [
        (r"\bNCR\b|\bNational Capital Region\b", "NCR"),
        (r"\bRegion\s+I\b|\bIlocos\b", "Region I"),
        (r"\bRegion\s+II\b|\bCagayan Valley\b", "Region II"),
        (r"\bRegion\s+III\b|\bCentral Luzon\b", "Region III"),
        (r"\bRegion\s+IV[-\s]?A\b|\bCALABARZON\b", "Region IV-A"),
        (r"\bRegion\s+IV[-\s]?B\b|\bMIMAROP[AO]\b", "Region IV-B"),
        (r"\bRegion\s+V\b|\bBicol\b", "Region V"),
        (r"\bRegion\s+VI\b|\bWestern Visayas\b", "Region VI"),
        (r"\bRegion\s+VII\b|\bCentral Visayas\b", "Region VII"),
        (r"\bRegion\s+VIII\b|\bEastern Visayas\b", "Region VIII"),
        (r"\bRegion\s+IX\b|\bZamboanga\b", "Region IX"),
        (r"\bRegion\s+X\b|\bNorthern Mindanao\b", "Region X"),
        (r"\bRegion\s+XI\b|\bDavao\b", "Region XI"),
        (r"\bRegion\s+XII\b|\bSOCCSKSARGEN\b", "Region XII"),
        (r"\bRegion\s+XIII\b|\bCaraga\b", "Region XIII"),
        (r"\bCAR\b|\bCordillera\b", "CAR"),
        (r"\bBARMM\b|\bBangsamoro\b", "BARMM"),
    ]
    for pattern, region in region_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return region
    return None
