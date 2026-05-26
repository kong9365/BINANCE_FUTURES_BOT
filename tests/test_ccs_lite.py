"""
tests/test_ccs_lite.py
=====================================================================
strategy/ccs_lite.py — 8 kill gates + 3-state 분류기 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from strategy.ccs_lite import (
    CCSLiteConfig,
    GATE_BTC_RISK_OFF,
    GATE_BTC_TREND,
    GATE_FUNDING_EXTREME,
    GATE_FUNDING_WINDOW,
    GATE_LOSS_COOLDOWN,
    GATE_MACRO,
    GATE_SECTOR,
    GATE_SYMBOL_TREND,
    STATE_NEUTRAL,
    STATE_STRONG_SQUEEZE,
    STATE_STRONG_TREND,
    classify_state,
    evaluate_kill_gates,
    gate_btc_risk_off,
    gate_btc_trend,
    gate_funding_extreme,
    gate_funding_window,
    gate_loss_cooldown,
    gate_macro_proximity,
    gate_sector_exclusion,
    gate_symbol_trend,
)


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
_CFG = CCSLiteConfig()


# ── 개별 게이트 ────────────────────────────────────────────────────
def test_gate_btc_risk_off_passthrough():
    assert gate_btc_risk_off(True) is True
    assert gate_btc_risk_off(False) is False


def test_gate_btc_trend_close_below_ema_blocks():
    closes = [100.0] * 200 + [50.0]
    assert gate_btc_trend(closes, _CFG) is True


def test_gate_btc_trend_close_above_ema_passes():
    closes = list(range(1, 202))   # 단조 증가 → 마지막이 EMA 위
    assert gate_btc_trend(closes, _CFG) is False


def test_gate_btc_trend_empty_blocks_conservatively():
    assert gate_btc_trend([], _CFG) is True
    assert gate_btc_trend([100.0] * 50, _CFG) is True   # EMA200 계산 불가


def test_gate_symbol_trend_same_logic():
    closes = list(range(1, 202))
    assert gate_symbol_trend(closes, _CFG) is False
    closes_down = [200.0] * 200 + [50.0]
    assert gate_symbol_trend(closes_down, _CFG) is True


def test_gate_macro_proximity_within_window_blocks():
    events = [_NOW + timedelta(hours=10)]
    assert gate_macro_proximity(_NOW, events, _CFG) is True


def test_gate_macro_proximity_outside_window_passes():
    events = [_NOW + timedelta(hours=48)]
    assert gate_macro_proximity(_NOW, events, _CFG) is False


def test_gate_macro_proximity_past_window_blocks():
    events = [_NOW - timedelta(hours=10)]
    assert gate_macro_proximity(_NOW, events, _CFG) is True


def test_gate_macro_proximity_empty_passes():
    assert gate_macro_proximity(_NOW, [], _CFG) is False


def test_gate_funding_extreme_z_below_threshold_passes():
    history = [0.0001] * 30   # 안정적 펀딩
    assert gate_funding_extreme(history, current_funding=0.0001, cfg=_CFG) is False


def test_gate_funding_extreme_z_above_threshold_blocks():
    history = [0.0001] * 30
    # 극단값 — 비정상적으로 높음
    assert gate_funding_extreme(history, current_funding=0.01, cfg=_CFG) is True


def test_gate_funding_extreme_reversion_passes():
    """극단값이지만 회귀 중(현재 절댓값 < 직전 절댓값) → 차단 안 함."""
    history = [0.0001] * 29 + [0.01]   # 직전이 극단
    assert gate_funding_extreme(history, current_funding=0.005, cfg=_CFG) is False


def test_gate_funding_extreme_few_samples_passes():
    """샘플 < 5 → 차단 안 함(데이터 부족 시 보수적 false-pass)."""
    assert gate_funding_extreme([0.001], current_funding=0.5, cfg=_CFG) is False


def test_gate_loss_cooldown_passthrough():
    assert gate_loss_cooldown(True) is True
    assert gate_loss_cooldown(False) is False


def test_gate_sector_exclusion_blocks_same_meme():
    assert gate_sector_exclusion("1000PEPEUSDT", ["DOGEUSDT"]) is True


def test_gate_sector_exclusion_allows_different_sectors():
    assert gate_sector_exclusion("ARBUSDT", ["DOGEUSDT"]) is False


def test_gate_sector_exclusion_empty_held():
    assert gate_sector_exclusion("1000PEPEUSDT", []) is False


def test_gate_funding_window_within_blocks():
    next_funding = _NOW + timedelta(minutes=15)
    assert gate_funding_window(_NOW, next_funding, _CFG) is True


def test_gate_funding_window_outside_passes():
    next_funding = _NOW + timedelta(minutes=60)
    assert gate_funding_window(_NOW, next_funding, _CFG) is False


def test_gate_funding_window_none_passes():
    assert gate_funding_window(_NOW, None, _CFG) is False


def test_gate_funding_window_past_passes():
    # 이미 지난 펀딩(음수 delta) — 다음 펀딩이 아니므로 차단 안 함
    next_funding = _NOW - timedelta(minutes=10)
    assert gate_funding_window(_NOW, next_funding, _CFG) is False


# ── 8 게이트 동시 평가 ────────────────────────────────────────────
def _make_passing_kwargs(symbol: str = "ARBUSDT") -> dict:
    """모든 게이트 통과하는 기본 입력. 개별 테스트는 일부만 override."""
    closes_up = list(range(1, 220))
    return {
        "btc_risk_off_active": False,
        "btc_closes_4h": closes_up,
        "symbol_closes_1h": closes_up,
        "now": _NOW,
        "upcoming_macro_events": [],
        "funding_history": [0.0001] * 30,
        "current_funding": 0.0001,
        "loss_cooldown_active": False,
        "candidate_symbol": symbol,
        "held_symbols": [],
        "next_funding_at": _NOW + timedelta(hours=4),
        "cfg": _CFG,
    }


def test_all_gates_pass():
    res = evaluate_kill_gates(**_make_passing_kwargs())
    assert res.blocked is False
    assert res.active_gates == []


def test_single_gate_blocks_btc_risk_off():
    kw = _make_passing_kwargs()
    kw["btc_risk_off_active"] = True
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_BTC_RISK_OFF in res.active_gates


def test_multiple_gates_block_macro_and_sector():
    kw = _make_passing_kwargs(symbol="1000PEPEUSDT")
    kw["upcoming_macro_events"] = [_NOW + timedelta(hours=10)]
    kw["held_symbols"] = ["DOGEUSDT"]
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_MACRO in res.active_gates
    assert GATE_SECTOR in res.active_gates


def test_btc_trend_kill_blocks():
    kw = _make_passing_kwargs()
    kw["btc_closes_4h"] = [200.0] * 200 + [50.0]
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_BTC_TREND in res.active_gates


def test_symbol_trend_kill_blocks():
    kw = _make_passing_kwargs()
    kw["symbol_closes_1h"] = [200.0] * 200 + [50.0]
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_SYMBOL_TREND in res.active_gates


def test_funding_extreme_blocks():
    kw = _make_passing_kwargs()
    kw["funding_history"] = [0.0001] * 30
    kw["current_funding"] = 0.05
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_FUNDING_EXTREME in res.active_gates


def test_loss_cooldown_blocks():
    kw = _make_passing_kwargs()
    kw["loss_cooldown_active"] = True
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_LOSS_COOLDOWN in res.active_gates


def test_funding_window_blocks():
    kw = _make_passing_kwargs()
    kw["next_funding_at"] = _NOW + timedelta(minutes=10)
    res = evaluate_kill_gates(**kw)
    assert res.blocked is True
    assert GATE_FUNDING_WINDOW in res.active_gates


# ── 3-State 분류 ──────────────────────────────────────────────────
def test_classify_strong_trend():
    closes_up = list(range(1, 220))
    s = classify_state(
        btc_closes_4h=closes_up,
        symbol_closes_1h=closes_up,
        prev_funding=0.0001,
        current_funding=0.0001,
        recent_oi_change_pct=0.0,
        cfg=_CFG,
    )
    assert s == STATE_STRONG_TREND


def test_classify_strong_squeeze():
    closes_down = [200.0] * 200 + [50.0]
    s = classify_state(
        btc_closes_4h=closes_down,
        symbol_closes_1h=closes_down,
        prev_funding=-0.001,   # 음수
        current_funding=0.0,    # 0 으로 회귀
        recent_oi_change_pct=-0.07,   # 강한 OI 감소
        cfg=_CFG,
    )
    assert s == STATE_STRONG_SQUEEZE


def test_classify_neutral_default():
    closes_down = [200.0] * 200 + [50.0]
    s = classify_state(
        btc_closes_4h=closes_down,
        symbol_closes_1h=closes_down,
        prev_funding=0.0001,
        current_funding=0.0001,
        recent_oi_change_pct=-0.01,
        cfg=_CFG,
    )
    assert s == STATE_NEUTRAL


def test_classify_partial_squeeze_funding_only_neutral():
    """funding 회귀만 있고 OI 가속 없으면 NEUTRAL."""
    closes_down = [200.0] * 200 + [50.0]
    s = classify_state(
        btc_closes_4h=closes_down,
        symbol_closes_1h=closes_down,
        prev_funding=-0.001,
        current_funding=0.0,
        recent_oi_change_pct=-0.01,   # 약함
        cfg=_CFG,
    )
    assert s == STATE_NEUTRAL


def test_classify_strong_trend_takes_priority_over_squeeze():
    """양쪽 모두 매칭 가능한 경우 STRONG_TREND 가 우선."""
    closes_up = list(range(1, 220))
    s = classify_state(
        btc_closes_4h=closes_up,
        symbol_closes_1h=closes_up,
        prev_funding=-0.001,
        current_funding=0.0,
        recent_oi_change_pct=-0.07,
        cfg=_CFG,
    )
    assert s == STATE_STRONG_TREND
