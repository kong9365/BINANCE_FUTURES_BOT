"""Gate 1 — Spearman IC + t-stat."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class ICResult:
    feature: str
    horizon: str
    ic: float
    t_stat: float
    p_value: float
    n: int


def spearman_ic(feature: pd.Series, forward: pd.Series) -> tuple[float, float, float, int]:
    aligned = pd.concat([feature, forward], axis=1).dropna()
    n = len(aligned)
    if n < 30:
        return float("nan"), float("nan"), float("nan"), n
    ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman")
    if ic is None or np.isnan(ic):
        return float("nan"), float("nan"), float("nan"), n
    # t ≈ ic * sqrt(n-2) / sqrt(1-ic^2)
    denom = max(1e-12, 1.0 - ic * ic)
    t_stat = ic * np.sqrt(n - 2) / np.sqrt(denom)
    # two-sided normal approx for p
    from math import erfc, sqrt
    p_value = erfc(abs(t_stat) / sqrt(2))
    return float(ic), float(t_stat), float(p_value), n


def run_ic_panel(
    feature_cols: List[str],
    horizon_cols: Dict[str, str],
    frames: List[pd.DataFrame],
) -> List[ICResult]:
    """여러 심볼 feature 프레임을 pool 하여 IC 계산."""
    pooled = pd.concat(frames, ignore_index=False)
    out: List[ICResult] = []
    for feat in feature_cols:
        for hz_label, hz_col in horizon_cols.items():
            if feat not in pooled.columns or hz_col not in pooled.columns:
                continue
            ic, t, p, n = spearman_ic(pooled[feat], pooled[hz_col])
            out.append(ICResult(feat, hz_label, ic, t, p, n))
    return out


def best_ic(results: List[ICResult]) -> Optional[ICResult]:
    valid = [r for r in results if not np.isnan(r.ic)]
    if not valid:
        return None
    return max(valid, key=lambda r: r.ic)
