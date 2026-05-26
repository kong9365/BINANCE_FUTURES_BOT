"""
tests/test_loss_cooldown.py
=====================================================================
strategy/loss_cooldown.py — 연속 손실 쿨다운 상태 머신 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from strategy.loss_cooldown import (
    LossCooldownConfig,
    LossCooldownState,
    is_cooldown_active,
    maybe_auto_clear,
    record_trade_result,
    reset,
)


_NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
_CFG = LossCooldownConfig(enabled=True, consecutive_losses_threshold=3, cooldown_hours=12.0)


def test_initial_state_no_cooldown():
    s = LossCooldownState()
    assert s.consecutive_losses == 0
    assert s.cooldown_until is None
    assert not is_cooldown_active(s, _NOW)


def test_single_loss_no_trigger():
    s = record_trade_result(LossCooldownState(), pnl=-10.0, now=_NOW, cfg=_CFG)
    assert s.consecutive_losses == 1
    assert s.cooldown_until is None
    assert not is_cooldown_active(s, _NOW)


def test_two_losses_no_trigger():
    s = LossCooldownState()
    s = record_trade_result(s, pnl=-10.0, now=_NOW, cfg=_CFG)
    s = record_trade_result(s, pnl=-5.0, now=_NOW + timedelta(minutes=10), cfg=_CFG)
    assert s.consecutive_losses == 2
    assert s.cooldown_until is None


def test_three_losses_triggers_cooldown():
    s = LossCooldownState()
    for i in range(3):
        s = record_trade_result(s, pnl=-3.0, now=_NOW + timedelta(minutes=i*10), cfg=_CFG)
    assert s.consecutive_losses == 3
    assert s.cooldown_until is not None
    # 12h 후 만료
    expected_until = _NOW + timedelta(minutes=20) + timedelta(hours=12.0)
    assert s.cooldown_until == expected_until
    assert is_cooldown_active(s, _NOW + timedelta(minutes=30))
    # 만료 시점 직전엔 활성
    assert is_cooldown_active(s, expected_until - timedelta(minutes=1))
    # 만료 시점부터 비활성
    assert not is_cooldown_active(s, expected_until)
    assert not is_cooldown_active(s, expected_until + timedelta(minutes=1))


def test_win_resets_streak():
    s = LossCooldownState(consecutive_losses=2)
    s = record_trade_result(s, pnl=15.0, now=_NOW, cfg=_CFG)
    assert s.consecutive_losses == 0
    # 다음 패배는 1부터 다시
    s = record_trade_result(s, pnl=-2.0, now=_NOW + timedelta(minutes=5), cfg=_CFG)
    assert s.consecutive_losses == 1


def test_disabled_no_trigger():
    cfg = LossCooldownConfig(enabled=False)
    s = LossCooldownState()
    for i in range(5):
        s = record_trade_result(s, pnl=-1.0, now=_NOW + timedelta(minutes=i), cfg=cfg)
    # streak 는 갱신되지만 cooldown 발동 안 함
    assert s.consecutive_losses == 5
    assert s.cooldown_until is None
    assert not is_cooldown_active(s, _NOW + timedelta(minutes=10))


def test_zero_pnl_treated_as_loss():
    """수수료 차감 후 손익 0 = 손실로 보수적 처리."""
    s = record_trade_result(LossCooldownState(), pnl=0.0, now=_NOW, cfg=_CFG)
    assert s.consecutive_losses == 1


def test_maybe_auto_clear():
    s = LossCooldownState(
        consecutive_losses=3,
        cooldown_until=_NOW - timedelta(minutes=1),  # 이미 만료
    )
    new_s = maybe_auto_clear(s, _NOW)
    assert new_s.cooldown_until is None
    assert new_s.consecutive_losses == 3  # streak 는 보존
    assert not is_cooldown_active(new_s, _NOW)


def test_maybe_auto_clear_still_active():
    s = LossCooldownState(
        consecutive_losses=3,
        cooldown_until=_NOW + timedelta(hours=1),
    )
    new_s = maybe_auto_clear(s, _NOW)
    assert new_s.cooldown_until == s.cooldown_until  # 변경 없음


def test_reset_manual():
    s = LossCooldownState(
        consecutive_losses=5,
        cooldown_until=_NOW + timedelta(hours=6),
    )
    new_s = reset(s)
    assert new_s.consecutive_losses == 0
    assert new_s.cooldown_until is None


def test_win_during_cooldown_does_not_clear():
    """승리가 들어와도 *기존 cooldown 은 자동 해제 안 됨* (시간 만료까지 유지).
    streak 만 0 으로 리셋. 다음 N 패배가 다시 누적되어야 새 cooldown 트리거.
    """
    s = LossCooldownState(
        consecutive_losses=3,
        cooldown_until=_NOW + timedelta(hours=6),
    )
    new_s = record_trade_result(s, pnl=20.0, now=_NOW + timedelta(minutes=10), cfg=_CFG)
    assert new_s.consecutive_losses == 0
    # cooldown_until 은 유지 (시간 만료로만 해제)
    assert new_s.cooldown_until == s.cooldown_until
    assert is_cooldown_active(new_s, _NOW + timedelta(minutes=11))
