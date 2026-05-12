from __future__ import annotations

import json
from typing import Type

import httpx
from pydantic import BaseModel, Field
from crewai.tools import BaseTool


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query string")
    max_results: int = Field(5, description="Maximum number of results to return")


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "Search the web for news, COA audit reports, or public records about "
        "Philippine government spending anomalies, budget irregularities, or specific congressmen. "
        "Use queries like 'Cagayan Region DPWH COA audit 2024' or 'Juan dela Cruz budget anomaly'."
    )
    args_schema: Type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5) -> str:
        try:
            results = _ddg_search(query, max_results)
        except Exception as e:
            results = [{"error": str(e), "note": "Web search unavailable; using fallback context"}]
        return json.dumps({"query": query, "results": results}, indent=2)


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Use DuckDuckGo instant answer API (no API key required)."""
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                return _fallback_results(query)
            data = resp.json()

        results = []

        # Abstract (main result)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"][:500],
                "url": data.get("AbstractURL", ""),
                "source": "DuckDuckGo Abstract",
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", "")[:400],
                    "url": topic.get("FirstURL", ""),
                    "source": "DuckDuckGo Related",
                })

        return results if results else _fallback_results(query)

    except Exception:
        return _fallback_results(query)


def _fallback_results(query: str) -> list[dict]:
    """Return curated fallback context about Philippine budget accountability."""
    return [
        {
            "title": "Commission on Audit (COA) Philippines",
            "snippet": "The COA regularly publishes audit reports on government agencies. "
                       "Key findings include delays in disbursements, single-bid procurement, "
                       "and ghost projects. Access reports at coa.gov.ph.",
            "url": "https://www.coa.gov.ph",
            "source": "fallback",
        },
        {
            "title": "BetterGov PH - Open Budget Data",
            "snippet": "BetterGov.PH provides processed open budget data including NEP/GAA 2020-2026 "
                       "and congressional district mappings. Useful for cross-referencing spending.",
            "url": "https://bettergov.ph",
            "source": "fallback",
        },
        {
            "title": f"Search context for: {query}",
            "snippet": "Web search temporarily unavailable. Manual verification recommended "
                       "via COA audit reports, DBM open data, and news archives.",
            "url": "",
            "source": "fallback",
        },
    ]
