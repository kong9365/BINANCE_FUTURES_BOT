"""
governance/shadow_runner.py
=====================================================================
ShadowAgentRunner — main_7590 통합 캡슐화 (M7).

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.3 (워크플로우 예시)
  - REFACTOR_PLAN_v2_BLUEPRINT.md M3 main_7590 통합
  - 운영자 결정 (M7): ENABLE_SHADOW_AGENTS env flag (기본 OFF — 안전)

설계:
  - main_7590 본문 변경 최소화 (1~3줄). 모든 shadow 로직은 본 모듈에 캡슐화.
  - candidate → SignalDecision 변환 + AgentOrchestrator review + audit_log
  - *shadow mode*: 실제 거래 결정에 영향 X. audit_log 적재만.
  - env OFF 면 no-op (성능 0 영향)
=====================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from agents.orchestrator import AgentOrchestrator, OrchestratorResult
from audit.audit_log import EventType
from audit.audit_logger import AuditLogger
from audit.signal_decision import SignalDecision
from governance.shadow_config import (
    ShadowAgentConfig, resolve_shadow_enabled as _resolve_shadow_enabled,
)

logger = logging.getLogger(__name__)


class ShadowAgentRunner:
    """main_7590 통합용 캡슐화.

    사용 in main_7590:
        self.shadow_runner = ShadowAgentRunner(
            orchestrator=self.orchestrator,
            audit_logger=self.audit_logger,
            cfg=SHADOW_AGENT_CONFIG,
        )
        # _handle_signal 끝:
        if self.shadow_runner.enabled:
            await self.shadow_runner.run_shadow(candidate, capital_snapshot)

    shadow review 결과는 *실거래 결정에 영향 X*. audit_log 적재만.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        audit_logger: Optional[AuditLogger] = None,
        cfg: Optional[ShadowAgentConfig] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.audit_logger = audit_logger
        self.cfg = cfg or ShadowAgentConfig()
        self._enabled_cached = _resolve_shadow_enabled(self.cfg.enabled)

    @property
    def enabled(self) -> bool:
        """env flag + cfg.enabled 결합."""
        return self._enabled_cached

    async def run_shadow(
        self,
        candidate: Any,
        capital_snapshot: Optional[Any] = None,
        setup_id: str = "1d_tsmom_donchian_long_v1",
        params_hash: str = "",
    ) -> Optional[OrchestratorResult]:
        """candidate → SignalDecision 변환 + orchestrator review.

        Args:
            candidate: main_7590 의 candidate object (symbol, action 등 속성).
            capital_snapshot: CapitalSnapshot (선택, audit 메타).
            setup_id: skill SETUP_ID (기본 DailyTSMOM).
            params_hash: skill PARAMS_HASH.

        Returns:
            OrchestratorResult — shadow 만, 실거래 영향 X.
        """
        if not self.enabled:
            return None

        # candidate → SignalDecision 변환
        try:
            decision = self._candidate_to_signal_decision(
                candidate, setup_id=setup_id, params_hash=params_hash,
            )
        except Exception as e:  # noqa: BLE001 — shadow 는 fail-safe
            logger.warning("[Shadow] candidate 변환 실패: %s", e)
            return None

        # SIGNAL_GENERATED 이벤트 기록
        if self.cfg.log_to_audit and self.audit_logger:
            try:
                await self.audit_logger.log_event(
                    event_type=EventType.SIGNAL_GENERATED,
                    payload=decision.to_audit_payload(),
                    actor="shadow_strategy_skill",
                    related_signal_id=decision.signal_id,
                    related_setup_id=decision.setup_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[Shadow] audit SIGNAL_GENERATED 실패: %s", e)

        # 5-Agent orchestrator review
        try:
            result = await self.orchestrator.review_signal(decision)
        except Exception as e:  # noqa: BLE001 — shadow 는 fail-safe
            logger.warning("[Shadow] orchestrator review 실패: %s", e)
            return None

        # SIGNAL_REVIEWED 이벤트 (요약)
        if self.cfg.log_to_audit and self.audit_logger:
            try:
                await self.audit_logger.log_event(
                    event_type=EventType.SIGNAL_REVIEWED,
                    payload={
                        "signal_id": decision.signal_id,
                        "approved": result.approved,
                        "final_verdict": result.final_verdict,
                        "blocked_at_agent": result.blocked_at_agent,
                        "n_reviews": len(result.reviews),
                    },
                    actor="shadow_orchestrator",
                    related_signal_id=decision.signal_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[Shadow] audit SIGNAL_REVIEWED 실패: %s", e)

        logger.info(
            "[Shadow] %s review=%s blocked_at=%s",
            decision.symbol, result.final_verdict, result.blocked_at_agent,
        )
        return result

    @staticmethod
    def _candidate_to_signal_decision(
        candidate: Any,
        setup_id: str,
        params_hash: str,
    ) -> SignalDecision:
        """main_7590 candidate → SignalDecision 변환.

        candidate 는 dict, namedtuple, 또는 dataclass 가능. 다음 속성 접근:
            symbol, action (선택 — 없으면 LONG), confidence (선택)
        """
        symbol = ShadowAgentRunner._get_attr(candidate, "symbol", "UNKNOWN")
        action = ShadowAgentRunner._get_attr(candidate, "action", "LONG")
        confidence = float(ShadowAgentRunner._get_attr(candidate, "confidence", 0.6))

        # 기본 features (운영자 환경에 따라 확장 가능)
        features = {
            "candidate_type": type(candidate).__name__,
            "shadow_source": "main_7590",
        }

        now = datetime.now(timezone.utc)
        return SignalDecision(
            signal_id=str(uuid4()),
            setup_id=setup_id,
            params_hash=params_hash or "shadow-no-hash",
            signal_source="strategy_skill",     # shadow 도 strategy_skill 분류
            reasoning=f"Shadow review of main_7590 candidate ({type(candidate).__name__})",
            ts_signal_generated=now,
            raw_data_hash="shadow-" + str(uuid4())[:16],
            features_snapshot_json=json.dumps(features, sort_keys=True),
            symbol=symbol,
            action=action,
            confidence=confidence,
            expires_at=now + timedelta(hours=1),
        )

    @staticmethod
    def _get_attr(obj: Any, name: str, default: Any) -> Any:
        """dict / object 양쪽 호환 속성 접근."""
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
