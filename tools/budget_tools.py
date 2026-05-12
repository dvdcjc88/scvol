from __future__ import annotations

import asyncio
import json
from typing import Optional, Type

import pandas as pd
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from sqlalchemy import select, text

from data.models import BudgetItem
from data.pipeline import get_session


class BudgetQueryInput(BaseModel):
    region: Optional[str] = Field(None, description="Filter by region name, e.g. 'Region III' or 'NCR'")
    year: Optional[int] = Field(None, description="Filter by budget year, e.g. 2024")
    agency: Optional[str] = Field(None, description="Filter by agency name, e.g. 'DPWH'")
    limit: int = Field(50, description="Maximum rows to return")


class BudgetQueryTool(BaseTool):
    name: str = "budget_query"
    description: str = (
        "Query the Philippine government budget database. Returns budget items "
        "with allocation, obligation, and disbursement amounts in PHP. "
        "Can filter by region, year, or agency."
    )
    args_schema: Type[BaseModel] = BudgetQueryInput

    def _run(self, region: str | None = None, year: int | None = None,
             agency: str | None = None, limit: int = 50) -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_run(region, year, agency, limit))
        finally:
            loop.close()

    async def _async_run(self, region, year, agency, limit) -> str:
        async with get_session() as session:
            stmt = select(BudgetItem)
            if region:
                stmt = stmt.where(BudgetItem.region.ilike(f"%{region}%"))
            if year:
                stmt = stmt.where(BudgetItem.year == year)
            if agency:
                stmt = stmt.where(BudgetItem.agency.ilike(f"%{agency}%"))
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            items = result.scalars().all()

        rows = [
            {
                "id": item.id,
                "year": item.year,
                "region": item.region,
                "province": item.province,
                "district_code": item.district_code,
                "agency": item.agency,
                "program": item.program,
                "allocation_php": item.allocation_php,
                "obligation_php": item.obligation_php,
                "disbursement_php": item.disbursement_php,
                "disbursement_rate": round(item.disbursement_php / item.allocation_php, 4)
                if item.allocation_php > 0 else 0,
            }
            for item in items
        ]
        return json.dumps({"count": len(rows), "items": rows}, indent=2)


class BudgetSummaryInput(BaseModel):
    group_by: str = Field("region", description="Group by: 'region', 'agency', 'district_code', or 'year'")
    year: Optional[int] = Field(None, description="Filter by year")


class BudgetSummaryTool(BaseTool):
    name: str = "budget_summary"
    description: str = (
        "Get aggregated budget summary statistics grouped by region, agency, or district. "
        "Returns totals and average disbursement rates per group."
    )
    args_schema: Type[BaseModel] = BudgetSummaryInput

    def _run(self, group_by: str = "region", year: int | None = None) -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_run(group_by, year))
        finally:
            loop.close()

    async def _async_run(self, group_by: str, year: int | None) -> str:
        valid_groups = {"region", "agency", "district_code", "year"}
        if group_by not in valid_groups:
            group_by = "region"

        year_filter = f"WHERE year = {year}" if year else ""
        query = f"""
            SELECT {group_by},
                   COUNT(*) as project_count,
                   SUM(allocation_php) as total_allocation,
                   SUM(disbursement_php) as total_disbursement,
                   ROUND(SUM(disbursement_php) * 1.0 / NULLIF(SUM(allocation_php), 0), 4) as avg_disbursement_rate
            FROM budget_items
            {year_filter}
            GROUP BY {group_by}
            ORDER BY total_allocation DESC
            LIMIT 30
        """
        async with get_session() as session:
            result = await session.execute(text(query))
            rows = [dict(r._mapping) for r in result.fetchall()]

        return json.dumps({"group_by": group_by, "rows": rows}, indent=2)
