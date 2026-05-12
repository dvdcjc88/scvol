from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ml.features import engineer_features, FEATURE_COLS
from config import settings


def run_isolation_forest(df: pd.DataFrame, region: str | None = None) -> pd.DataFrame:
    """Fit Isolation Forest on budget data; return df with 'if_score' and 'if_anomaly' columns.

    if_score is the raw decision_function value (more negative = more anomalous).
    Scores are normalized to [0, 1] where 1 = most anomalous.
    """
    work = engineer_features(df)

    if region:
        mask = work["region"].str.lower() == region.lower()
        fit_data = work[mask].copy()
    else:
        fit_data = work.copy()

    if len(fit_data) < 5:
        work["if_score"] = 0.0
        work["if_anomaly"] = False
        return work

    X = fit_data[FEATURE_COLS].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    clf = IsolationForest(
        contamination=settings.anomaly_contamination,
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_scaled)

    # Score ALL rows (not just the fit subset) using the same scaler
    X_all = work[FEATURE_COLS].values
    X_all_scaled = scaler.transform(X_all)
    raw_scores = clf.decision_function(X_all_scaled)

    # Normalize: map decision_function (negative=anomalous) to [0,1] anomaly intensity
    min_s, max_s = raw_scores.min(), raw_scores.max()
    if max_s == min_s:
        normalized = np.zeros_like(raw_scores)
    else:
        # Invert so higher = more anomalous
        normalized = (max_s - raw_scores) / (max_s - min_s)

    work["if_score"] = normalized
    work["if_anomaly"] = normalized > (1 - settings.anomaly_contamination * 2)

    return work
