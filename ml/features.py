from __future__ import annotations

import numpy as np
import pandas as pd


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add anomaly-detection features to a budget DataFrame.

    Required input columns: allocation_php, obligation_php, disbursement_php,
    region, agency, year.
    """
    out = df.copy()

    # Core ratios — NaN where allocation is zero
    out["disbursement_rate"] = np.where(
        out["allocation_php"] > 0,
        out["disbursement_php"] / out["allocation_php"],
        np.nan,
    )
    out["obligation_rate"] = np.where(
        out["allocation_php"] > 0,
        out["obligation_php"] / out["allocation_php"],
        np.nan,
    )

    # Log-scaled allocation (size normalization)
    out["allocation_log"] = np.log1p(out["allocation_php"].clip(lower=0))

    # Regional percentile rank within year
    out["regional_percentile"] = out.groupby(["region", "year"])["allocation_php"].rank(pct=True)

    # Year-over-year allocation change per (district_code, agency, program) group
    group_cols = ["district_code", "agency"]
    out = out.sort_values("year")
    out["yoy_alloc_change"] = out.groupby(group_cols)["allocation_php"].pct_change()

    # Disbursement anomaly flag: rate < 5% or > 105%
    out["disbursement_flag"] = (
        (out["disbursement_rate"] < 0.05) | (out["disbursement_rate"] > 1.05)
    ).astype(int)

    # Fill remaining NaNs with 0 for ML compatibility
    feature_cols = [
        "disbursement_rate", "obligation_rate", "allocation_log",
        "regional_percentile", "yoy_alloc_change", "disbursement_flag",
    ]
    out[feature_cols] = out[feature_cols].fillna(0)

    return out


FEATURE_COLS = [
    "disbursement_rate",
    "obligation_rate",
    "allocation_log",
    "regional_percentile",
    "yoy_alloc_change",
    "disbursement_flag",
]
