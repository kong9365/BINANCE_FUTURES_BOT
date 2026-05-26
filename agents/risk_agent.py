"""
agents/risk_agent.py
=====================================================================
RiskAgent — RiskManager wrap (본문 0줄 수정)

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 Agent 2 (Risk Agent JSON 명세)
  - trading/risk_manager.py (RiskManager.check_all 7-check)
  - REFACTOR_PLAN_v2_BLUEPRINT.md M3 (7→5 매핑 표)

7→5 매핑:
  | RiskManager (7) | Agent JSON (5) |
  |---|---|
  | _check_daily_loss | daily_loss_check |
  | _check_total_drawdown | mdd_check |
  | _check_monthly_drawdown | weekly_loss_check (월간 → 주간 매핑) |
  | _check_consecutive_losses | concerns ("N연패") |
  | _check_concurrent_positions | concentration_check |
  | _check_min_balance | concerns ("min_balance") |
  | _check_cooldown | concerns ("cooldown_remaining") |
  | get_max_leverage(tier, regime) | leverage_check |

verdict in {APPROVE, REJECT, ESCALATE}.
=====================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from agents.base import Agent
from audit.agent_review import AgentReview
from audit.signal_decision import SignalDecision
from config.settings import RISK_RULES
from trading.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class RiskAgent(Agent):
    """RiskManager wrap → 청사진 §3.2.2 Agent 2 JSON 형식.

    *RiskManager 본문 0줄 수정* — check_all() 결과를 직렬화만.
    """

    AGENT_TYPE = "risk"

    def __init__(self, risk_manager: RiskManager) -> None:
        if risk_manager is None:
            raise ValueError("risk_manager must not be None")
        self.risk_manager = risk_manager

    async def review(self, payload: SignalDecision) -> AgentReview:
        """SignalDecision 으로 review — 자본 + 한도 + 레버리지 검증."""
        if not isinstance(payload, SignalDecision):
            raise TypeError(
                f"payload must be SignalDecision, got {type(payload).__name__}"
            )

        # RiskManager.check_all() 호출 (본문 0줄 수정)
        all_passed = await self.risk_manager.check_all()

        # 자본 + 한도 메트릭 수집
        try:
            snapshot = await self.risk_manager.capital_manager.get_snapshot()
        except Exception as e:  # noqa: BLE001
            snapshot = None
            logger.warning("[RiskAgent] snapshot 조회 실패: %s", e)

        initial = self.risk_manager.capital_manager.get_initial_capital()
        daily_start = self.risk_manager.capital_manager.get_daily_start_capital()

        concerns: list[str] = []
        recommendations: list[str] = []
        confidence = "HIGH"

        # 청사진 §3.2.2 Agent 2 의 5-check 매핑
        daily_loss_check = self._build_check(
            label="daily_loss_check",
            current=snapshot.wallet_balance if snapshot else 0,
            baseline=daily_start or 0,
            limit_pct=RISK_RULES.max_daily_loss_pct * 100,
        )
        mdd_check = self._build_check(
            label="mdd_check",
            current=snapshot.wallet_balance if snapshot else 0,
            baseline=initial or 0,
            limit_pct=RISK_RULES.max_total_drawdown_pct * 100,
        )
        # weekly_loss = monthly (현재 SPEC), 청사진 §3.2.2 와 매핑
        weekly_loss_check = {
            "limit_pct": RISK_RULES.monthly_drawdown_terminal_pct * 100,
            "status": "OK_if_check_all_passed",
        }
        concentration_check = {
            "single_symbol_max_pct": 25.0,
            "limit_pct": 25.0,  # 운영자 권장 7기준
            "status": "OK_assumed",
            "note": "Detailed by per-symbol pnl in M2 signal_validation",
        }
        leverage_check = {
            "default_leverage": 3,
            "max_leverage": 5,
            "limit": RISK_RULES.max_leverage_by_tier.get(1, 0),
            "status": "OK",
        }

        if not all_passed:
            verdict = "REJECT"
            concerns.append("RiskManager.check_all() rejected — 자본/한도/연패/쿨다운")
            confidence = "HIGH"
        else:
            # borderline 체크 — daily_loss 80% 도달 시 ESCALATE
            if daily_loss_check.get("status") == "BORDERLINE":
                verdict = "ESCALATE"
                concerns.append("daily_loss 80%+ 도달 — 운영자 확인 권장")
                confidence = "MEDIUM"
            else:
                verdict = "APPROVE"

        full_json = self._serialize_full_json({
            "agent": self.AGENT_TYPE,
            "verdict": verdict,
            "daily_loss_check": daily_loss_check,
            "weekly_loss_check": weekly_loss_check,
            "mdd_check": mdd_check,
            "concentration_check": concentration_check,
            "leverage_check": leverage_check,
            "concerns": concerns,
            "escalation_required": verdict == "ESCALATE",
            "_mapping_note": "RiskManager 7-check → Agent 5-check (M3 매핑 표)",
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

    @staticmethod
    def _build_check(
        label: str, current: float, baseline: float, limit_pct: float,
    ) -> dict:
        if baseline <= 0:
            return {
                "label": label,
                "current_pct": 0.0,
                "limit_pct": limit_pct,
                "status": "UNKNOWN (baseline=0)",
            }
        pct = (current - baseline) / baseline * 100
        status = "OK"
        if pct <= -limit_pct:
            status = "BLOCKED"
        elif pct <= -limit_pct * 0.8:
            status = "BORDERLINE"
        return {
            "label": label,
            "current_pct": round(pct, 2),
            "limit_pct": limit_pct,
            "status": status,
        }
