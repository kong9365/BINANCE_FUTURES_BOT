"""
governance/risk_hierarchy.py
=====================================================================
RiskHierarchyConfig — 2-tier hierarchy (운영자 결정 #v2-2).

근거:
  - 운영자 결정 (2026-05-26): 2-tier 단순화 (3-tier 의 soft 제거)
  - docs/SPEC_v3.1_APPENDIX_E.md E-10 (2-tier hierarchy)
  - REFACTOR_PLAN_v2_BLUEPRINT.md M4

2-tier:
  - warn -1.0% → Slack/Telegram 경고 (거래 계속)
  - hard -1.5% → KillSwitch 자동 활성화 (SPEC v3.1.1 RISK_RULES 유지)

청사진 §7.4 의 daily_soft_pct -2.0% 는 *제거* (hard -1.5%에서 이미 차단되어 도달 X).
청사진 -3.0% / -5.0% 는 *최종 백스톱 Stage 3* (별도 governance/stage3_rule.py).
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RiskHierarchyConfig:
    """2-tier risk hierarchy (운영자 결정 #v2-2)."""

    # Daily loss
    daily_warn_pct: float = 0.010    # -1.0% → Slack 경고
    daily_hard_pct: float = 0.015    # -1.5% → KillSwitch (SPEC v3.1.1)

    # MDD
    mdd_warn_pct: float = 0.10        # -10% → Slack 경고
    mdd_hard_pct: float = 0.15        # -15% → KillSwitch (SPEC v3.1.1)

    # Stage 3 백스톱 (청사진 §7.5)
    stage3_rolling_90d_pct: float = 0.10   # 90d 누적 -10% → KillSwitch
    stage3_mdd_pct: float = 0.25            # 90d MDD -25% → KillSwitch


# 모듈 레벨 export (config/settings.py 패턴 정합)
RISK_HIERARCHY_CONFIG = RiskHierarchyConfig()


def check_daily_loss(
    current_wallet: float,
    daily_start: float,
    cfg: RiskHierarchyConfig = RISK_HIERARCHY_CONFIG,
) -> str:
    """일일 손익 단계 평가.

    Args:
        current_wallet: 현재 wallet_balance.
        daily_start: 오늘 00:00 UTC wallet_balance.
        cfg: 임계 (기본 RISK_HIERARCHY_CONFIG).

    Returns:
        'OK' / 'WARN' / 'HARD' / 'UNKNOWN' (daily_start <= 0).
    """
    if daily_start <= 0:
        return "UNKNOWN"
    pct = (current_wallet - daily_start) / daily_start
    if pct <= -cfg.daily_hard_pct:
        return "HARD"
    if pct <= -cfg.daily_warn_pct:
        return "WARN"
    return "OK"


def check_mdd(
    current_wallet: float,
    initial: float,
    cfg: RiskHierarchyConfig = RISK_HIERARCHY_CONFIG,
) -> str:
    """전체 MDD 단계 평가."""
    if initial <= 0:
        return "UNKNOWN"
    pct = (current_wallet - initial) / initial
    if pct <= -cfg.mdd_hard_pct:
        return "HARD"
    if pct <= -cfg.mdd_warn_pct:
        return "WARN"
    return "OK"
