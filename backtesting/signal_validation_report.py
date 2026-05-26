"""
backtesting/signal_validation_report.py
=====================================================================
SignalValidationReport — 7기준 결과를 setup_registry update + 보고서 생성

근거:
  - backtesting/signal_validation.py (7기준 검증)
  - registry/setup_registry.py (DB CRUD)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2

사용:
    report = validate_and_persist(
        trades=trades,
        setup_id="1d_tsmom_donchian_long_v1",
        registry=SetupRegistry(db_path),
        mode="trend",
    )
    print(report.summary())
=====================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from backtesting.signal_validation import (
    ValidationMode,
    ValidationReport,
    validate_setup,
)
from registry.setup_registry import SetupMetrics, SetupRegistry, SetupStatus

logger = logging.getLogger(__name__)


def validate_and_persist(
    trades: list[dict],
    setup_id: str,
    registry: SetupRegistry,
    mode: ValidationMode = "trend",
    evaluation_type: str = "backtest",
    auto_update_status: bool = True,
    response_latency_p95_ms: Optional[float] = None,
) -> ValidationReport:
    """7기준 검증 + setup_registry 자동 update + 보고서 반환.

    Args:
        trades: 거래 dict 리스트 (signal_validation.validate_setup 형식).
        setup_id: 등록된 setup_id.
        registry: SetupRegistry 인스턴스.
        mode: "trend" / "mean_rev".
        evaluation_type: "backtest" / "paper" / "live_7d".
        auto_update_status: True 면 결과에 따라 자동 status 변경:
            - passed → R0_QUALIFIED
            - 6/7 통과 + borderline → CONDITIONAL
            - 그 외 → DISABLED
        response_latency_p95_ms: M6 라이브 측정.

    Returns:
        ValidationReport.
    """
    report = validate_setup(
        trades=trades,
        mode=mode,
        response_latency_p95_ms=response_latency_p95_ms,
    )

    # SetupMetrics 변환
    metrics = SetupMetrics(
        n=int(report.metrics.get("n", 0)),
        pf=report.metrics.get("pf", 0.0),
        expectancy_r=report.metrics.get("expectancy_r", 0.0),
        avg_win_loss_ratio=report.metrics.get("avg_win_loss_ratio", 0.0),
        mdd_pct=report.metrics.get("mdd_pct", 0.0),
        single_symbol_max_pct=report.metrics.get("single_symbol_max_pct", 0.0),
        top3_excluded_pf=report.metrics.get("top3_excluded_pf", 0.0),
        win_rate=report.metrics.get("win_rate", 0.0),
        response_latency_p95_ms=report.metrics.get("response_latency_p95_ms", 0.0),
    )

    # setup_registry update
    registry.update_metrics(
        setup_id=setup_id,
        metrics=metrics,
        evaluation_type=evaluation_type,
        passed=report.passed,
        failed_criteria=report.failed_criteria,
        full_report_json=json.dumps(
            {
                "metrics": report.metrics,
                "thresholds": report.thresholds,
                "failed_criteria": report.failed_criteria,
                "notes": report.notes,
                "mode": mode,
            },
            sort_keys=True,
        ),
    )

    # 자동 status 변경 (선택)
    if auto_update_status:
        if report.passed:
            new_status = SetupStatus.R0_QUALIFIED
            reason = "7-criteria all passed"
        elif len(report.failed_criteria) == 1:
            new_status = SetupStatus.CONDITIONAL
            reason = f"6/7 passed, 1 borderline: {report.failed_criteria}"
        else:
            new_status = SetupStatus.DISABLED
            reason = (
                f"{len(report.failed_criteria)} criteria failed: "
                + ", ".join(report.failed_criteria)
            )
        registry.set_status(setup_id, new_status, reason=reason)

    return report


def summarize(report: ValidationReport) -> str:
    """ValidationReport 를 운영자 친화 텍스트로."""
    lines = [
        f"=== 7-Criteria Validation Report (mode={report.mode}) ===",
        f"Result: {'PASS [OK]' if report.passed else 'FAIL [X]'}",
        "",
        "Metrics:",
        f"  n                          = {int(report.metrics.get('n', 0))} "
        f"(min {report.thresholds.get('min_n', 200)})",
        f"  Net PF                     = {report.metrics.get('pf', 0):.3f} "
        f"(min {report.thresholds.get('min_pf', 1.25)})",
        f"  Expectancy_R               = {report.metrics.get('expectancy_r', 0):.4f} "
        f"(min {report.thresholds.get('min_expectancy_r', 0)})",
        f"  avg_win/avg_loss           = {report.metrics.get('avg_win_loss_ratio', 0):.3f} "
        f"(min {report.thresholds.get('min_avg_win_loss_ratio', 1.5)})",
        f"  MDD %                      = {report.metrics.get('mdd_pct', 0):.2f}% "
        f"(max {report.thresholds.get('max_mdd_pct', 25)}%)",
        f"  Single Symbol Max %        = {report.metrics.get('single_symbol_max_pct', 0):.2f}% "
        f"(max {report.thresholds.get('max_single_symbol_pct', 25)}%)",
        f"  Top-3 Excluded PF          = {report.metrics.get('top3_excluded_pf', 0):.3f} "
        f"(min {report.thresholds.get('min_top3_excluded_pf', 1.0)})",
        f"  Win Rate                   = {report.metrics.get('win_rate', 0) * 100:.1f}%",
    ]
    if report.metrics.get("response_latency_p95_ms") is not None:
        lines.append(
            f"  Response Latency p95 (ms)  = {report.metrics.get('response_latency_p95_ms', 0):.0f} "
            f"(max {report.thresholds.get('max_response_latency_p95_ms', 5000)})"
        )
    if report.failed_criteria:
        lines.append("")
        lines.append(f"Failed criteria ({len(report.failed_criteria)}): "
                     + ", ".join(report.failed_criteria))
    if report.notes:
        lines.append("")
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)
