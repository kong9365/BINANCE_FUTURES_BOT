"""
governance/stage3_rule.py
=====================================================================
Stage 3 Hard Rule — 청사진 §7.5 의 9-strategy fail 시 archetype 재고.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §7.5 (Stage 3 90d -10% / MDD -25%)
  - governance/risk_hierarchy.py (stage3_rolling_90d_pct, stage3_mdd_pct)
  - docs/HANDOFF.md "알파 영구 중단" 결정

발동 조건:
  rolling 90d PnL ≤ -10% 또는 rolling 90d MDD > 25%
→ KillSwitch 자동 활성화 + Slack 옵션 A/B/C 안내
  옵션 A: Manual semi-discretionary 전환
  옵션 B: Buy-and-hold + 50d MA cash exit (Grayscale 2023)
  옵션 C: 운영자 가설 청취

설계 메모:
  - 매 60분 주기 호출 (main_7590._main_loop 통합).
  - DB의 trades 테이블에서 rolling 90d PnL 집계.
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from governance.risk_hierarchy import RISK_HIERARCHY_CONFIG, RiskHierarchyConfig

logger = logging.getLogger(__name__)


@dataclass
class Stage3Result:
    """Stage 3 평가 결과."""

    triggered: bool                # True 면 archetype 재고 권장
    reason: Optional[str] = None
    rolling_90d_pnl_pct: float = 0.0
    rolling_90d_mdd_pct: float = 0.0
    options_message: Optional[str] = None  # Slack 알림 메시지


# 운영자 옵션 A/B/C (청사진 §7.5)
OPTIONS_MESSAGE_TEMPLATE = """🛑 STAGE 3 HARD RULE ACTIVATED

90d rolling PnL: {pnl_pct:.2f}% (한도 -{pnl_limit:.1f}%)
90d MDD: {mdd_pct:.2f}% (한도 {mdd_limit:.1f}%)

운영자: 자동매매 archetype 재고 권장
옵션:
  A. Manual semi-discretionary 전환
  B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)
  C. 운영자 가설 청취

(청사진 §7.5 Stage 3 백스톱)
"""


def evaluate_stage3(
    db_path: str,
    cfg: RiskHierarchyConfig = RISK_HIERARCHY_CONFIG,
    now: Optional[datetime] = None,
) -> Stage3Result:
    """rolling 90d PnL + MDD 평가.

    Args:
        db_path: sqlite3 DB 경로 (trades 테이블).
        cfg: RiskHierarchyConfig (stage3_rolling_90d_pct, stage3_mdd_pct).
        now: 현재 UTC (테스트 용도).

    Returns:
        Stage3Result — triggered=True 면 archetype 재고 권장.
    """
    if not db_path:
        raise ValueError("db_path must not be empty")

    eval_now = now if now is not None else datetime.now(timezone.utc)
    if eval_now.tzinfo is None:
        eval_now = eval_now.replace(tzinfo=timezone.utc)
    cutoff = (eval_now - timedelta(days=90)).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        # rolling 90d realized PnL 합계
        row = conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(pnl_usd_net, pnl_usd)), 0.0)
            FROM trades
            WHERE exit_price IS NOT NULL
              AND timestamp >= ?
            """,
            (cutoff,),
        ).fetchone()
        rolling_pnl_usd = float(row[0]) if row and row[0] is not None else 0.0

        # rolling 90d trade 목록 (equity curve 계산)
        rows = conn.execute(
            """
            SELECT COALESCE(pnl_usd_net, pnl_usd) AS net_pnl
            FROM trades
            WHERE exit_price IS NOT NULL
              AND timestamp >= ?
            ORDER BY id ASC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    # MDD 계산 (equity curve 기반)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rows:
        if r[0] is None:
            continue
        equity += float(r[0])
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # 단순화: 초기 자본 1000 USD 가정으로 % 환산 (실제는 capital_initial에서)
    # M4 시점에는 단순화 — 운영자 결정 후 capital_manager 연동 가능
    base_capital = 1000.0
    rolling_pnl_pct = rolling_pnl_usd / base_capital * 100
    rolling_mdd_pct = max_dd / base_capital * 100

    pnl_limit_pct = cfg.stage3_rolling_90d_pct * 100
    mdd_limit_pct = cfg.stage3_mdd_pct * 100

    pnl_breached = rolling_pnl_pct <= -pnl_limit_pct
    mdd_breached = rolling_mdd_pct > mdd_limit_pct

    if pnl_breached or mdd_breached:
        reasons = []
        if pnl_breached:
            reasons.append(f"90d PnL {rolling_pnl_pct:.2f}% ≤ -{pnl_limit_pct:.1f}%")
        if mdd_breached:
            reasons.append(f"90d MDD {rolling_mdd_pct:.2f}% > {mdd_limit_pct:.1f}%")
        reason = "; ".join(reasons)

        options_message = OPTIONS_MESSAGE_TEMPLATE.format(
            pnl_pct=rolling_pnl_pct,
            pnl_limit=pnl_limit_pct,
            mdd_pct=rolling_mdd_pct,
            mdd_limit=mdd_limit_pct,
        )

        return Stage3Result(
            triggered=True,
            reason=reason,
            rolling_90d_pnl_pct=rolling_pnl_pct,
            rolling_90d_mdd_pct=rolling_mdd_pct,
            options_message=options_message,
        )

    return Stage3Result(
        triggered=False,
        rolling_90d_pnl_pct=rolling_pnl_pct,
        rolling_90d_mdd_pct=rolling_mdd_pct,
    )
