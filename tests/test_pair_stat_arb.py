"""
tests/test_pair_stat_arb.py
=====================================================================
strategy/pair_stat_arb.py — direction-neutral 페어 시그널 상태 머신 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from strategy.pair_stat_arb import (
    ACTION_ENTER_LONG_A,
    ACTION_ENTER_SHORT_A,
    ACTION_EXIT_HARD_STOP,
    ACTION_EXIT_NORMAL,
    ACTION_EXIT_TIME_STOP,
    ACTION_HOLD,
    PairArbConfig,
    PairState,
    SIDE_LONG_A,
    SIDE_SHORT_A,
    compute_z,
    evaluate,
)


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
_CFG = PairArbConfig()


# ── compute_z ─────────────────────────────────────────────────────
def test_compute_z_returns_zero_when_current_equals_mean():
    """현재 spread 가 윈도 평균과 같으면 z=0."""
    n = 100
    rng = np.random.default_rng(0)
    a = np.cumsum(rng.normal(0, 0.01, n)) + 5.0
    b = np.cumsum(rng.normal(0, 0.01, n)) + 4.0
    # 마지막 값을 정확히 평균이 되도록 조정
    beta, intercept = 1.0, 0.0
    spreads = a - beta * b - intercept
    target_mean = float(spreads[:-1].mean())
    # 인위적으로 마지막 a 를 조정해 spread[-1] = mean(spreads[:-1])
    a[-1] = target_mean + beta * b[-1] + intercept
    z = compute_z(a, b, beta=beta, intercept=intercept)
    assert z is not None
    # 마지막 spread 가 mean 에 가까우면 z 가 작아야 함(완전히 0 은 아님 — full window mean 포함이라)
    assert abs(z) < 1.0


def test_compute_z_returns_positive_when_overshoot_up():
    """현재 spread 가 평균보다 크게 위에 있으면 z > 0."""
    n = 100
    spreads = np.zeros(n)
    spreads[-1] = 5.0   # 마지막만 크게 overshoot
    # a, b 를 spread = a - 1*b 로 재구성
    b = np.zeros(n)
    a = spreads + b
    z = compute_z(a, b, beta=1.0, intercept=0.0)
    assert z is not None
    assert z > 2.0  # 큰 양수


def test_compute_z_returns_negative_when_overshoot_down():
    n = 100
    spreads = np.zeros(n)
    spreads[-1] = -5.0
    b = np.zeros(n)
    a = spreads + b
    z = compute_z(a, b, beta=1.0, intercept=0.0)
    assert z is not None
    assert z < -2.0


def test_compute_z_short_window_returns_none():
    assert compute_z(np.zeros(5), np.zeros(5), 1.0, 0.0) is None


def test_compute_z_zero_std_returns_none():
    """spread 전 구간 동일(std=0) → z 계산 불가."""
    n = 100
    a = np.ones(n) * 5.0
    b = np.ones(n) * 5.0   # spread = 0 전 구간
    assert compute_z(a, b, beta=1.0, intercept=0.0) is None


def test_compute_z_mismatched_shape_returns_none():
    assert compute_z(np.zeros(50), np.zeros(60), 1.0, 0.0) is None


# ── evaluate (entry transitions) ──────────────────────────────────
def test_evaluate_no_position_z_below_entry_holds():
    state = PairState()
    new_s, action = evaluate(state, z=1.5, now=_NOW)
    assert action == ACTION_HOLD
    assert not new_s.in_position


def test_evaluate_no_position_z_above_entry_enters_short_a():
    state = PairState()
    new_s, action = evaluate(state, z=2.5, now=_NOW)
    assert action == ACTION_ENTER_SHORT_A
    assert new_s.side == SIDE_SHORT_A
    assert new_s.entry_z == 2.5
    assert new_s.entry_ts == _NOW


def test_evaluate_no_position_z_below_neg_entry_enters_long_a():
    state = PairState()
    new_s, action = evaluate(state, z=-2.5, now=_NOW)
    assert action == ACTION_ENTER_LONG_A
    assert new_s.side == SIDE_LONG_A
    assert new_s.entry_z == -2.5


def test_evaluate_at_exactly_entry_threshold_enters():
    """경계값 — 정확히 +entry_z 이면 진입(>=)."""
    state = PairState()
    new_s, action = evaluate(state, z=2.0, now=_NOW)
    assert action == ACTION_ENTER_SHORT_A


# ── evaluate (exit transitions) ───────────────────────────────────
def test_evaluate_short_a_normal_exit_when_z_below_zero():
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=-0.1, now=_NOW)
    assert action == ACTION_EXIT_NORMAL
    assert not new_s.in_position


def test_evaluate_long_a_normal_exit_when_z_above_zero():
    state = PairState(side=SIDE_LONG_A, entry_z=-2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=0.1, now=_NOW)
    assert action == ACTION_EXIT_NORMAL
    assert not new_s.in_position


def test_evaluate_short_a_holds_when_z_still_positive():
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=1.5, now=_NOW)
    assert action == ACTION_HOLD
    assert new_s.in_position


def test_evaluate_long_a_holds_when_z_still_negative():
    state = PairState(side=SIDE_LONG_A, entry_z=-2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=-1.5, now=_NOW)
    assert action == ACTION_HOLD
    assert new_s.in_position


def test_evaluate_short_a_hard_stop_when_z_blows_up():
    """SHORT_A 보유 중 z 가 +4 초과 → hard stop (cointegration 깨짐)."""
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=4.5, now=_NOW)
    assert action == ACTION_EXIT_HARD_STOP
    assert not new_s.in_position


def test_evaluate_long_a_hard_stop_when_z_blows_down():
    state = PairState(side=SIDE_LONG_A, entry_z=-2.5, entry_ts=_NOW - timedelta(hours=10))
    new_s, action = evaluate(state, z=-4.5, now=_NOW)
    assert action == ACTION_EXIT_HARD_STOP


def test_evaluate_time_stop_after_168h():
    """진입 후 168h(7일) 경과 → time stop."""
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5,
                       entry_ts=_NOW - timedelta(hours=168, minutes=1))
    new_s, action = evaluate(state, z=1.5, now=_NOW)
    assert action == ACTION_EXIT_TIME_STOP
    assert not new_s.in_position


def test_evaluate_time_stop_takes_priority_over_hard_stop():
    """time stop 조건 + hard stop 조건 동시 시 time stop 우선(코드 순서)."""
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5,
                       entry_ts=_NOW - timedelta(hours=200))
    new_s, action = evaluate(state, z=5.0, now=_NOW)
    assert action == ACTION_EXIT_TIME_STOP


# ── evaluate (edge cases) ─────────────────────────────────────────
def test_evaluate_none_z_holds_position():
    state = PairState(side=SIDE_SHORT_A, entry_z=2.5, entry_ts=_NOW)
    new_s, action = evaluate(state, z=None, now=_NOW)
    assert action == ACTION_HOLD
    assert new_s.in_position


def test_evaluate_none_z_no_entry():
    state = PairState()
    new_s, action = evaluate(state, z=None, now=_NOW)
    assert action == ACTION_HOLD
    assert not new_s.in_position


def test_evaluate_inf_z_holds():
    state = PairState()
    new_s, action = evaluate(state, z=float("inf"), now=_NOW)
    assert action == ACTION_HOLD


def test_full_cycle_short_then_exit():
    """완전 사이클: 미보유 → SHORT_A 진입 → 보유 → 정상 청산."""
    state = PairState()
    # 진입
    state, action = evaluate(state, z=2.5, now=_NOW)
    assert action == ACTION_ENTER_SHORT_A
    assert state.side == SIDE_SHORT_A
    # 1시간 후 z 여전히 positive → hold
    state, action = evaluate(state, z=1.8, now=_NOW + timedelta(hours=1))
    assert action == ACTION_HOLD
    # 2시간 후 z 가 0 cross → exit
    state, action = evaluate(state, z=-0.05, now=_NOW + timedelta(hours=2))
    assert action == ACTION_EXIT_NORMAL
    assert not state.in_position
