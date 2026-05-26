"""
tests/test_ccs_lite_backtest.py
=====================================================================
analytics/ccs_lite_backtest.py — R0 러너의 평가 로직 단위 테스트
(Supabase 네트워크 호출은 mock).
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pandas as pd

from analytics.ccs_lite_backtest import (
    R0Criterion,
    R0Report,
    R0_MAX_SINGLE_CONTRIB,
    R0_MIN_CROSS_SECTIONAL_POS,
    R0_MIN_EVENTS,
    R0_MIN_MEAN_4H,
    R0_MIN_SHARPE_4H,
    _btc_risk_off_active_at,
    _forward_returns,
    _resample_4h_close,
    evaluate_r0,
)


def _make_event_df(n: int, fwd_4h_mean: float, fwd_24h_mean: float,
                   symbols: List[str], seed: int = 0) -> pd.DataFrame:
    """N 이벤트 합성 df. 심볼은 round-robin 배치."""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(
        [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i) for i in range(n)]
    )
    fwd4 = rng.normal(fwd_4h_mean, 0.01, n)
    fwd24 = rng.normal(fwd_24h_mean, 0.02, n)
    sym_cycle = [symbols[i % len(symbols)] for i in range(n)]
    return pd.DataFrame({
        "symbol": sym_cycle, "fwd_4h": fwd4, "fwd_24h": fwd24,
        "btc_risk_off": False, "passed_gates": True, "state": "STRONG_TREND",
    }, index=idx)


# ── _resample_4h_close ───────────────────────────────────────────
def test_resample_4h_close_empty():
    out = _resample_4h_close(pd.DataFrame())
    assert out.empty


def test_resample_4h_close_basic():
    idx = pd.date_range("2026-01-01", periods=24, freq="1h", tz="UTC")
    ohlcv = pd.DataFrame({"close": np.arange(24, dtype=float)}, index=idx)
    out = _resample_4h_close(ohlcv)
    # 24봉 1h → 6봉 4h
    assert len(out) == 6
    # 마지막 4h 의 close = 1h 마지막 close = 23
    assert out.iloc[-1] == 23


# ── _btc_risk_off_active_at ──────────────────────────────────────
def test_btc_risk_off_active_when_drop_exceeds_threshold():
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    btc = pd.DataFrame({"close": [100, 99, 98, 96, 50]}, index=idx)
    # 96 → 50: -47.9% (≤ -1.2% 만족)
    assert _btc_risk_off_active_at(btc, idx[-1]) is True
    # 98 → 96: -2.04% (≤ -1.2%)
    assert _btc_risk_off_active_at(btc, idx[-2]) is True


def test_btc_risk_off_not_active_when_small_drop():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    btc = pd.DataFrame({"close": [100, 100.5, 99.9]}, index=idx)
    # 100.5 → 99.9: -0.6% (>−1.2% — 미달)
    assert _btc_risk_off_active_at(btc, idx[-1]) is False


def test_btc_risk_off_first_bar_returns_false():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    btc = pd.DataFrame({"close": [100, 99, 98]}, index=idx)
    assert _btc_risk_off_active_at(btc, idx[0]) is False


def test_btc_risk_off_unknown_ts_returns_false():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    btc = pd.DataFrame({"close": [100, 99, 98]}, index=idx)
    unknown = idx[0] + timedelta(hours=999)
    assert _btc_risk_off_active_at(btc, unknown) is False


# ── _forward_returns ─────────────────────────────────────────────
def test_forward_returns_lookahead_safe():
    """진입가는 이벤트 봉 i+1 시가, 청산은 i+h 종가 — 룩어헤드 차단 검증.

    h=4 면 entry=open[i+1], exit=close[i+4]. 즉 *이벤트 봉 이후* 데이터만 사용.
    """
    idx = pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")
    opens = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109], dtype=float)
    closes = opens + 0.5
    ohlcv = pd.DataFrame({"open": opens, "close": closes}, index=idx)
    # 이벤트 시점 i=2
    events = pd.DataFrame({"symbol": ["X"]}, index=[idx[2]])
    out = _forward_returns(events, ohlcv, horizons_bars=(4,))
    # entry = open[3] = 103, exit = close[6] = 106.5 → return = (106.5-103)/103
    expected = (106.5 - 103) / 103
    assert np.isclose(out["fwd_4h"].iloc[0], expected)


def test_forward_returns_insufficient_future_yields_nan():
    """이벤트 봉이 시리즈 끝쪽이라 i+h 범위 벗어나면 NaN."""
    idx = pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")
    ohlcv = pd.DataFrame({"open": np.ones(10), "close": np.ones(10)}, index=idx)
    events = pd.DataFrame({"symbol": ["X"]}, index=[idx[-1]])  # 마지막 봉
    out = _forward_returns(events, ohlcv, horizons_bars=(4,))
    assert pd.isna(out["fwd_4h"].iloc[0])


# ── evaluate_r0 ──────────────────────────────────────────────────
def test_evaluate_r0_too_few_events_fails_n_criterion():
    strong = _make_event_df(n=50, fwd_4h_mean=0.01, fwd_24h_mean=0.02,
                             symbols=["AUSDT", "BUSDT"])
    neutral = _make_event_df(n=200, fwd_4h_mean=0.0, fwd_24h_mean=0.0,
                              symbols=["AUSDT", "BUSDT"])
    ro = pd.DataFrame()
    crits = evaluate_r0(strong, neutral, ro)
    n_crit = next(c for c in crits if c.name == "n_strong>=200")
    assert n_crit.passed is False
    assert n_crit.value == 50


def test_evaluate_r0_pass_when_strong_clearly_better():
    """이상적 케이스 — 전 기준 통과."""
    strong = _make_event_df(n=400, fwd_4h_mean=0.015, fwd_24h_mean=0.03,
                             symbols=[f"S{i}USDT" for i in range(20)])
    neutral = _make_event_df(n=200, fwd_4h_mean=0.0, fwd_24h_mean=0.0,
                              symbols=[f"S{i}USDT" for i in range(20)])
    ro = _make_event_df(n=100, fwd_4h_mean=-0.005, fwd_24h_mean=-0.01,
                         symbols=[f"S{i}USDT" for i in range(20)])
    crits = evaluate_r0(strong, neutral, ro)
    by_name = {c.name: c for c in crits}
    assert by_name["n_strong>=200"].passed
    assert by_name["mean_4h>0.5%"].passed
    assert by_name["sharpe_4h>1.0"].passed
    assert by_name["monotonicity_4h_24h"].passed
    assert by_name["cross_sectional_pos>=60%"].passed
    assert by_name["single_symbol_contrib<30%"].passed
    assert by_name["btc_riskoff_subset<=0"].passed
    assert all(c.passed for c in crits)


def test_evaluate_r0_fails_on_low_mean():
    strong = _make_event_df(n=400, fwd_4h_mean=0.002, fwd_24h_mean=0.01,
                             symbols=[f"S{i}USDT" for i in range(20)])
    neutral = _make_event_df(n=200, fwd_4h_mean=0.0, fwd_24h_mean=0.0,
                              symbols=[f"S{i}USDT" for i in range(20)])
    ro = pd.DataFrame()
    crits = evaluate_r0(strong, neutral, ro)
    mean_crit = next(c for c in crits if c.name == "mean_4h>0.5%")
    assert mean_crit.passed is False


def test_evaluate_r0_concentration_blocks_when_single_symbol_dominates():
    """단일 종목이 누적 |return| 의 50% 차지 → 단일 종목 기여 기준 FAIL."""
    n = 400
    rng = np.random.default_rng(7)
    idx = pd.DatetimeIndex(
        [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i) for i in range(n)]
    )
    fwd4 = rng.normal(0.01, 0.005, n)
    # LAB 같은 한 종목이 큰 + 누적 만들도록 200건이 LAB, 200건이 분산
    syms = ["LAB" if i < 200 else f"S{i % 20}USDT" for i in range(n)]
    # LAB 의 fwd4 를 매우 크게 부풀려서 단일 기여 50% 이상으로 만듦
    fwd4[:200] = 0.05
    strong = pd.DataFrame({
        "symbol": syms, "fwd_4h": fwd4, "fwd_24h": fwd4 * 2,
        "btc_risk_off": False, "passed_gates": True, "state": "STRONG_TREND",
    }, index=idx)
    neutral = _make_event_df(n=200, fwd_4h_mean=0.0, fwd_24h_mean=0.0,
                              symbols=[f"S{i}USDT" for i in range(20)])
    crits = evaluate_r0(strong, neutral, pd.DataFrame())
    contrib_crit = next(c for c in crits if c.name == "single_symbol_contrib<30%")
    assert contrib_crit.passed is False


def test_evaluate_r0_btc_riskoff_positive_subset_fails_sanity():
    """BTC risk-off subset 의 평균이 양(+) 이면 sanity FAIL."""
    strong = _make_event_df(n=400, fwd_4h_mean=0.01, fwd_24h_mean=0.02,
                             symbols=[f"S{i}USDT" for i in range(20)])
    neutral = _make_event_df(n=200, fwd_4h_mean=0.0, fwd_24h_mean=0.0,
                              symbols=[f"S{i}USDT" for i in range(20)])
    ro_bad = _make_event_df(n=100, fwd_4h_mean=0.02, fwd_24h_mean=0.04,   # 양수
                             symbols=[f"S{i}USDT" for i in range(20)])
    crits = evaluate_r0(strong, neutral, ro_bad)
    ro_crit = next(c for c in crits if c.name == "btc_riskoff_subset<=0")
    assert ro_crit.passed is False


def test_evaluate_r0_handles_empty_neutral():
    strong = _make_event_df(n=400, fwd_4h_mean=0.01, fwd_24h_mean=0.02,
                             symbols=[f"S{i}USDT" for i in range(20)])
    crits = evaluate_r0(strong, pd.DataFrame(), pd.DataFrame())
    mono_crit = next(c for c in crits if c.name == "monotonicity_4h_24h")
    assert mono_crit.passed is False
    assert "NEUTRAL 표본 없음" in mono_crit.detail


# ── 상수 사전확정 검증 (다중검정 보정 반영) ─────────────────────
def test_constants_are_locked():
    """사전확정 임계가 계획서와 일치하는지 — 임의 변경 차단."""
    assert R0_MIN_EVENTS == 200
    assert R0_MIN_MEAN_4H == 0.005
    assert R0_MIN_SHARPE_4H == 1.0
    assert R0_MIN_CROSS_SECTIONAL_POS == 0.60
    assert R0_MAX_SINGLE_CONTRIB == 0.30
