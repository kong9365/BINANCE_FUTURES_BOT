"""
tests/test_exit_plan.py
=====================================================================
ExitPlanController 단위 테스트.

근거:
  - docs/SPEC_v3.1.md §8-8-2 (분할 TP, ATR 트레일, BE, 시간 스톱)
  - docs/SPEC_v3.1.md §8-8-3 (기존 포지션 자동 관리)
  - docs/SPEC_v3.1.md D-2 (ExitPlanController.start_tracking())

구성:
  - executor 는 AsyncMock 주입 (close_position 은 기본 success=True 반환)
  - now 는 datetime 직접 주입 (시간 스톱 결정성 확보)
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 시나리오:
  1. start_tracking — plan 등록 + partial_tp_price 계산
  2. 시간 스톱 → 전량 청산
  3. SL 히트 LONG / SHORT → 전량 청산
  4. TP 히트 → 전량 청산
  5. 분할 TP → portion 부분 청산 + BE 이동 (같은 iteration)
  6. 분할 TP 후 ATR 트레일 → SL 상향(LONG)/하향(SHORT)
  7. 분할 TP 전에는 트레일 미적용
  8. update_all — 순회 + 가격 없는 symbol 스킵
  9. stop_tracking / get_tracked / get_plan
 10. 전량 청산 실패 → 추적 유지 (재시도)
 11. 분할 청산 실패 → partial_tp_done 미설정
 12. 잘못된 비율 → ValueError
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from trading.exit_plan import ExitPlanController


# ── helpers ─────────────────────────────────────────────────────────

def _executor(success: bool = True):
    ex = AsyncMock()
    ex.close_position = AsyncMock(
        return_value={"success": success, "reason": "ok" if success else "fail"}
    )
    return ex


def _decision(*, symbol="SOLUSDT", action="LONG", entry=100.0, tp=102.0, sl=99.0):
    return {
        "symbol": symbol, "action": action,
        "entry_price": entry, "tp": tp, "sl": sl,
    }


_T0 = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


# ── 1. start_tracking ───────────────────────────────────────────────

def test_start_tracking_registers_plan():
    """start_tracking → plan 등록, partial_tp_price = entry→tp 50% 지점."""
    ctrl = ExitPlanController(_executor(), partial_tp_trigger_pct=0.5)
    plan = ctrl.start_tracking(1, _decision(entry=100.0, tp=102.0), quantity=0.5,
                               opened_at=_T0)
    assert ctrl.get_tracked() == ["SOLUSDT"]
    assert plan.partial_tp_price == pytest.approx(101.0)
    assert plan.current_sl == 99.0
    assert plan.peak_price == 100.0
    assert plan.quantity == 0.5


# ── 2. 시간 스톱 ────────────────────────────────────────────────────

async def test_time_stop_full_close():
    """max_hold_minutes 경과 → 전량 청산 (time_stop), 추적 해제."""
    ex = _executor()
    ctrl = ExitPlanController(ex, default_max_hold_minutes=120)
    ctrl.start_tracking(1, _decision(), quantity=0.5, opened_at=_T0)
    # 진입 후 3시간 경과
    result = await ctrl.update("SOLUSDT", 100.5, now=_T0 + timedelta(hours=3))
    assert result["closed"] is True
    assert "time_stop" in result["actions"]
    ex.close_position.assert_awaited_once_with("SOLUSDT", reason="time_stop")
    assert ctrl.get_tracked() == []


# ── 3. SL 히트 ──────────────────────────────────────────────────────

async def test_sl_hit_long_full_close():
    """LONG: 현재가 <= current_sl → 전량 청산 (stop_loss)."""
    ex = _executor()
    ctrl = ExitPlanController(ex)
    ctrl.start_tracking(1, _decision(action="LONG", entry=100.0, sl=99.0),
                        quantity=0.5, opened_at=_T0)
    result = await ctrl.update("SOLUSDT", 98.9, now=_T0 + timedelta(minutes=10))
    assert result["closed"] is True
    ex.close_position.assert_awaited_once_with("SOLUSDT", reason="stop_loss")


async def test_sl_hit_short_full_close():
    """SHORT: 현재가 >= current_sl → 전량 청산."""
    ex = _executor()
    ctrl = ExitPlanController(ex)
    ctrl.start_tracking(1, _decision(action="SHORT", entry=100.0, tp=98.0, sl=101.0),
                        quantity=0.5, opened_at=_T0)
    result = await ctrl.update("SOLUSDT", 101.1, now=_T0 + timedelta(minutes=10))
    assert result["closed"] is True
    ex.close_position.assert_awaited_once_with("SOLUSDT", reason="stop_loss")


# ── 4. TP 히트 ──────────────────────────────────────────────────────

async def test_tp_hit_full_close():
    """LONG: 현재가 >= tp → 전량 청산 (take_profit)."""
    ex = _executor()
    ctrl = ExitPlanController(ex)
    ctrl.start_tracking(1, _decision(entry=100.0, tp=102.0), quantity=0.5,
                        opened_at=_T0)
    result = await ctrl.update("SOLUSDT", 102.5, now=_T0 + timedelta(minutes=10))
    assert result["closed"] is True
    ex.close_position.assert_awaited_once_with("SOLUSDT", reason="take_profit")


# ── 5. 분할 TP + BE ─────────────────────────────────────────────────

async def test_partial_tp_triggers_partial_close_and_breakeven():
    """분할 TP 트리거 → portion 부분 청산 + 같은 iteration 에 SL → BE 이동."""
    ex = _executor()
    ctrl = ExitPlanController(ex, partial_tp_ratio=0.5, partial_tp_trigger_pct=0.5)
    ctrl.start_tracking(1, _decision(entry=100.0, tp=102.0, sl=99.0),
                        quantity=1.0, opened_at=_T0)
    # partial_tp_price = 101.0, 현재가 101.5 → 분할 TP 히트 (TP 102 미도달)
    result = await ctrl.update("SOLUSDT", 101.5, now=_T0 + timedelta(minutes=10))
    assert result["closed"] is False
    assert "partial_tp" in result["actions"]
    assert "breakeven" in result["actions"]
    ex.close_position.assert_awaited_once_with(
        "SOLUSDT", reason="partial_tp", portion=0.5
    )
    plan = ctrl.get_plan("SOLUSDT")
    assert plan.partial_tp_done is True
    assert plan.be_moved is True
    assert plan.current_sl == 100.0          # break-even = entry


# ── 6. ATR 트레일 ───────────────────────────────────────────────────

async def test_atr_trailing_raises_sl_long_after_partial():
    """분할 TP 후 LONG: ATR 트레일이 SL 을 상향 이동시킨다."""
    ex = _executor()
    ctrl = ExitPlanController(ex, partial_tp_ratio=0.5, partial_tp_trigger_pct=0.5,
                              atr_trail_multiplier=1.5)
    ctrl.start_tracking(1, _decision(entry=100.0, tp=110.0, sl=99.0),
                        quantity=1.0, opened_at=_T0)
    # partial_tp_price = 105.0. 1차: 105.5 → 분할 TP + BE(sl=100)
    await ctrl.update("SOLUSDT", 105.5, now=_T0 + timedelta(minutes=10))
    # 2차: 106.0, atr=0.4 → peak=106, trail_sl = 106 - 0.6 = 105.4 > 100
    result = await ctrl.update("SOLUSDT", 106.0, current_atr=0.4,
                               now=_T0 + timedelta(minutes=20))
    assert "trail" in result["actions"]
    assert ctrl.get_plan("SOLUSDT").current_sl == pytest.approx(105.4)


async def test_atr_trailing_lowers_sl_short_after_partial():
    """분할 TP 후 SHORT: ATR 트레일이 SL 을 하향 이동시킨다."""
    ex = _executor()
    ctrl = ExitPlanController(ex, partial_tp_ratio=0.5, partial_tp_trigger_pct=0.5,
                              atr_trail_multiplier=1.5)
    ctrl.start_tracking(1, _decision(action="SHORT", entry=100.0, tp=90.0, sl=101.0),
                        quantity=1.0, opened_at=_T0)
    # partial_tp_price = 95.0. 1차: 94.5 → 분할 TP + BE(sl=100)
    await ctrl.update("SOLUSDT", 94.5, now=_T0 + timedelta(minutes=10))
    # 2차: 94.0, atr=0.4 → peak=94, trail_sl = 94 + 0.6 = 94.6 < 100
    result = await ctrl.update("SOLUSDT", 94.0, current_atr=0.4,
                               now=_T0 + timedelta(minutes=20))
    assert "trail" in result["actions"]
    assert ctrl.get_plan("SOLUSDT").current_sl == pytest.approx(94.6)


# ── 7. 분할 전 트레일 미적용 ────────────────────────────────────────

async def test_no_trailing_before_partial_tp():
    """분할 TP 체결 전에는 ATR 가 주어져도 트레일링하지 않는다."""
    ex = _executor()
    ctrl = ExitPlanController(ex, partial_tp_trigger_pct=0.5)
    ctrl.start_tracking(1, _decision(entry=100.0, tp=110.0, sl=99.0),
                        quantity=1.0, opened_at=_T0)
    # partial_tp_price = 105.0, 현재가 102.0 → 분할 미도달
    result = await ctrl.update("SOLUSDT", 102.0, current_atr=0.5,
                               now=_T0 + timedelta(minutes=10))
    assert result["actions"] == []
    assert ctrl.get_plan("SOLUSDT").current_sl == 99.0   # 불변


# ── 8. update_all ──────────────────────────────────────────────────

async def test_update_all_iterates_and_skips_missing_price():
    """update_all — 추적 전체 순회, price_map 에 없는 symbol 은 스킵."""
    ex = _executor()
    ctrl = ExitPlanController(ex)
    ctrl.start_tracking(1, _decision(symbol="SOLUSDT"), quantity=0.5, opened_at=_T0)
    ctrl.start_tracking(2, _decision(symbol="BNBUSDT"), quantity=0.5, opened_at=_T0)
    results = await ctrl.update_all(
        {"SOLUSDT": 100.5}, now=_T0 + timedelta(minutes=10)
    )
    # BNBUSDT 는 가격 없어 스킵
    assert [r["symbol"] for r in results] == ["SOLUSDT"]


# ── 9. 추적 관리 ────────────────────────────────────────────────────

def test_stop_tracking_and_getters():
    """stop_tracking / get_tracked / get_plan 동작."""
    ctrl = ExitPlanController(_executor())
    ctrl.start_tracking(1, _decision(symbol="SOLUSDT"), quantity=0.5, opened_at=_T0)
    assert ctrl.get_plan("SOLUSDT") is not None
    ctrl.stop_tracking("SOLUSDT")
    assert ctrl.get_tracked() == []
    assert ctrl.get_plan("SOLUSDT") is None
    ctrl.stop_tracking("SOLUSDT")  # 재호출 안전 (no-op)


# ── 10. 전량 청산 실패 → 추적 유지 ──────────────────────────────────

async def test_full_close_failure_keeps_tracking():
    """close_position 실패 시 plan 을 제거하지 않고 재시도 대상으로 유지."""
    ex = _executor(success=False)
    ctrl = ExitPlanController(ex)
    ctrl.start_tracking(1, _decision(entry=100.0, sl=99.0), quantity=0.5,
                        opened_at=_T0)
    result = await ctrl.update("SOLUSDT", 98.5, now=_T0 + timedelta(minutes=10))
    assert result["closed"] is False
    assert "stop_loss_failed" in result["actions"]
    assert ctrl.get_tracked() == ["SOLUSDT"]   # 추적 유지


# ── 11. 분할 청산 실패 → partial_tp_done 미설정 ─────────────────────

async def test_partial_close_failure_keeps_partial_undone():
    """분할 청산 실패 시 partial_tp_done 이 False 로 남아 다음 루프 재시도."""
    ex = _executor(success=False)
    ctrl = ExitPlanController(ex, partial_tp_trigger_pct=0.5)
    ctrl.start_tracking(1, _decision(entry=100.0, tp=102.0, sl=99.0),
                        quantity=1.0, opened_at=_T0)
    result = await ctrl.update("SOLUSDT", 101.5, now=_T0 + timedelta(minutes=10))
    assert "partial_tp_failed" in result["actions"]
    plan = ctrl.get_plan("SOLUSDT")
    assert plan.partial_tp_done is False
    assert plan.be_moved is False


# ── 12. 입력 검증 ───────────────────────────────────────────────────

def test_invalid_ratios_raise():
    """partial_tp_ratio / partial_tp_trigger_pct 가 (0,1) 밖이면 ValueError."""
    with pytest.raises(ValueError):
        ExitPlanController(_executor(), partial_tp_ratio=0.0)
    with pytest.raises(ValueError):
        ExitPlanController(_executor(), partial_tp_ratio=1.0)
    with pytest.raises(ValueError):
        ExitPlanController(_executor(), partial_tp_trigger_pct=1.5)


# ── 추적 비대상 symbol ──────────────────────────────────────────────

async def test_update_untracked_symbol():
    """추적하지 않는 symbol → tracked=False."""
    ctrl = ExitPlanController(_executor())
    result = await ctrl.update("UNKNOWN", 100.0)
    assert result["tracked"] is False
    assert result["closed"] is False
