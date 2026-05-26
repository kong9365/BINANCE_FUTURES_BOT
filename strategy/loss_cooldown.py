"""
strategy/loss_cooldown.py
=====================================================================
연속 손실 쿨다운 상태 머신 (CCS-Lite v1.1 kill gate 6).

근거:
  연속 손실은 *전략 미스매치* 또는 *시장 레짐 전환* 신호일 수 있다. 적어도
  체계적으로 (a) 단기 진입 차단, (b) 운영자가 상황을 점검할 시간 확보,
  (c) 손실 가속화 방지(틸트 트레이딩 자동 방지)를 위해 N연속 패배 시 신규
  진입을 일정 시간 자동 차단한다.

설계:
  - 순수 함수 + 상태 객체. I/O 없음(테스트 용이).
  - record_trade_result 가 trade_pnl(±) 를 받아 streak 갱신 + 임계 도달 시
    cooldown_until 설정.
  - is_cooldown_active 가 현재 시각 기준 활성 여부(자동 해제) 판정.
  - 운영자 수동 해제: state.cooldown_until = None.

연동:
  - 라이브: 봇이 청산 완료 시 record_trade_result 호출.
  - 백테스트: 시뮬레이션 청산 시 호출.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class LossCooldownConfig:
    """연속 손실 쿨다운 파라미터.

    CCS-Lite v1.1 운영 제약: 연속 3패 → 12h 차단(자동 해제). 기존 RiskRules
    의 `max_consecutive_losses_warning`(3패 → 4h cooldown)와 *별개*인 *CCS
    전용* 게이트로 더 강한 12h 차단을 부과한다. (RiskRules 와 중복 작동해도
    OR 로 둘 다 적용 — 더 엄격한 쪽 효과만 보임.)
    """

    enabled: bool = True
    consecutive_losses_threshold: int = 3
    cooldown_hours: float = 12.0


@dataclass
class LossCooldownState:
    """연속 손실 상태.

    Attributes:
        consecutive_losses: 현재 연속 패배 수(승리 시 0 리셋).
        cooldown_until: 활성 cooldown 만료 시각(UTC). None 이면 비활성.
        last_trigger_at: 마지막 cooldown 발동 시각(UTC).
        last_result_at: 마지막 trade 결과 반영 시각(UTC).
    """

    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None
    last_trigger_at: Optional[datetime] = None
    last_result_at: Optional[datetime] = None


def record_trade_result(
    state: LossCooldownState,
    pnl: float,
    now: datetime,
    cfg: Optional[LossCooldownConfig] = None,
) -> LossCooldownState:
    """트레이드 결과 1건 반영 → 새 상태.

    Args:
        state: 현재 LossCooldownState.
        pnl: 청산 손익(USDT). 양수=승리, 0/음수=패배(0은 손실 처리 — 보수적).
        now: 현재 시각(UTC).
        cfg: 설정.

    Returns:
        새 LossCooldownState. 임계 도달 시 cooldown_until 설정.

    노트:
        - pnl == 0 은 *수수료 차감 후 손실에 준함*으로 보수적 처리.
        - cfg.enabled=False 면 통계만 업데이트(cooldown_until 발동 안 함).
    """
    cfg = cfg or LossCooldownConfig()

    new_losses = state.consecutive_losses
    cooldown_until = state.cooldown_until
    last_trigger = state.last_trigger_at

    if pnl > 0:
        # 승리 → streak 리셋.
        new_losses = 0
    else:
        # 패배(0 포함) → streak 증가.
        new_losses = state.consecutive_losses + 1
        if cfg.enabled and new_losses >= cfg.consecutive_losses_threshold:
            cooldown_until = now + timedelta(hours=cfg.cooldown_hours)
            last_trigger = now

    return LossCooldownState(
        consecutive_losses=new_losses,
        cooldown_until=cooldown_until,
        last_trigger_at=last_trigger,
        last_result_at=now,
    )


def is_cooldown_active(state: LossCooldownState, now: datetime) -> bool:
    """현재 시각 기준 cooldown 활성 여부(자동 해제 반영).

    Args:
        state: LossCooldownState.
        now: 현재 시각(UTC).

    Returns:
        True 면 신규 진입 차단해야 함.
    """
    if state.cooldown_until is None:
        return False
    return now < state.cooldown_until


def maybe_auto_clear(state: LossCooldownState, now: datetime) -> LossCooldownState:
    """cooldown 만료 시 cooldown_until 만 None 처리한 새 상태 반환.

    streak 자체는 보존(이후 승리/패배가 갱신). 호출자가 라이브 루프에서 매
    iter 호출하면 만료 후 자동으로 비활성화된다.
    """
    if state.cooldown_until is not None and now >= state.cooldown_until:
        return LossCooldownState(
            consecutive_losses=state.consecutive_losses,
            cooldown_until=None,
            last_trigger_at=state.last_trigger_at,
            last_result_at=state.last_result_at,
        )
    return state


def reset(state: LossCooldownState) -> LossCooldownState:
    """수동 리셋(streak + cooldown 둘 다 0/None). 운영자 명시 호출용."""
    return LossCooldownState(
        consecutive_losses=0,
        cooldown_until=None,
        last_trigger_at=state.last_trigger_at,
        last_result_at=state.last_result_at,
    )
