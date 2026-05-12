from __future__ import annotations

import asyncio
import json
from typing import Optional, Type

import pandas as pd
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from sqlalchemy import select, delete

from data.models import BudgetItem, Anomaly
from data.pipeline import get_session
from ml.isolation_forest import run_isolation_forest
from ml.zscore import run_zscore, combine_scores, build_anomaly_reason


class AnomalyDetectionInput(BaseModel):
    region: Optional[str] = Field(None, description="Limit detection to this region")
    year: Optional[int] = Field(None, description="Limit detection to this budget year")
    top_n: int = Field(10, description="Return top N anomalies by risk score")


class AnomalyDetectionTool(BaseTool):
    name: str = "anomaly_detection"
    description: str = (
        "Run Isolation Forest and Z-score anomaly detection on Philippine budget data. "
        "Returns the top anomalies with risk scores (1-10), anomaly reasons, and budget details."
    )
    args_schema: Type[BaseModel] = AnomalyDetectionInput

    def _run(self, region: str | None = None, year: int | None = None, top_n: int = 10) -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_run(region, year, top_n))
        finally:
            loop.close()

    async def _async_run(self, region, year, top_n) -> str:
        # Load budget data into DataFrame
        async with get_session() as session:
            stmt = select(BudgetItem)
            if region:
                stmt = stmt.where(BudgetItem.region.ilike(f"%{region}%"))
            if year:
                stmt = stmt.where(BudgetItem.year == year)
            result = await session.execute(stmt)
            items = result.scalars().all()

        if not items:
            return json.dumps({"error": "No budget data found. Run data ingestion first.", "anomalies": []})

        df = pd.DataFrame([{
            "id": i.id,
            "year": i.year,
            "region": i.region,
            "province": i.province,
            "district_code": i.district_code,
            "agency": i.agency,
            "program": i.program,
            "allocation_php": i.allocation_php,
            "obligation_php": i.obligation_php,
            "disbursement_php": i.disbursement_php,
        } for i in items])

        # Run ML
        df_if = run_isolation_forest(df, region=region)
        df_z = run_zscore(df)
        df_if["zscore_value"] = df_z["zscore_value"].values
        df_combined = combine_scores(df_if)
        df_combined["anomaly_reason"] = df_combined.apply(build_anomaly_reason, axis=1)

        # Sort by risk score, take top N
        top = df_combined.nlargest(top_n, "risk_score")

        # Persist anomalies to DB
        await self._persist_anomalies(top)

        anomalies = []
        for _, row in top.iterrows():
            anomalies.append({
                "district_code": row.get("district_code", ""),
                "region": row.get("region", ""),
                "province": row.get("province", ""),
                "agency": row.get("agency", ""),
                "program": row.get("program", ""),
                "year": int(row.get("year", 0)),
                "allocation_php": float(row.get("allocation_php", 0)),
                "disbursement_php": float(row.get("disbursement_php", 0)),
                "disbursement_rate": float(row.get("disbursement_rate", 0)),
                "risk_score": round(float(row.get("risk_score", 0)), 2),
                "anomaly_reason": row.get("anomaly_reason", ""),
                "if_score": round(float(row.get("if_score", 0)), 4),
                "zscore_value": round(float(row.get("zscore_value", 0)), 4),
            })

        return json.dumps({"total_analyzed": len(df), "top_anomalies": anomalies}, indent=2)

    async def _persist_anomalies(self, top_df: pd.DataFrame) -> None:  # called from within _async_run's event loop
        async with get_session() as session:
            for _, row in top_df.iterrows():
                budget_item_id = row.get("id")
                if not budget_item_id:
                    continue
                anomaly = Anomaly(
                    budget_item_id=budget_item_id,
                    method="combined",
                    zscore_value=float(row.get("zscore_value", 0)),
                    if_score=float(row.get("if_score", 0)),
                    disbursement_rate=float(row.get("disbursement_rate", 0)),
                    risk_score=round(float(row.get("risk_score", 0)), 2),
                    anomaly_reason=row.get("anomaly_reason", ""),
                )
                session.add(anomaly)
