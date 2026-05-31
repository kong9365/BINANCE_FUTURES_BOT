"""
governance/probe_guard.py
=====================================================================
ProbeGuard — 경계 있는 B-3 계측 프로브의 다조건 전면 HALT 평가 (순수 함수).

auto_trigger 패턴: 순수 평가만 수행 → caller(main_7590._iter)가 KillSwitch.activate +
DB 읽기/쓰기를 담당(의존성 분리). PROBE_CONFIG.enabled 일 때만 caller 가 호출한다.

fail 정책:
  - 손실/자본/연속(조건 1-3) = fail-closed: 입력 None(조회 실패) → halt=True + unevaluable=True.
  - 카운트/시간 자동정지(조건 4-5) = fail-open: 입력 None → skip(halt=False).
  - 래칭 여부는 caller 가 분리: 실제 임계 위반(unevaluable=False) → KillSwitch.activate(영속);
    unevaluable=True(일시적 조회 실패) → 래칭 없이 당 iter 차단(스퓨리어스 다일 HALT 방지).

조건(첫 적중 우선):
  1. 총손실 ≤ -total_loss_pct (basis = 초기자본)
  2. 일일손실 ≤ -daily_loss_pct (basis = 일일시작자본)
  3. 연속손실 ≥ max_consec_losses
  4. 체결 ≥ n_fill AND 미체결 ≥ n_unfill → auto_stop (데이터 목표 — 경계 있는 실험)
  5. 경과 ≥ max_weeks → auto_stop (기간)
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class ProbeHaltResult:
    """프로브 HALT 평가 결과."""

    halt: bool
    reason: Optional[str] = None
    # probe_total_loss / probe_daily_loss / probe_consec_losses /
    # probe_data_target / probe_max_weeks / probe_*_unevaluable
    source: Optional[str] = None
    auto_stop: bool = False        # True = 경계 자동정지(손실 아님 — 데이터/기간 도달)
    unevaluable: bool = False      # True = 입력 None 으로 평가불가(fail-closed) — caller 비래칭 처리


def evaluate_probe_halt(
    *,
    current_wallet: Optional[float],
    initial_capital: Optional[float],
    daily_start_capital: Optional[float],
    loss_streak: Optional[int],
    filled_count: Optional[int],
    unfilled_count: Optional[int],
    probe_start_at: Optional[datetime],
    now: datetime,
    cfg,
) -> ProbeHaltResult:
    """프로브 HALT 평가. 손실/자본/연속(1-3) fail-closed, 카운트/시간(4-5) fail-open."""
    # 1. 총손실 (fail-closed)
    if current_wallet is None or initial_capital is None:
        return ProbeHaltResult(
            True, "총손실 평가불가(자본 None)", "probe_total_loss_unevaluable", unevaluable=True)
    if initial_capital > 0:
        total_ret = (current_wallet - initial_capital) / initial_capital
        if total_ret <= -cfg.total_loss_pct:
            return ProbeHaltResult(
                True, f"총손실 {total_ret*100:.2f}% ≤ -{cfg.total_loss_pct*100:.0f}%",
                "probe_total_loss")

    # 2. 일일손실 (fail-closed)
    if daily_start_capital is None:
        return ProbeHaltResult(
            True, "일일손실 평가불가(daily_start None)", "probe_daily_loss_unevaluable",
            unevaluable=True)
    if daily_start_capital > 0:
        daily_ret = (current_wallet - daily_start_capital) / daily_start_capital
        if daily_ret <= -cfg.daily_loss_pct:
            return ProbeHaltResult(
                True, f"일일손실 {daily_ret*100:.2f}% ≤ -{cfg.daily_loss_pct*100:.0f}%",
                "probe_daily_loss")

    # 3. 연속손실 (fail-closed)
    if loss_streak is None:
        return ProbeHaltResult(
            True, "연속손실 평가불가(streak None)", "probe_consec_losses_unevaluable",
            unevaluable=True)
    if loss_streak >= cfg.max_consec_losses:
        return ProbeHaltResult(
            True, f"연속손실 {loss_streak} ≥ {cfg.max_consec_losses}", "probe_consec_losses")

    # 4. 데이터 목표 자동정지 (fail-open)
    if filled_count is not None and unfilled_count is not None:
        if filled_count >= cfg.n_fill and unfilled_count >= cfg.n_unfill:
            return ProbeHaltResult(
                True,
                f"데이터 목표 도달(체결 {filled_count}≥{cfg.n_fill}, 미체결 {unfilled_count}≥{cfg.n_unfill})",
                "probe_data_target", auto_stop=True)

    # 5. 기간 자동정지 (fail-open)
    if probe_start_at is not None:
        if now - probe_start_at >= timedelta(weeks=cfg.max_weeks):
            return ProbeHaltResult(
                True, f"기간 경과(≥{cfg.max_weeks}주)", "probe_max_weeks", auto_stop=True)

    return ProbeHaltResult(False)
