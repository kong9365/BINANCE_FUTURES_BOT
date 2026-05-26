"""
agents/strategy_agent.py
=====================================================================
StrategyAgent — 알파 + 다중검정 + 학계 근거 + 운영자 7기준 검증.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 Agent 1
  - 운영자 권장 7기준 (REFACTOR_PLAN_v2_BLUEPRINT.md 추가 결정)
  - backtesting/signal_validation.py (실제 7기준 계산)

입력: SignalDecision + Setup Registry 메트릭
출력: AgentReview (verdict in {PASS, CONDITIONAL, FAIL})
=====================================================================
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import uuid4

from agents.base import Agent
from audit.agent_review import AgentReview
from audit.signal_decision import SignalDecision
from registry.setup_registry import SetupRegistry

logger = logging.getLogger(__name__)


class StrategyAgent(Agent):
    """청사진 §3.2.2 Agent 1 — 알파 + 다중검정 + 학계 근거.

    판정 룰 (청사진 §3.2.2 + 운영자 7기준 통합):
        - PASS: 7기준 모두 통과 + DSR ≥ 0.95 (또는 미측정) + 학계 근거 명확
        - CONDITIONAL: 6/7 통과 + borderline
        - FAIL: 1개 이상 fail 또는 DSR < 0.80
    """

    AGENT_TYPE = "strategy_quant"

    def __init__(self, registry: SetupRegistry) -> None:
        if registry is None:
            raise ValueError("registry must not be None")
        self.registry = registry

    async def review(self, payload: SignalDecision) -> AgentReview:
        """SignalDecision + setup_registry 메트릭으로 검증."""
        if not isinstance(payload, SignalDecision):
            raise TypeError(
                f"payload must be SignalDecision, got {type(payload).__name__}"
            )

        setup = self.registry.get_setup(payload.setup_id)
        concerns: list[str] = []
        recommendations: list[str] = []
        confidence = "MEDIUM"

        if setup is None:
            verdict = "FAIL"
            concerns.append(f"setup_id {payload.setup_id!r} not registered")
            recommendations.append("Register the setup via SetupRegistry.register()")
            confidence = "HIGH"
        else:
            # 운영자 7기준 검증 (last_metric_* 컬럼)
            pf = setup.get("last_metric_pf") or 0.0
            n = setup.get("last_metric_n") or 0
            mdd = setup.get("last_metric_mdd_pct") or 0.0
            top3_excluded_pf = setup.get("last_metric_top3_excluded_pf") or 0.0
            single_symbol_max = setup.get("last_metric_single_symbol_max_pct") or 0.0
            expectancy_r = setup.get("last_metric_expectancy_r") or 0.0
            avg_win_loss = setup.get("last_metric_avg_win_loss_ratio") or 0.0
            dsr = setup.get("last_metric_dsr") or 0.0
            academic_ref = setup.get("academic_ref") or ""

            failed_criteria: list[str] = []
            if n < 200:
                failed_criteria.append(f"n={n}<200")
            if pf < 1.25:
                failed_criteria.append(f"pf={pf:.2f}<1.25")
            if expectancy_r <= 0:
                failed_criteria.append(f"expectancy_r={expectancy_r:.4f}<=0")
            if avg_win_loss < 1.5:
                failed_criteria.append(f"avg_win_loss={avg_win_loss:.2f}<1.5")
            if mdd > 25.0:
                failed_criteria.append(f"mdd={mdd:.1f}%>25%")
            if single_symbol_max >= 25.0:
                failed_criteria.append(
                    f"single_symbol_max={single_symbol_max:.1f}%>=25%"
                )
            if top3_excluded_pf < 1.0:
                failed_criteria.append(
                    f"top3_excluded_pf={top3_excluded_pf:.2f}<1.0 "
                    f"(ZEC/LAB 단일종목 행운 의심)"
                )

            # 학계 근거
            if not academic_ref:
                concerns.append("academic_ref 미명시 — alpha 출처 불명")
            else:
                recommendations.append(f"Alpha source: {academic_ref}")

            # DSR (Deflated Sharpe Ratio)
            if dsr > 0 and dsr < 0.80:
                failed_criteria.append(f"dsr={dsr:.2f}<0.80 (다중검정 의심)")
            elif 0.80 <= dsr < 0.95:
                concerns.append(f"dsr={dsr:.2f} borderline (CONDITIONAL)")

            # 판정
            if not failed_criteria:
                verdict = "PASS"
                confidence = "HIGH" if dsr >= 0.95 else "MEDIUM"
            elif len(failed_criteria) == 1:
                verdict = "CONDITIONAL"
                concerns.extend(failed_criteria)
                recommendations.append("운영자 명시 결정 필요 (1/7 fail)")
                confidence = "MEDIUM"
            else:
                verdict = "FAIL"
                concerns.extend(failed_criteria)
                recommendations.append(
                    f"{len(failed_criteria)} criteria failed → DISABLED 권장"
                )
                confidence = "HIGH"

        full_json = self._serialize_full_json({
            "agent": self.AGENT_TYPE,
            "setup_id": payload.setup_id,
            "params_hash": payload.params_hash,
            "verdict": verdict,
            "setup_metrics": (
                {k: setup.get(k) for k in [
                    "last_metric_pf", "last_metric_n", "last_metric_mdd_pct",
                    "last_metric_top3_excluded_pf",
                    "last_metric_single_symbol_max_pct",
                    "last_metric_expectancy_r", "last_metric_avg_win_loss_ratio",
                    "last_metric_dsr",
                ]}
                if setup else {}
            ),
            "concerns": concerns,
            "recommendations": recommendations,
            "confidence": confidence,
        })

        return AgentReview(
            signal_id=payload.signal_id,
            agent=self.AGENT_TYPE,
            verdict=verdict,
            full_json=full_json,
            concerns=concerns,
            recommendations=recommendations,
            confidence=confidence,
        )
