"""
tests/test_pair_arb_backtest.py
=====================================================================
analytics/pair_arb_backtest.py — R0 평가 로직 단위 테스트 (Supabase 호출 mock).
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pandas as pd

from analytics.pair_arb_backtest import (
    PairR0Report,
    PairTrade,
    R0_MAX_MDD,
    R0_MAX_SINGLE_PAIR_CONTRIB,
    R0_MIN_CROSS_PAIR_POS_SHARE,
    R0_MIN_PF,
    R0_MIN_SHARPE,
    R0_MIN_TRADES,
    ROUND_TRIP_COST,
    _compute_mdd,
    _compute_pf,
    _compute_sharpe,
    _equity_curve,
    _simulate_pair,
    evaluate_r0,
)
from strategy.pair_selector import PairCandidate
from strategy.pair_stat_arb import PairArbConfig


def _make_trade(pair_key: str, side: str, net_return: float,
                exit_ts: datetime, sym_a: str = "A", sym_b: str = "B") -> PairTrade:
    return PairTrade(
        pair_key=pair_key, symbol_a=sym_a, symbol_b=sym_b, side=side,
        entry_ts=exit_ts - timedelta(hours=24), entry_price_a=100.0, entry_price_b=100.0,
        exit_ts=exit_ts, exit_price_a=100.0, exit_price_b=100.0,
        exit_reason="EXIT_NORMAL", gross_return=net_return + ROUND_TRIP_COST,
        net_return=net_return, beta=1.0, intercept=0.0,
    )


# ── 메트릭 ────────────────────────────────────────────────────────
def test_compute_pf_basic():
    rets = [0.02, -0.01, 0.03, -0.02]
    pf = _compute_pf(rets)
    # gains = 0.05, losses = 0.03 → 0.05/0.03
    assert abs(pf - (0.05 / 0.03)) < 1e-9


def test_compute_pf_no_losses_returns_inf():
    assert _compute_pf([0.01, 0.02]) == float("inf")


def test_compute_pf_no_gains_returns_zero():
    assert _compute_pf([-0.01, -0.02]) == 0.0


def test_compute_pf_empty():
    assert _compute_pf([]) == 0.0


def test_compute_sharpe_positive():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.001, 0.005, 100)
    sh = _compute_sharpe(rets)
    assert sh > 0  # 평균이 양수면 양수


def test_compute_sharpe_zero_variance():
    assert _compute_sharpe([0.01, 0.01, 0.01]) == 0.0


def test_compute_mdd_basic():
    eq = [1.0, 1.1, 1.05, 0.9, 1.2]
    # 1.1 → 0.9 → DD = 0.2/1.1 ≈ 0.1818
    mdd = _compute_mdd(eq)
    assert abs(mdd - (0.2 / 1.1)) < 1e-9


def test_compute_mdd_monotonic_up_zero():
    assert _compute_mdd([1.0, 1.1, 1.2, 1.5]) == 0.0


def test_equity_curve_compounds_returns():
    trades = [
        _make_trade("A|B", "LONG_A", 0.01, datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _make_trade("A|B", "LONG_A", -0.005, datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ]
    eq = _equity_curve(trades)
    assert len(eq) == 3
    assert eq[0] == 1.0
    assert abs(eq[1] - 1.01) < 1e-9
    assert abs(eq[2] - 1.01 * (1 - 0.005)) < 1e-9


# ── evaluate_r0 ───────────────────────────────────────────────────
def test_evaluate_r0_empty_trades_fails():
    crits, metrics = evaluate_r0([])
    by_name = {c.name: c for c in crits}
    assert not by_name["n_trades>=200"].passed
    assert by_name["n_trades>=200"].value == 0


def test_evaluate_r0_high_quality_passes():
    """이상적 케이스 — 모든 기준 통과."""
    trades = []
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(300):
        # 12개 페어 round-robin
        pair_idx = i % 12
        pair_key = f"P{pair_idx}|Q{pair_idx}"
        # 60% 승률, 평균 +0.008(0.8%) 수익, 40% 패배 평균 -0.004(0.4%)
        net = 0.008 if (i % 5) < 3 else -0.004
        trades.append(_make_trade(pair_key, "LONG_A", net,
                                   base_ts + timedelta(hours=i)))
    crits, metrics = evaluate_r0(trades)
    by_name = {c.name: c for c in crits}
    assert by_name["n_trades>=200"].passed
    assert by_name["pf>=1.3"].passed
    assert by_name["sharpe>1.0"].passed
    assert by_name["cross_pair_positive>=50%"].passed
    assert by_name["single_pair_contrib<25%"].passed


def test_evaluate_r0_concentration_fails_when_one_pair_dominates():
    """단일 페어가 누적 |return| 의 50% 차지 → 단일 페어 기여 FAIL."""
    trades = []
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 200개 거래 중 100 개가 "DOMINANT" 페어, 각 +0.05(5%) 누적
    for i in range(100):
        trades.append(_make_trade("DOMINANT|X", "LONG_A", 0.05,
                                   base_ts + timedelta(hours=i)))
    # 나머지 100 개를 10 개 페어에 분산, 각 +0.005
    for i in range(100):
        trades.append(_make_trade(f"P{i%10}|Q{i%10}", "LONG_A", 0.005,
                                   base_ts + timedelta(hours=100+i)))
    crits, metrics = evaluate_r0(trades)
    by_name = {c.name: c for c in crits}
    assert not by_name["single_pair_contrib<25%"].passed


def test_evaluate_r0_low_pf_fails():
    """PF < 1.3 → FAIL."""
    trades = []
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(300):
        # 평균 거의 0 → PF ≈ 1
        net = 0.001 if i % 2 == 0 else -0.001
        trades.append(_make_trade(f"P{i%12}|Q{i%12}", "LONG_A", net,
                                   base_ts + timedelta(hours=i)))
    crits, metrics = evaluate_r0(trades)
    by_name = {c.name: c for c in crits}
    assert not by_name["pf>=1.3"].passed


# ── _simulate_pair ────────────────────────────────────────────────
def _make_aligned_ohlcv(n: int = 2000, lam: float = 0.95,
                          shock_std: float = 0.01,
                          b_noise: float = 0.001,
                          seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """합성 ohlcv: B = random walk(small noise), A = B + AR(1) mean-reverting spread.

    spread[t] = lam · spread[t-1] + N(0, shock_std). lam<1 → mean-reverting.
    AR(1) 의 stationary std = shock_std / sqrt(1 - lam²). z 가 자연스럽게 ±3 범위.
    """
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex([start + timedelta(hours=i) for i in range(n)])
    b_log = np.cumsum(rng.normal(0, b_noise, n)) + np.log(100.0)
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = lam * spread[t-1] + rng.normal(0, shock_std)
    a_log = b_log + spread
    df_a = pd.DataFrame({"open": np.exp(a_log), "close": np.exp(a_log)}, index=idx)
    df_b = pd.DataFrame({"open": np.exp(b_log), "close": np.exp(b_log)}, index=idx)
    return df_a, df_b


def test_simulate_pair_produces_trades_on_oscillating_spread():
    """AR(1) mean-reverting spread → z 가 자연스럽게 ±2 cross → 다수 trade."""
    df_a, df_b = _make_aligned_ohlcv(n=3000, lam=0.95, shock_std=0.01, seed=1)
    candidate = PairCandidate(
        symbol_a="A", symbol_b="B", beta=1.0, intercept=0.0,
        adf_pvalue=0.001, hurst=0.3, half_life_hours=14, n_obs=3000,
    )
    cfg = PairArbConfig(lookback_days=10)
    trades = _simulate_pair(candidate, df_a, df_b, start_idx=300, cfg=cfg,
                             z_lookback_bars=240)
    assert len(trades) >= 3, f"expected >=3 trades, got {len(trades)}"
    # 모든 trade 가 close 되어야 함 (OPEN 상태 잔존 X)
    for t in trades:
        assert t.exit_reason != "OPEN"
        assert t.entry_price_a > 0
        assert t.exit_price_a > 0


def test_simulate_pair_high_entry_threshold_no_trades():
    """entry_z 매우 높임 → z 가 도달 못해 진입 0."""
    df_a, df_b = _make_aligned_ohlcv(n=2000, lam=0.95, shock_std=0.01, seed=11)
    candidate = PairCandidate(
        symbol_a="A", symbol_b="B", beta=1.0, intercept=0.0,
        adf_pvalue=0.001, hurst=0.3, half_life_hours=14, n_obs=2000,
    )
    cfg = PairArbConfig(lookback_days=10, entry_z=20.0)   # 진입 임계 매우 높임
    trades = _simulate_pair(candidate, df_a, df_b, start_idx=300, cfg=cfg,
                             z_lookback_bars=240)
    assert len(trades) == 0


def test_simulate_pair_pnl_positive_for_clean_mean_reverting_spread():
    """AR(1) 강한 mean-reverting spread + 작은 b noise → 누적 gross PnL 양수.

    lam=0.95, shock_std=0.01 → stationary spread std ≈ 0.032. half-life = -ln2/ln0.95 ≈ 13.5 봉.
    time_stop(168봉) 안에서 충분히 revert. b_noise 0.0005 라 noise leg 영향 최소.
    """
    df_a, df_b = _make_aligned_ohlcv(n=3000, lam=0.95, shock_std=0.01,
                                       b_noise=0.0005, seed=42)
    candidate = PairCandidate(
        symbol_a="A", symbol_b="B", beta=1.0, intercept=0.0,
        adf_pvalue=0.001, hurst=0.3, half_life_hours=14, n_obs=3000,
    )
    cfg = PairArbConfig(lookback_days=10)
    trades = _simulate_pair(candidate, df_a, df_b, start_idx=300, cfg=cfg,
                             z_lookback_bars=240)
    assert len(trades) > 0
    grosses = [t.gross_return for t in trades]
    # 합계 gross 가 양수여야 함(깨끗한 mean-reversion 활용)
    assert sum(grosses) > 0, f"sum grosses = {sum(grosses):.4f}, n={len(trades)}, sample={grosses[:5]}"


def test_round_trip_cost_value():
    """비용 모델이 plan 과 일치(round-trip ~0.33%)."""
    assert abs(ROUND_TRIP_COST - (2 * (0.00018 + 0.00045 + 0.00100))) < 1e-9
    assert abs(ROUND_TRIP_COST - 0.00326) < 1e-5


def test_constants_are_locked():
    """사전확정 임계가 plan 과 일치."""
    assert R0_MIN_TRADES == 200
    assert R0_MIN_PF == 1.3
    assert R0_MIN_SHARPE == 1.0
    assert R0_MAX_MDD == 0.12
    assert R0_MIN_CROSS_PAIR_POS_SHARE == 0.50
    assert R0_MAX_SINGLE_PAIR_CONTRIB == 0.25
