"""
strategy/pair_stat_arb.py
=====================================================================
Direction-neutral pair stat-arb 시그널 + 상태 머신 (Pair Stat-Arb R0).

설계 (plan §시그널 로직):
  qualified pair (A, B) + β + α (pair_selector 산출) 위에서:
    spread_t = log(p_a_t) - β·log(p_b_t) - α
    z_t      = (spread_t - rolling_mean_60d) / rolling_std_60d

  Entry (no position):
    z > +entry_z → SHORT A, LONG B
    z < -entry_z → LONG A, SHORT B
  Exit (in position):
    z 가 0 cross(진입 방향 sign 반전) → normal exit
    |z| > hard_stop_z → cointegration breaking, hard stop
    진입 후 time_stop_hours 경과 → time stop

설계 원칙:
  - 순수 함수 + immutable state dataclass. I/O 없음.
  - 룩어헤드 차단: 호출자가 시점 t 봉까지의 가격만 전달. 진입은 t+1 봉 시가 가정.
  - 백테스트와 라이브가 동일 모듈 호출.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

import numpy as np


# ── 상수 (action enum) ─────────────────────────────────────────────
ACTION_ENTER_LONG_A = "ENTER_LONG_A_SHORT_B"
ACTION_ENTER_SHORT_A = "ENTER_SHORT_A_LONG_B"
ACTION_EXIT_NORMAL = "EXIT_NORMAL"
ACTION_EXIT_HARD_STOP = "EXIT_HARD_STOP"
ACTION_EXIT_TIME_STOP = "EXIT_TIME_STOP"
ACTION_HOLD = "HOLD"

SIDE_LONG_A = "LONG_A"       # LONG A + SHORT B
SIDE_SHORT_A = "SHORT_A"     # SHORT A + LONG B


@dataclass
class PairArbConfig:
    """사전확정 임계 (R0 동안 변경 금지)."""

    entry_z: float = 2.0
    exit_z: float = 0.0          # z 가 진입 방향 sign 반전 시 normal exit
    hard_stop_z: float = 4.0
    time_stop_hours: float = 168.0   # 7일
    lookback_days: int = 60          # rolling mean/std 윈도(1h × 24 × 60 = 1440 봉)


@dataclass
class PairState:
    """페어 보유 상태(immutable).

    Attributes:
        side: SIDE_LONG_A / SIDE_SHORT_A / None(미보유).
        entry_z: 진입 시점의 z-score.
        entry_ts: 진입 시각(UTC).
    """

    side: Optional[str] = None
    entry_z: Optional[float] = None
    entry_ts: Optional[datetime] = None

    @property
    def in_position(self) -> bool:
        return self.side is not None


def compute_z(
    log_p_a_window: Sequence[float],
    log_p_b_window: Sequence[float],
    beta: float,
    intercept: float,
) -> Optional[float]:
    """최근 윈도 가격 → 현재 봉의 z-score.

    Args:
        log_p_a_window: 최근 N 봉 log price A (마지막 = 현재 봉 종가).
        log_p_b_window: 동일 길이의 B.
        beta, intercept: pair_selector 산출값(스냅샷).

    Returns:
        z (float) 또는 None (윈도 부족/std=0).
    """
    a = np.asarray(log_p_a_window, dtype=float)
    b = np.asarray(log_p_b_window, dtype=float)
    if a.shape != b.shape or len(a) < 10:
        return None
    spreads = a - beta * b - intercept
    mean = float(spreads.mean())
    std = float(spreads.std(ddof=1))
    if std <= 0 or not np.isfinite(std):
        return None
    current_spread = spreads[-1]
    z = (current_spread - mean) / std
    if not np.isfinite(z):
        return None
    return float(z)


def evaluate(
    state: PairState,
    z: Optional[float],
    now: datetime,
    cfg: Optional[PairArbConfig] = None,
) -> tuple[PairState, str]:
    """현재 z + state → 새 state + action.

    Returns:
        (new_state, action). action 은 ACTION_* 상수.
    """
    cfg = cfg or PairArbConfig()

    # z 계산 실패(데이터 부족 등) → 보유 중이면 hold, 아니면 hold
    if z is None or not np.isfinite(z):
        return state, ACTION_HOLD

    if state.in_position:
        # time stop
        if state.entry_ts is not None:
            elapsed_h = (now - state.entry_ts).total_seconds() / 3600.0
            if elapsed_h >= cfg.time_stop_hours:
                return PairState(side=None, entry_z=None, entry_ts=None), ACTION_EXIT_TIME_STOP

        # hard stop (cointegration 깨짐)
        if abs(z) > cfg.hard_stop_z:
            return PairState(side=None, entry_z=None, entry_ts=None), ACTION_EXIT_HARD_STOP

        # normal exit: z 가 진입 방향 반대로 cross
        # SHORT_A 진입은 z > +entry_z 에서 들어갔으므로 z <= exit_z 면 exit
        # LONG_A 진입은 z < -entry_z 에서 들어갔으므로 z >= -exit_z 면 exit
        if state.side == SIDE_SHORT_A and z <= cfg.exit_z:
            return PairState(side=None, entry_z=None, entry_ts=None), ACTION_EXIT_NORMAL
        if state.side == SIDE_LONG_A and z >= -cfg.exit_z:
            return PairState(side=None, entry_z=None, entry_ts=None), ACTION_EXIT_NORMAL

        return state, ACTION_HOLD

    # not in position — 진입 평가
    if z >= cfg.entry_z:
        new_state = PairState(side=SIDE_SHORT_A, entry_z=z, entry_ts=now)
        return new_state, ACTION_ENTER_SHORT_A
    if z <= -cfg.entry_z:
        new_state = PairState(side=SIDE_LONG_A, entry_z=z, entry_ts=now)
        return new_state, ACTION_ENTER_LONG_A
    return state, ACTION_HOLD
