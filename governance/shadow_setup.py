"""
governance/shadow_setup.py
=====================================================================
ShadowAgentRunner 의존성 셋업 헬퍼 (M14 main_7590 통합용).

근거:
  - REFACTOR_PLAN_v2_BLUEPRINT.md M14 Paper 운영
  - 운영자 결정 (2026-05-27): ENABLE_SHADOW_AGENTS=true → main_7590 통합

설계:
  - main_7590.py 본문 변경 최소화 (1~3줄). 모든 의존성 셋업은 본 모듈 캡슐화.
  - ENABLE_SHADOW_AGENTS=false (env 미설정 또는 false) 면 None 반환 → no-op.
  - 5-Agent + AuditLogger + SetupRegistry + Orchestrator 생성 비싸므로 *1회만*.
=====================================================================
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from governance.shadow_config import resolve_shadow_enabled

if TYPE_CHECKING:
    from governance.shadow_runner import ShadowAgentRunner

logger = logging.getLogger(__name__)


def build_shadow_runner_for_main_bot(
    risk_manager,
    system_health_monitor,
    db_path: str,
) -> Optional["ShadowAgentRunner"]:
    """main_7590 의 의존성으로 ShadowAgentRunner 생성.

    Args:
        risk_manager: main_7590.risk_manager (RiskManager 인스턴스)
        system_health_monitor: main_7590.health (SystemHealthMonitor)
        db_path: main_7590 DB 경로 (SYSTEM_CONFIG 결정)

    Returns:
        ShadowAgentRunner — env ENABLE_SHADOW_AGENTS=true 일 때만. 그 외 None.
    """
    if not resolve_shadow_enabled():
        logger.debug("[ShadowSetup] ENABLE_SHADOW_AGENTS=false → shadow 비활성")
        return None

    try:
        from agents.data_agent import DataAgent
        from agents.execution_agent import ExecutionAgent
        from agents.ops_agent import OpsAgent
        from agents.orchestrator import AgentOrchestrator
        from agents.risk_agent import RiskAgent
        from agents.strategy_agent import StrategyAgent
        from audit.audit_logger import AuditLogger
        from governance.shadow_runner import ShadowAgentRunner
        from registry.setup_registry import SetupRegistry
        from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill
    except ImportError as e:
        logger.warning("[ShadowSetup] 의존성 import 실패: %s — shadow 비활성", e)
        return None

    try:
        # SetupRegistry 등록 (멱등 — 이미 있으면 갱신만)
        registry = SetupRegistry(db_path=db_path)
        registry.register(DailyTSMOMDonchianSkill)

        # AuditLogger (chain hash 자동)
        audit_logger = AuditLogger(db_path=db_path)

        # 5-Agent
        strategy_agent = StrategyAgent(registry=registry)
        risk_agent = RiskAgent(risk_manager=risk_manager)
        execution_agent = ExecutionAgent()
        data_agent = DataAgent()    # 기본: settings 의 protected_symbols 자동 사용
        ops_agent = OpsAgent(
            system_health_monitor=system_health_monitor, db_path=db_path,
        )

        orchestrator = AgentOrchestrator(
            strategy_agent=strategy_agent,
            risk_agent=risk_agent,
            execution_agent=execution_agent,
            data_agent=data_agent,
            ops_agent=ops_agent,
            audit_logger=audit_logger,
        )

        runner = ShadowAgentRunner(
            orchestrator=orchestrator,
            audit_logger=audit_logger,
        )

        logger.info(
            "[ShadowSetup] 5-Agent shadow runner 초기화 완료 "
            "(setup_id=%s, ENABLE_SHADOW_AGENTS=true)",
            DailyTSMOMDonchianSkill.SETUP_ID,
        )
        return runner
    except Exception as e:  # noqa: BLE001 — 셋업 실패는 fail-soft
        logger.error("[ShadowSetup] 의존성 생성 실패: %s — shadow 비활성", e)
        return None
