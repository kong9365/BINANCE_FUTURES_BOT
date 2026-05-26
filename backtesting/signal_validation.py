"""
backtesting/signal_validation.py
=====================================================================
SignalValidation — 운영자 권장 7기준 백테스트 검증 게이트

근거:
  - 운영자 결정 추가 (2026-05-26): n≥200 / Net PF≥1.25 / Expectancy_R>0 /
    avg_win/avg_loss≥1.5 / MDD≤25% / single_symbol<25% / top3_excluded_pf≥1.0
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2 (7기준 게이트)
  - docs/HANDOFF.md (1d 돌파 ZEC 단일 97% 발견 → top3_excluded_pf 핵심)

7기준:
  1. n ≥ 200           (sample size)
  2. Net PF ≥ 1.25     (profitability, after fees)
  3. Expectancy_R > 0  (각 거래당 평균 R)
  4. avg_win / avg_loss ≥ 1.5  (payoff ratio — 추세는 win rate 낮아도 OK)
  5. MDD ≤ 25%
  6. Single symbol contribution < 25%  (집중 회피)
  7. Top-3 symbols excluded PF ≥ 1.0  (ZEC/LAB 단일종목 행운 방어, 2026-05-22 A2-②)

추가 (M6 라이브):
  8. Response latency p95 < 5000ms

설계 메모:
  - 입력: trades DataFrame (또는 list of trade dict).
  - 출력: ValidationReport (passed bool + failed_criteria list).
  - mode="mean_rev" 시: win_rate ≥ 0.55 추가 체크. mode="trend" 시 X.
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)

ValidationMode = Literal["trend", "mean_rev"]


@dataclass
class ValidationReport:
    """7기준 검증 결과."""

    passed: bool                         # 모든 기준 통과 여부
    failed_criteria: list[str]           # 실패한 기준 이름
    metrics: dict[str, float]            # 측정된 메트릭 값들
    thresholds: dict[str, float]         # 기준 임계값
    mode: ValidationMode
    notes: list[str] = field(default_factory=list)


# 운영자 권장 7기준 임계값 (변경 금지 — TIER 1 #9 정합)
DEFAULT_THRESHOLDS = {
    "min_n": 200,
    "min_pf": 1.25,
    "min_expectancy_r": 0.0,
    "min_avg_win_loss_ratio": 1.5,
    "max_mdd_pct": 25.0,
    "max_single_symbol_pct": 25.0,
    "min_top3_excluded_pf": 1.0,
    # mean_rev 모드만
    "min_win_rate_mean_rev": 0.55,
    # M6 라이브
    "max_response_latency_p95_ms": 5000.0,
}


def validate_setup(
    trades: list[dict],
    mode: ValidationMode = "trend",
    thresholds: Optional[dict] = None,
    response_latency_p95_ms: Optional[float] = None,
) -> ValidationReport:
    """trades 리스트로 7기준 검증.

    Args:
        trades: 거래 dict 리스트. 필수 키:
            - "symbol": str
            - "pnl_r": float (R 단위 — 1R = 진입 시 손절폭)
            - "pnl_pct": float (% 단위, MDD 계산용)
            - "is_win": bool (또는 pnl_r > 0)
        mode: "trend" (win rate 무관) / "mean_rev" (win rate ≥ 55% 추가)
        thresholds: 임계값 override (테스트 용도). 기본 DEFAULT_THRESHOLDS.
        response_latency_p95_ms: M6 라이브 측정 (None 이면 체크 안 함).

    Returns:
        ValidationReport — passed=False 면 failed_criteria 확인.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    n = len(trades)
    metrics: dict[str, float] = {}
    failed: list[str] = []
    notes: list[str] = []

    if n == 0:
        return ValidationReport(
            passed=False,
            failed_criteria=["min_n"],
            metrics={"n": 0},
            thresholds=th,
            mode=mode,
            notes=["No trades provided"],
        )

    # 1. n
    metrics["n"] = n
    if n < th["min_n"]:
        failed.append("min_n")

    # PF 계산
    gross_profit = sum(t["pnl_r"] for t in trades if t.get("pnl_r", 0) > 0)
    gross_loss = abs(sum(t["pnl_r"] for t in trades if t.get("pnl_r", 0) < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    metrics["pf"] = pf
    if pf < th["min_pf"]:
        failed.append("min_pf")

    # Expectancy R
    expectancy_r = sum(t.get("pnl_r", 0) for t in trades) / n
    metrics["expectancy_r"] = expectancy_r
    if expectancy_r <= th["min_expectancy_r"]:
        failed.append("min_expectancy_r")

    # Average win / loss ratio
    wins = [t["pnl_r"] for t in trades if t.get("pnl_r", 0) > 0]
    losses = [abs(t["pnl_r"]) for t in trades if t.get("pnl_r", 0) < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    avg_win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    metrics["avg_win"] = avg_win
    metrics["avg_loss"] = avg_loss
    metrics["avg_win_loss_ratio"] = avg_win_loss_ratio
    if avg_win_loss_ratio < th["min_avg_win_loss_ratio"]:
        failed.append("min_avg_win_loss_ratio")

    # Win rate (mode='mean_rev' 일 때 강제)
    win_rate = len(wins) / n
    metrics["win_rate"] = win_rate
    if mode == "mean_rev" and win_rate < th["min_win_rate_mean_rev"]:
        failed.append("min_win_rate_mean_rev")

    # MDD 계산 (equity curve 기반)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.get("pnl_pct", 0)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    metrics["mdd_pct"] = max_dd
    if max_dd > th["max_mdd_pct"]:
        failed.append("max_mdd_pct")

    # Single symbol contribution
    by_symbol_pnl: dict[str, float] = {}
    total_positive_pnl = sum(t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct", 0) > 0)
    for t in trades:
        s = t.get("symbol", "UNKNOWN")
        by_symbol_pnl[s] = by_symbol_pnl.get(s, 0) + t.get("pnl_pct", 0)
    positive_values = [v for v in by_symbol_pnl.values() if v > 0]
    if total_positive_pnl > 0 and positive_values:
        max_contrib = max(v / total_positive_pnl * 100 for v in positive_values)
    else:
        max_contrib = 0.0
    metrics["single_symbol_max_pct"] = max_contrib
    if max_contrib >= th["max_single_symbol_pct"]:
        failed.append("max_single_symbol_pct")

    # Top-3 excluded PF (운영자 7기준의 핵심, ZEC 단일 행운 방어)
    top3_symbols = sorted(
        by_symbol_pnl.items(), key=lambda x: x[1], reverse=True
    )[:3]
    top3_symbol_names = {s for s, _ in top3_symbols}
    excluded_trades = [
        t for t in trades if t.get("symbol") not in top3_symbol_names
    ]
    if excluded_trades:
        gp_ex = sum(
            t["pnl_r"] for t in excluded_trades if t.get("pnl_r", 0) > 0
        )
        gl_ex = abs(sum(
            t["pnl_r"] for t in excluded_trades if t.get("pnl_r", 0) < 0
        ))
        top3_excluded_pf = gp_ex / gl_ex if gl_ex > 0 else float("inf")
    else:
        top3_excluded_pf = 0.0  # 상위 3종목만 있음 → 매우 위험
    metrics["top3_excluded_pf"] = top3_excluded_pf
    if top3_excluded_pf < th["min_top3_excluded_pf"]:
        failed.append("min_top3_excluded_pf")
        notes.append(
            f"Top-3 symbols excluded PF {top3_excluded_pf:.2f} < "
            f"{th['min_top3_excluded_pf']} — 집중 의심 (ZEC/LAB 단일 패턴)"
        )

    # M6 응답지연 (옵션)
    if response_latency_p95_ms is not None:
        metrics["response_latency_p95_ms"] = response_latency_p95_ms
        if response_latency_p95_ms > th["max_response_latency_p95_ms"]:
            failed.append("max_response_latency_p95_ms")

    return ValidationReport(
        passed=len(failed) == 0,
        failed_criteria=failed,
        metrics=metrics,
        thresholds=th,
        mode=mode,
        notes=notes,
    )
