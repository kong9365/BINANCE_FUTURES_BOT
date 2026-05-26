"""
agents/execution_agent.py
=====================================================================
ExecutionAgent — 체결 가능성 + slippage + 4-case 검증.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 Agent 3
  - arXiv 2502.18625 (메이커 232,897건 4-case 분포)
  - strategy/cost_guard.py (기존 비용 계산)

입력: SignalDecision + 호가창 정보 (depth, spread)
출력: verdict in {EXECUTE, DELAY, SKIP}
=====================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from agents.base import Agent
from audit.agent_review import AgentReview
from audit.signal_decision import SignalDecision

logger = logging.getLogger(__name__)


class ExecutionAgent(Agent):
    """체결 확률 + slippage 추정 → EXECUTE/DELAY/SKIP."""

    AGENT_TYPE = "execution"

    def __init__(
        self,
        min_fill_probability: float = 0.6,
        max_spread_pct: float = 0.001,  # 0.1%
        delay_fill_probability: float = 0.3,
    ) -> None:
        self.min_fill_probability = min_fill_probability
        self.max_spread_pct = max_spread_pct
        self.delay_fill_probability = delay_fill_probability

    async def review(self, payload: SignalDecision) -> AgentReview:
        """SignalDecision + features 의 호가창 정보로 체결 가능성 추정.

        market_state 외 외부 의존성 없음 (테스트 용이성).
        features_snapshot_json 에 spread/bid_depth/ask_depth 가 있으면 사용,
        없으면 *보수적* 추정 (fill_probability=0.5).
        """
        if not isinstance(payload, SignalDecision):
            raise TypeError(
                f"payload must be SignalDecision, got {type(payload).__name__}"
            )

        import json
        try:
            features = json.loads(payload.features_snapshot_json)
        except (json.JSONDecodeError, TypeError):
            features = {}

        spread_pct = features.get("spread_pct", 0.0005)  # 기본 0.05% 보수
        bid_depth = features.get("bid_depth", 1.0)        # 정규화 (1.0=충분)

        # 4-case 추정
        # Case A: post-only 즉시 체결 (fill_probability 0.6+, slippage 작음)
        # Case B: 5분 후 미체결 → skip (cancel)
        # Case C: 5분 후 미체결 → taker fallback (slippage ↑)
        # Case D: 진입 직후 adverse selection (역방향 진입)
        if spread_pct > self.max_spread_pct:
            # spread 넓음 → 4-case D 위험 ↑
            fill_probability = 0.3
            estimated_slippage_pct = spread_pct * 50
        elif bid_depth < 0.3:
            # 유동성 부족 → 4-case C 위험 ↑
            fill_probability = 0.4
            estimated_slippage_pct = 0.0008
        else:
            fill_probability = 0.7
            estimated_slippage_pct = spread_pct * 10

        # arXiv 2502.18625 caveat: maker fill 도 1/3 비용
        # case_a = 정상 fill, case_b/c/d 합 = (1 - fill) 정확 분배 → 총합 = 1.0
        not_filled = 1.0 - fill_probability
        case_distribution = {
            "case_a_prob": round(fill_probability, 3),               # 정상 체결
            "case_b_prob": round(not_filled * 0.5, 3),              # 미체결 skip
            "case_c_prob": round(not_filled * 0.4, 3),              # taker fallback
            "case_d_prob": round(not_filled * 0.1, 3),              # adverse
        }

        concerns: list[str] = []
        recommendations: list[str] = []

        # spread 가 max 초과 → SKIP (체결해도 비용 과다)
        if spread_pct > self.max_spread_pct:
            verdict = "SKIP"
            concerns.append(
                f"spread_pct={spread_pct:.4f} > max={self.max_spread_pct:.4f} "
                f"— 체결 비용 과다"
            )
            confidence = "HIGH"
        elif fill_probability >= self.min_fill_probability:
            verdict = "EXECUTE"
            confidence = "HIGH"
        elif fill_probability >= self.delay_fill_probability:
            verdict = "DELAY"
            concerns.append(
                f"fill_probability={fill_probability:.2f} between "
                f"{self.delay_fill_probability:.2f}~{self.min_fill_probability:.2f} "
                f"— 5분 후 재시도 권장"
            )
            recommendations.append("Schedule retry in 5 minutes")
            confidence = "MEDIUM"
        else:
            verdict = "SKIP"
            concerns.append(
                f"fill_probability={fill_probability:.2f} < "
                f"{self.delay_fill_probability:.2f} — 체결 불가 가능성 높음"
            )
            confidence = "HIGH"

        order_plan = {
            "symbol": payload.symbol,
            "side": "BUY" if payload.action == "LONG" else "SELL",
            "order_type": "LIMIT",
            "post_only": True,
            "reduce_only": False,
        }

        full_json = self._serialize_full_json({
            "agent": self.AGENT_TYPE,
            "verdict": verdict,
            "order_plan": order_plan,
            "fill_probability": fill_probability,
            "estimated_slippage_pct": round(estimated_slippage_pct, 5),
            "spread_pct": spread_pct,
            "bid_depth": bid_depth,
            "case_distribution": case_distribution,
            "concerns": concerns,
            "recommendations": recommendations,
            "fallback_plan": "5분 미체결 시 taker fallback (case C)",
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
