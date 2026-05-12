from __future__ import annotations

import asyncio
import json
from typing import Optional, Type

from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from sqlalchemy import select

from data.models import Congressman
from data.pipeline import get_session


class CongressmanLookupInput(BaseModel):
    name: Optional[str] = Field(None, description="Congressman's name or partial name")
    district_code: Optional[str] = Field(None, description="District code, e.g. 'R3-7'")
    region: Optional[str] = Field(None, description="Region name, e.g. 'Region III'")
    party: Optional[str] = Field(None, description="Political party name")


class CongressmanLookupTool(BaseTool):
    name: str = "congressman_lookup"
    description: str = (
        "Look up Philippine congressman profiles by name, district code, region, or party. "
        "Returns names, districts, parties, and regional affiliations."
    )
    args_schema: Type[BaseModel] = CongressmanLookupInput

    def _run(self, name: str | None = None, district_code: str | None = None,
             region: str | None = None, party: str | None = None) -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_run(name, district_code, region, party))
        finally:
            loop.close()

    async def _async_run(self, name, district_code, region, party) -> str:
        async with get_session() as session:
            stmt = select(Congressman)
            if name:
                stmt = stmt.where(Congressman.name.ilike(f"%{name}%"))
            if district_code:
                stmt = stmt.where(Congressman.district_code == district_code)
            if region:
                stmt = stmt.where(Congressman.region.ilike(f"%{region}%"))
            if party:
                stmt = stmt.where(Congressman.party.ilike(f"%{party}%"))
            stmt = stmt.limit(20)
            result = await session.execute(stmt)
            items = result.scalars().all()

        rows = [
            {
                "id": c.id,
                "name": c.name,
                "party": c.party,
                "district_code": c.district_code,
                "district_label": c.district_label,
                "province": c.province,
                "region": c.region,
                "profile_url": c.profile_url,
            }
            for c in items
        ]
        return json.dumps({"count": len(rows), "congressmen": rows}, indent=2)


class DistrictMappingInput(BaseModel):
    district_code: str = Field(..., description="District code to map to congressman, e.g. 'R3-7'")


class DistrictToCongressmanTool(BaseTool):
    name: str = "district_to_congressman"
    description: str = (
        "Map a budget district code to the sitting congressman for that district. "
        "Returns congressman name, party, and district label."
    )
    args_schema: Type[BaseModel] = DistrictMappingInput

    def _run(self, district_code: str) -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_run(district_code))
        finally:
            loop.close()

    async def _async_run(self, district_code: str) -> str:
        async with get_session() as session:
            result = await session.execute(
                select(Congressman).where(Congressman.district_code == district_code).limit(1)
            )
            c = result.scalar_one_or_none()

        if c:
            return json.dumps({
                "found": True,
                "congressman": {
                    "name": c.name, "party": c.party,
                    "district_label": c.district_label,
                    "province": c.province, "region": c.region,
                }
            })
        return json.dumps({"found": False, "district_code": district_code})
