"""
strategy/btc_risk_off.py
=====================================================================
BTC Risk-Off Safety Filter (LCR 셋업 C 독립 채택, Phase 0에서 데이터 검증됨).

근거 (docs/STRATEGY_REALISM_REVIEW.md §9):
  Phase 0 라벨링 결과(187종목·2년·n=13,887):
    - BTC 단독 급락 시 알트 셋업 A +4h 평균 +0.387%
    - BTC 동반 급락 시 −0.645%  → 1.03%pt 스프레드(명백한 불리)
  → "BTC가 1h 동안 −1.2% 이상 급락할 때 알트 신규 LONG 진입 금지" 룰은
  데이터로 검증된 *안전 필터*(거래 엣지 검증 불필요 — 회피 룰).

설계:
  - 순수 함수 + 상태 객체. I/O 없음(테스트 용이).
  - 호출자는 BTC 1h 마감봉의 최근 close 두 개와 현재 시각만 전달.
  - 트리거: drop% ≤ −drop_threshold AND 쿨다운 중 아님 → halted_until = now + cooldown.
  - 쿨다운 만료 시 자동 해제(다음 평가에서). 운영자 수동 해제도 가능(state.halted_until = None).
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


@dataclass
class BTCRiskOffConfig:
    enabled: bool = True
    drop_threshold_pct: float = 0.012     # 1.2% (음수 부호는 내부에서 처리)
    cooldown_hours: float = 6.0           # BTC 동반 급락 후 6h 회피(+4h 손실 구간 커버)


@dataclass
class BTCRiskOffState:
    halted_until: Optional[datetime] = None
    last_trigger_drop_pct: Optional[float] = None
    last_trigger_at: Optional[datetime] = None


@dataclass
class RiskOffEvent:
    """트리거 발생 시 반환되는 이벤트(로그·알림·기록용)."""
    triggered_at: datetime
    drop_pct: float
    halted_until: datetime
    btc_close: float


def evaluate(
    btc_close_now: float,
    btc_close_prev: float,
    now: datetime,
    state: BTCRiskOffState,
    cfg: Optional[BTCRiskOffConfig] = None,
) -> Tuple[BTCRiskOffState, Optional[RiskOffEvent]]:
    """BTC 1h 변화 감지 + 상태 머신.

    Args:
        btc_close_now: BTC 직전(가장 최근) 1h 봉 종가.
        btc_close_prev: 그 이전 1h 봉 종가.
        now: 현재 시각(UTC).
        state: 현재 BTCRiskOffState (변이 가능).
        cfg: 설정.

    Returns:
        (new_state, fired_event) — fired_event 는 *이번* 트리거가 새로 발동된 경우만 비-None.
    """
    cfg = cfg or BTCRiskOffConfig()
    if not cfg.enabled:
        return state, None

    # 쿨다운 만료 시 자동 해제
    if state.halted_until is not None and now >= state.halted_until:
        state = BTCRiskOffState(halted_until=None,
                                 last_trigger_drop_pct=state.last_trigger_drop_pct,
                                 last_trigger_at=state.last_trigger_at)

    # 이미 halted → 새 트리거 평가 없음(이벤트 폭주 방지). 자동 해제만.
    if state.halted_until is not None:
        return state, None

    if btc_close_prev <= 0 or btc_close_now <= 0:
        return state, None

    drop = (btc_close_now - btc_close_prev) / btc_close_prev
    if drop <= -cfg.drop_threshold_pct:
        halted_until = now + timedelta(hours=cfg.cooldown_hours)
        new_state = BTCRiskOffState(
            halted_until=halted_until,
            last_trigger_drop_pct=drop,
            last_trigger_at=now,
        )
        return new_state, RiskOffEvent(
            triggered_at=now, drop_pct=drop,
            halted_until=halted_until, btc_close=btc_close_now,
        )
    return state, None


def is_halted(state: BTCRiskOffState, now: datetime) -> bool:
    """현재 시각 기준 halt 활성 여부(자동 해제 반영)."""
    if state.halted_until is None:
        return False
    return now < state.halted_until
