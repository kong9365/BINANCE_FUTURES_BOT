"""Gate / IC / decile 단위 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.verification.decile_analysis import decile_analysis
from analytics.verification.feature_engineering import forward_return_open
from analytics.verification.gates import judge_ic, overall_verdict
from analytics.verification.ic_analysis import spearman_ic


def test_forward_return_no_lookahead():
    open_ = pd.Series([100.0, 110.0, 121.0, 133.1], index=pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC"))
    fwd = forward_return_open(open_, 1)
    assert abs(fwd.iloc[0] - (121.0 / 110.0 - 1.0)) < 1e-9
    assert np.isnan(fwd.iloc[-1])


def test_spearman_ic_positive_monotone():
    x = pd.Series(np.linspace(0, 1, 200))
    y = x * 0.1 + np.random.default_rng(0).normal(0, 0.01, 200)
    ic, t, p, n = spearman_ic(x, y)
    assert n == 200
    assert ic > 0.5


def test_judge_ic_thresholds():
    assert judge_ic(0.06) == "pass"
    assert judge_ic(0.03) == "borderline"
    assert judge_ic(0.01) == "fail"


def test_decile_spread():
    f = pd.Series(np.arange(100, dtype=float))
    r = f * 0.001
    d = decile_analysis(f, r)
    assert d is not None
    assert d.spread_pct > 0


def test_overall_pass():
    assert overall_verdict("pass", "pass", "pass") == "PASS"
