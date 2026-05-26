"""
tests/test_pair_selector.py
=====================================================================
strategy/pair_selector.py — 공적분 스캐너 단위 테스트.

합성 데이터:
  - 진짜 cointegrated 페어: B = α + β·A + ε(stationary) → spread mean-reverting
  - 진짜 비공적분 페어: 독립 random walk 둘 → spread 자체가 random walk
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from strategy.pair_selector import (
    PairCandidate,
    PairSelectorConfig,
    adf_pvalue,
    compute_spread,
    evaluate_pair,
    half_life,
    hurst_exponent,
    select_pairs,
)


def _synthetic_cointegrated(n: int = 2000, beta: float = 1.5,
                              intercept: float = 0.5, noise_std: float = 0.01,
                              seed: int = 42) -> tuple[pd.Series, pd.Series]:
    """A = random walk, B = α + β·A + AR(1) stationary noise → cointegrated."""
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    a_steps = rng.normal(0, 0.005, n)
    a_log = np.cumsum(a_steps) + np.log(100.0)
    # stationary AR(1) noise (lambda=0.7)
    eps = np.zeros(n)
    for t in range(1, n):
        eps[t] = 0.7 * eps[t-1] + rng.normal(0, noise_std)
    b_log = intercept + beta * a_log + eps
    a_price = np.exp(a_log)
    b_price = np.exp(b_log)
    return (
        pd.Series(a_price, index=idx, name="A"),
        pd.Series(b_price, index=idx, name="B"),
    )


def _synthetic_independent_rw(n: int = 2000, seed: int = 7) -> tuple[pd.Series, pd.Series]:
    """독립 두 random walk — cointegrated 아님."""
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    a_log = np.cumsum(rng.normal(0, 0.005, n)) + np.log(100.0)
    b_log = np.cumsum(rng.normal(0, 0.005, n)) + np.log(80.0)
    return (
        pd.Series(np.exp(a_log), index=idx, name="A"),
        pd.Series(np.exp(b_log), index=idx, name="B"),
    )


# ── compute_spread ────────────────────────────────────────────────
def test_compute_spread_recovers_known_beta():
    rng = np.random.default_rng(0)
    n = 1000
    x = rng.normal(0, 1, n)
    true_beta = 1.5
    true_intercept = 0.5
    y = true_intercept + true_beta * x + rng.normal(0, 0.01, n)
    beta, intercept, residuals = compute_spread(y, x)
    assert abs(beta - true_beta) < 0.01
    assert abs(intercept - true_intercept) < 0.01
    assert len(residuals) == n


def test_compute_spread_mismatched_shape_raises():
    import pytest
    with pytest.raises(ValueError):
        compute_spread(np.zeros(5), np.zeros(6))


def test_compute_spread_nan_input_raises():
    import pytest
    bad = np.array([1.0, np.nan, 3.0])
    with pytest.raises(ValueError):
        compute_spread(bad, np.array([1.0, 2.0, 3.0]))


def test_compute_spread_zero_variance_raises():
    import pytest
    with pytest.raises(ValueError):
        compute_spread(np.array([1.0, 2.0, 3.0]), np.array([5.0, 5.0, 5.0]))


# ── adf_pvalue ────────────────────────────────────────────────────
def test_adf_pvalue_stationary_low_p():
    """AR(1) with lambda 0.5 → 강한 stationary → p < 0.01 기대."""
    rng = np.random.default_rng(1)
    n = 1000
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.5 * x[t-1] + rng.normal(0, 1)
    p = adf_pvalue(x)
    assert p < 0.01


def test_adf_pvalue_random_walk_high_p():
    """순수 random walk → p 대체로 높음(>0.1)."""
    rng = np.random.default_rng(2)
    rw = np.cumsum(rng.normal(0, 1, 500))
    p = adf_pvalue(rw)
    assert p > 0.10


def test_adf_pvalue_short_series_returns_one():
    assert adf_pvalue(np.zeros(10)) == 1.0


# ── hurst_exponent ───────────────────────────────────────────────
def test_hurst_random_walk_around_05():
    """random walk → H ≈ 0.5 (±0.15)."""
    rng = np.random.default_rng(3)
    rw = np.cumsum(rng.normal(0, 1, 2000))
    h = hurst_exponent(rw)
    assert 0.35 < h < 0.65


def test_hurst_mean_reverting_below_05():
    """AR(1) lambda=0.3 (mean-reverting strong) → H < 0.5."""
    rng = np.random.default_rng(4)
    n = 2000
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.3 * x[t-1] + rng.normal(0, 1)
    h = hurst_exponent(x)
    assert h < 0.5


def test_hurst_short_series_fallback():
    assert hurst_exponent(np.zeros(10)) == 0.5


# ── half_life ────────────────────────────────────────────────────
def test_half_life_strong_reversion():
    """lambda=0.5 → half life = -ln(2)/ln(0.5) = 1 봉."""
    rng = np.random.default_rng(5)
    n = 2000
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.5 * x[t-1] + rng.normal(0, 1)
    hl = half_life(x)
    assert 0.5 < hl < 1.5   # 노이즈 허용


def test_half_life_random_walk_is_long_or_inf():
    """random walk → half_life 가 inf 이거나 매우 큰 값(>50봉).

    노트: 유한 표본의 OLS λ 는 random walk 에서도 1 보다 약간 작게 추정되어
    half_life 가 finite 일 수 있다(finite-sample bias). 페어 선정에서 이
    노이즈는 ADF p-value 게이트가 막아주므로 hl 자체는 *충분히 길면* OK.
    """
    rng = np.random.default_rng(6)
    rw = np.cumsum(rng.normal(0, 1, 1000))
    hl = half_life(rw)
    assert hl == float("inf") or hl > 50


def test_half_life_short_series_returns_inf():
    assert half_life(np.zeros(10)) == float("inf")


# ── evaluate_pair ─────────────────────────────────────────────────
def test_evaluate_pair_cointegrated_returns_candidate():
    """합성 cointegrated 페어 → PairCandidate 반환."""
    a, b = _synthetic_cointegrated(n=2500, beta=1.5, intercept=0.5, noise_std=0.005)
    cand = evaluate_pair(a, b)
    assert cand is not None
    assert isinstance(cand, PairCandidate)
    assert cand.symbol_a == "A"
    assert cand.symbol_b == "B"
    # OLS β 는 swapped 일 수 있음(우리 코드는 log_a = β·log_b — 둘 다 거의 cointegrated 라 어느 쪽이든 OK)
    assert cand.adf_pvalue < 0.01
    assert cand.hurst < 0.4
    assert cand.half_life_hours > 0
    assert cand.n_obs > 1000


def test_evaluate_pair_independent_returns_none():
    """독립 random walk → 자격 미달 → None."""
    a, b = _synthetic_independent_rw(n=2000)
    cand = evaluate_pair(a, b)
    assert cand is None


def test_evaluate_pair_insufficient_overlap_returns_none():
    """min_overlap_bars 미달 → None."""
    a, b = _synthetic_cointegrated(n=100)
    cand = evaluate_pair(a, b)
    assert cand is None


def test_evaluate_pair_negative_prices_returns_none():
    """가격에 0 이나 음수 있으면 log 불가 → None."""
    a, b = _synthetic_cointegrated(n=2000)
    a.iloc[100] = -5.0
    cand = evaluate_pair(a, b)
    assert cand is None


def test_evaluate_pair_beta_out_of_range_returns_none():
    """β 가 [0.3, 3.0] 밖 → None. β 가 매우 작도록 합성."""
    a, b = _synthetic_cointegrated(n=2500, beta=0.10, intercept=0.0, noise_std=0.005)
    cand = evaluate_pair(a, b)
    assert cand is None


# ── select_pairs ──────────────────────────────────────────────────
def test_select_pairs_finds_cointegrated_among_random():
    """3개 시리즈 중 (A,B) 만 cointegrated, C 는 독립 → 1 페어만 발견."""
    a, b = _synthetic_cointegrated(n=2500, beta=1.5, intercept=0.5, noise_std=0.005)
    _, c = _synthetic_independent_rw(n=2500, seed=99)
    c = c.rename("C")
    df_a = pd.DataFrame({"close": a.values}, index=a.index)
    df_b = pd.DataFrame({"close": b.values}, index=b.index)
    df_c = pd.DataFrame({"close": c.values}, index=c.index)
    result = select_pairs({"A": df_a, "B": df_b, "C": df_c})
    # (A,B) 만 자격, (A,C) 와 (B,C) 는 미달
    assert len(result) == 1
    pair = result[0]
    assert {pair.symbol_a, pair.symbol_b} == {"A", "B"}


def test_select_pairs_empty_input():
    assert select_pairs({}) == []


def test_select_pairs_single_symbol():
    """1 심볼만 있으면 페어 형성 불가 → empty."""
    a, _ = _synthetic_cointegrated(n=500)
    df = pd.DataFrame({"close": a.values}, index=a.index)
    assert select_pairs({"A": df}) == []
