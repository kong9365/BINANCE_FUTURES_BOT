"""
tests/test_btc_risk_off.py
=====================================================================
strategy/btc_risk_off.py — BTC 급락 자동 HALT 안전 필터 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from strategy.btc_risk_off import (
    BTCRiskOffConfig, BTCRiskOffState, evaluate, is_halted,
)


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
_CFG = BTCRiskOffConfig(enabled=True, drop_threshold_pct=0.012, cooldown_hours=6.0)


def test_no_drop_no_trigger():
    state = BTCRiskOffState()
    s, ev = evaluate(100.0, 100.0, _NOW, state, _CFG)
    assert ev is None
    assert s.halted_until is None
    assert not is_halted(s, _NOW)


def test_small_drop_below_threshold_no_trigger():
    s, ev = evaluate(99.0, 100.0, _NOW, BTCRiskOffState(), _CFG)   # -1% < 1.2%
    assert ev is None and s.halted_until is None


def test_large_drop_triggers_halt():
    s, ev = evaluate(98.5, 100.0, _NOW, BTCRiskOffState(), _CFG)   # -1.5%
    assert ev is not None
    assert ev.drop_pct < -0.012
    assert s.halted_until == _NOW + timedelta(hours=6)
    assert is_halted(s, _NOW)
    assert is_halted(s, _NOW + timedelta(hours=5, minutes=59))


def test_no_duplicate_trigger_during_cooldown():
    """이미 halted 상태면 추가 급락에도 새 트리거 발동 안 함(이벤트 폭주 방지)."""
    s = BTCRiskOffState(halted_until=_NOW + timedelta(hours=3),
                         last_trigger_drop_pct=-0.02,
                         last_trigger_at=_NOW - timedelta(hours=1))
    _, ev = evaluate(95.0, 100.0, _NOW, s, _CFG)
    assert ev is None
    # 여전히 halted


def test_auto_clear_after_cooldown_expiry():
    expired = _NOW - timedelta(minutes=1)
    s = BTCRiskOffState(halted_until=expired)
    new_s, ev = evaluate(100.0, 100.0, _NOW, s, _CFG)
    assert new_s.halted_until is None
    assert not is_halted(new_s, _NOW)


def test_re_trigger_after_cooldown_clears():
    expired = _NOW - timedelta(minutes=1)
    s = BTCRiskOffState(halted_until=expired)
    # 만료 후 새 급락 발생 → 다시 트리거
    new_s, ev = evaluate(98.0, 100.0, _NOW, s, _CFG)
    assert ev is not None
    assert new_s.halted_until == _NOW + timedelta(hours=6)


def test_disabled_no_trigger_even_on_crash():
    cfg = BTCRiskOffConfig(enabled=False)
    _, ev = evaluate(90.0, 100.0, _NOW, BTCRiskOffState(), cfg)
    assert ev is None


def test_invalid_prices_no_trigger():
    _, ev1 = evaluate(0.0, 100.0, _NOW, BTCRiskOffState(), _CFG)
    _, ev2 = evaluate(100.0, 0.0, _NOW, BTCRiskOffState(), _CFG)
    assert ev1 is None and ev2 is None


def test_is_halted_no_state():
    assert not is_halted(BTCRiskOffState(), _NOW)
