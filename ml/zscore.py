from __future__ import annotations

import numpy as np
import pandas as pd

from ml.features import engineer_features
from config import settings


def run_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Compute z-score anomaly on disbursement_rate grouped by (region, agency).

    Returns df with 'zscore_value', 'zscore_anomaly' columns.
    """
    work = engineer_features(df)

    # Compute z-score within each (region, agency) group
    def _zscore_group(series: pd.Series) -> pd.Series:
        mean, std = series.mean(), series.std()
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=series.index)
        return (series - mean) / std

    work["zscore_value"] = work.groupby(["region", "agency"])["disbursement_rate"].transform(_zscore_group)
    work["zscore_value"] = work["zscore_value"].abs()  # Use magnitude
    work["zscore_anomaly"] = work["zscore_value"] > settings.zscore_threshold

    return work


def combine_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Merge IF score and Z-score into a single risk_score on [1, 10] scale.

    Requires columns: if_score, zscore_value, disbursement_flag from previous steps.
    """
    if "if_score" not in df.columns:
        df["if_score"] = 0.0
    if "zscore_value" not in df.columns:
        df["zscore_value"] = 0.0
    if "disbursement_flag" not in df.columns:
        df["disbursement_flag"] = 0

    # Normalize z-score component to [0,1]
    max_z = df["zscore_value"].max()
    z_normalized = df["zscore_value"] / max_z if max_z > 0 else df["zscore_value"]

    combined = (
        0.4 * df["if_score"]
        + 0.4 * z_normalized
        + 0.2 * df["disbursement_flag"].astype(float)
    )

    # Scale to 1–10
    min_c, max_c = combined.min(), combined.max()
    if max_c == min_c:
        df["risk_score"] = 1.0
    else:
        df["risk_score"] = 1 + 9 * (combined - min_c) / (max_c - min_c)

    return df


def build_anomaly_reason(row: pd.Series) -> str:
    reasons = []
    dr = row.get("disbursement_rate", 0)
    if dr < 0.05:
        reasons.append(f"Very low disbursement rate ({dr:.1%})")
    elif dr > 1.05:
        reasons.append(f"Disbursement exceeds allocation ({dr:.1%})")

    z = row.get("zscore_value", 0)
    if z > settings.zscore_threshold:
        reasons.append(f"Statistical outlier (z={z:.1f}σ within region/agency group)")

    if_s = row.get("if_score", 0)
    if if_s > 0.7:
        reasons.append(f"Isolation Forest anomaly score {if_s:.2f}")

    yoy = row.get("yoy_alloc_change", 0)
    if abs(yoy) > 2.0:
        reasons.append(f"Year-over-year allocation change {yoy:+.0%}")

    return "; ".join(reasons) if reasons else "Multi-factor statistical outlier"
