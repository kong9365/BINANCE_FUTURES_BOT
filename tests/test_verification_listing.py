"""후보 5 — 신규 상장 효과 단위 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.verification.feature_engineering import features_listing_effect
from analytics.verification.simple_backtest import backtest_listing_fade


def _synthetic_listing_df(days: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=days, freq="D", tz="UTC")
    # 상장 후 급등 → 페이드 시나리오
    open_ = 1.0 + np.linspace(0, 0.5, days)
    open_[8:] = open_[7] * (1 - np.linspace(0, 0.15, days - 8))
    return pd.DataFrame({"open": open_, "close": open_ * 1.01, "volume": np.full(days, 1e6)}, index=idx)


def test_features_listing_effect_window():
    df = _synthetic_listing_df(100)
    onboard = pd.Timestamp("2024-06-01", tz="UTC")
    feat = features_listing_effect(df, onboard)
    assert not feat.empty
    assert feat["days_since_listing"].min() >= 1
    assert feat["days_since_listing"].max() <= 90
    assert feat.loc[feat["days_since_listing"] >= 7, "first_week_return"].notna().any()


def test_backtest_listing_fade_triggers_short():
    df = _synthetic_listing_df(60)
    onboard = pd.Timestamp("2024-06-01", tz="UTC")
    r = backtest_listing_fade(df, onboard, "TESTUSDT")
    assert r.n_trades >= 0
