"""Gate 2 — Decile sort + 단조성."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd


@dataclass
class DecileResult:
    feature: str
    horizon: str
    decile_means: List[float]
    decile_counts: List[int]
    spread_pct: float
    monotonicity: float
    n: int


def decile_analysis(
    feature: pd.Series,
    forward: pd.Series,
    feature_name: str = "feature",
    horizon: str = "fwd",
    n_bins: int = 10,
) -> Optional[DecileResult]:
    df = pd.concat([feature.rename("f"), forward.rename("r")], axis=1).dropna()
    if len(df) < n_bins * 5:
        return None
    try:
        df["bin"] = pd.qcut(df["f"], n_bins, labels=False, duplicates="drop")
    except ValueError:
        return None
    means: List[float] = []
    counts: List[int] = []
    for b in sorted(df["bin"].unique()):
        sub = df[df["bin"] == b]["r"]
        means.append(float(sub.mean()) * 100.0)
        counts.append(int(len(sub)))
    if len(means) < 2:
        return None
    spread = means[-1] - means[0]
    idx = np.arange(len(means), dtype=float)
    mono = float(pd.Series(means).corr(pd.Series(idx), method="spearman"))
    if np.isnan(mono):
        mono = 0.0
    return DecileResult(
        feature_name, horizon, means, counts, spread, mono, len(df),
    )
