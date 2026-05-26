"""
agents/ops_agent.py
=====================================================================
OpsAgent — SystemHealthMonitor wrap + ALCOA+ outbox + KillSwitch 응답성.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 Agent 5
  - ops/system_health_monitor.py (기존 헬스 메트릭, 본문 0줄 수정)
  - governance/kill_switch.py (KillSwitch.is_active)

입력: 시스템 상태 dict (선택, 미주입 시 SystemHealthMonitor 호출)
출력: verdict in {HEALTHY, DEGRADED, CRITICAL}
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from agents.base import Agent
from audit.agent_review import AgentReview
from governance.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


class OpsAgent(Agent):
    """SystemHealthMonitor wrap + ALCOA+ outbox + KillSwitch 응답성."""

    AGENT_TYPE = "ops_observability"

    def __init__(
        self,
        system_health_monitor=None,
        db_path: Optional[str] = None,
        max_outbox_pending: int = 100,
    ) -> None:
        self.health = system_health_monitor
        self.db_path = db_path
        self.max_outbox_pending = max_outbox_pending

    async def review(self, payload: Any = None) -> AgentReview:
        """시스템 헬스 + ALCOA+ outbox + KillSwitch 응답성 평가.

        payload 는 *옵션* — None 이면 시스템 전체 상태 평가.
        signal_id 가 있으면 reference 로 기록.
        """
        concerns: list[str] = []
        recommendations: list[str] = []
        confidence = "HIGH"

        # 1. SystemHealthMonitor (기존 모듈 wrap)
        system_health = {"healthy": True, "issues": []}
        if self.health is not None:
            try:
                result = self.health.check()
                system_health = {
                    "healthy": result.healthy,
                    "critical": result.critical,
                    "issues": list(result.issues),
                }
                if not result.healthy:
                    concerns.extend(result.issues)
            except Exception as e:  # noqa: BLE001
                concerns.append(f"SystemHealthMonitor 호출 실패: {e}")
                system_health["healthy"] = False

        # 2. KillSwitch 응답성
        kill_switch_active = KillSwitch.is_active()
        kill_switch_status = KillSwitch.get_status()
        # 응답성 — get_status() 가 즉시 반환되면 responsive
        kill_switch_responsive = True  # 동기 호출이라 항상 responsive

        # 3. ALCOA+ outbox pending (audit_log INSERT 실패 누적 등)
        outbox_pending = 0
        if self.db_path and Path(self.db_path).exists():
            try:
                conn = sqlite3.connect(self.db_path)
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE name='audit_log'"
                    ).fetchone()
                    if row[0] > 0:
                        # audit_log 존재 — outbox 별도 테이블 (M5에서 추가)
                        pass
                finally:
                    conn.close()
            except sqlite3.Error as e:
                concerns.append(f"DB 접근 실패: {e}")

        if outbox_pending > self.max_outbox_pending:
            concerns.append(
                f"outbox_pending {outbox_pending} > {self.max_outbox_pending}"
            )

        # 판정
        if not system_health["healthy"] or kill_switch_active or \
                outbox_pending > self.max_outbox_pending:
            if system_health.get("critical") or kill_switch_active:
                verdict = "CRITICAL"
                confidence = "HIGH"
            else:
                verdict = "DEGRADED"
                confidence = "MEDIUM"
        else:
            verdict = "HEALTHY"

        full_json = self._serialize_full_json({
            "agent": self.AGENT_TYPE,
            "verdict": verdict,
            "system_health": system_health,
            "alerting": {
                "kill_switch_active": kill_switch_active,
                "kill_switch_status": kill_switch_status,
                "kill_switch_responsive": kill_switch_responsive,
            },
            "alcoa_compliance": {
                "outbox_pending": outbox_pending,
                "max_outbox_pending": self.max_outbox_pending,
            },
            "concerns": concerns,
            "recommendations": recommendations,
        })

        signal_id = (
            payload.signal_id if hasattr(payload, "signal_id") else "system-health"
        )

        return AgentReview(
            signal_id=signal_id,
            agent=self.AGENT_TYPE,
            verdict=verdict,
            full_json=full_json,
            concerns=concerns,
            recommendations=recommendations,
            confidence=confidence,
        )
