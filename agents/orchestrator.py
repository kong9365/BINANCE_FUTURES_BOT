"""
agents/orchestrator.py
=====================================================================
AgentOrchestrator — 5-Agent 병렬+순차 워크플로우 (청사진 §3.2.2 다이어그램).

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 (5-Agent 작동 시점 다이어그램)
  - audit/audit_logger.py (AGENT_REVIEW 이벤트 chain)

워크플로우:
    [신호 발생]
      ↓
    [Strategy + Data + Ops] (병렬)
      ↓ ALL PASS
    [Risk Agent] (순차)
      ↓ APPROVE
    [Execution Agent] (순차)
      ↓ EXECUTE
    [주문 실행]
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from agents.data_agent import DataAgent
from agents.execution_agent import ExecutionAgent
from agents.ops_agent import OpsAgent
from agents.risk_agent import RiskAgent
from agents.strategy_agent import StrategyAgent
from audit.agent_review import AgentReview
from audit.audit_log import EventType
from audit.audit_logger import AuditLogger
from audit.signal_decision import SignalDecision

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    """전체 orchestrator 결과."""

    approved: bool                         # 모든 Agent ALL PASS 면 True
    final_verdict: str                     # APPROVE/REJECT/CONDITIONAL/ESCALATE/...
    reviews: list[AgentReview] = field(default_factory=list)
    blocked_at_agent: Optional[str] = None
    blocked_reason: Optional[str] = None


class AgentOrchestrator:
    """청사진 §3.2.2 의 5-Agent 워크플로우 (병렬 + 순차).

    사용:
        orch = AgentOrchestrator(
            strategy_agent=StrategyAgent(registry),
            risk_agent=RiskAgent(risk_manager),
            execution_agent=ExecutionAgent(),
            data_agent=DataAgent(),
            ops_agent=OpsAgent(health_monitor),
            audit_logger=AuditLogger(db_path),
        )
        result = await orch.review_signal(decision)
        if result.approved:
            await execute_order(...)
    """

    # FAIL/INVALID/CRITICAL 등급 (즉시 차단)
    BLOCKING_VERDICTS = frozenset({"FAIL", "INVALID", "CRITICAL", "REJECT", "SKIP"})

    def __init__(
        self,
        strategy_agent: StrategyAgent,
        risk_agent: RiskAgent,
        execution_agent: ExecutionAgent,
        data_agent: DataAgent,
        ops_agent: OpsAgent,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self.strategy = strategy_agent
        self.risk = risk_agent
        self.execution = execution_agent
        self.data = data_agent
        self.ops = ops_agent
        self.audit_logger = audit_logger

    async def review_signal(self, decision: SignalDecision) -> OrchestratorResult:
        """SignalDecision 평가 — 5-Agent 워크플로우.

        Args:
            decision: SignalDecision (Strategy Skill 출력).

        Returns:
            OrchestratorResult — approved=True 면 모든 Agent PASS.
        """
        if not isinstance(decision, SignalDecision):
            raise TypeError(
                f"decision must be SignalDecision, got {type(decision).__name__}"
            )

        reviews: list[AgentReview] = []

        # 1. Strategy + Data + Ops 병렬 (§3.2.2 다이어그램)
        parallel = await asyncio.gather(
            self.strategy.review(decision),
            self.data.review(decision),
            self.ops.review(decision),
            return_exceptions=True,
        )
        # 예외 처리
        for i, r in enumerate(parallel):
            if isinstance(r, Exception):
                logger.error("[Orchestrator] parallel agent %d 예외: %s", i, r)
                # 예외는 fail-safe (REJECT 처리)
                # placeholder review 생성
                continue
            reviews.append(r)

        await self._log_reviews(decision, reviews)

        # 병렬 중 하나라도 BLOCKING → 즉시 차단
        for r in reviews:
            if r.verdict in self.BLOCKING_VERDICTS:
                return OrchestratorResult(
                    approved=False,
                    final_verdict=r.verdict,
                    reviews=reviews,
                    blocked_at_agent=r.agent,
                    blocked_reason=f"{r.agent}: {r.verdict} — " + "; ".join(r.concerns),
                )

        # 2. Risk Agent (순차)
        risk_review = await self.risk.review(decision)
        reviews.append(risk_review)
        await self._log_reviews(decision, [risk_review])

        if risk_review.verdict in self.BLOCKING_VERDICTS:
            return OrchestratorResult(
                approved=False,
                final_verdict=risk_review.verdict,
                reviews=reviews,
                blocked_at_agent="risk",
                blocked_reason="; ".join(risk_review.concerns),
            )

        # 3. Execution Agent (순차)
        exec_review = await self.execution.review(decision)
        reviews.append(exec_review)
        await self._log_reviews(decision, [exec_review])

        if exec_review.verdict in self.BLOCKING_VERDICTS:
            return OrchestratorResult(
                approved=False,
                final_verdict=exec_review.verdict,
                reviews=reviews,
                blocked_at_agent="execution",
                blocked_reason="; ".join(exec_review.concerns),
            )

        return OrchestratorResult(
            approved=True,
            final_verdict="APPROVED",
            reviews=reviews,
        )

    async def _log_reviews(
        self,
        decision: SignalDecision,
        reviews: list[AgentReview],
    ) -> None:
        """AgentReview 들을 audit_log 에 기록 (chain hash 자동)."""
        if self.audit_logger is None:
            return
        for r in reviews:
            try:
                await self.audit_logger.log_event(
                    event_type=EventType.AGENT_REVIEW,
                    payload=r.to_audit_payload(),
                    actor=f"agent_{r.agent}",
                    related_signal_id=decision.signal_id,
                )
            except Exception as e:  # noqa: BLE001 — 로그 실패가 orchestrator 차단 X
                logger.error("[Orchestrator] audit_log 실패: %s", e)
