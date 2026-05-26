"""
agents/base.py
=====================================================================
Agent — 5-Agent Subagent 추상 base.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 (5-Agent 명세)
  - audit/agent_review.py (출력 형식)

설계 메모:
  - 자식 클래스가 AGENT_TYPE 정의 (audit.agent_review.AgentType literal 중 하나).
  - review(payload) 는 async — RiskAgent/OpsAgent 가 async 의존성 사용.
  - 출력: AgentReview (full_json 직렬화 필수).
=====================================================================
"""

from __future__ import annotations

import json
import logging
from typing import Any

from audit.agent_review import AgentReview

logger = logging.getLogger(__name__)


class Agent:
    """5-Agent abstract base.

    자식 클래스 정의 예시:
        class StrategyAgent(Agent):
            AGENT_TYPE = "strategy_quant"
            async def review(self, payload):
                ...
    """

    AGENT_TYPE: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__name__ != "Agent" and not cls.AGENT_TYPE:
            raise TypeError(
                f"{cls.__name__} must define AGENT_TYPE (audit.agent_review.AgentType)"
            )

    async def review(self, payload: Any) -> AgentReview:
        """자식 클래스가 override 필수.

        Args:
            payload: Agent별 다름 (SignalDecision / dict / list).

        Returns:
            AgentReview (signal_id, agent, verdict, full_json, concerns, recommendations).
        """
        raise NotImplementedError(
            f"{type(self).__name__}.review() must be implemented"
        )

    @staticmethod
    def _serialize_full_json(data: dict) -> str:
        """full_json 직렬화 (Legible)."""
        return json.dumps(data, sort_keys=True, separators=(",", ":"))
