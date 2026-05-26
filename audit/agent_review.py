"""
audit/agent_review.py
=====================================================================
AgentReview — 5-Agent Subagent 검토 결과 (M3 상세 구현)

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.3 (AgentReview dataclass)
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 (5-Agent 명세)

M1 시점에는 *데이터 구조*만 정의. 실제 Agent 구현은 M3.
=====================================================================
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

AgentType = Literal[
    "strategy_quant",     # Agent 1 (§3.2.2 Agent 1)
    "risk",               # Agent 2 — RiskManager wrap
    "execution",          # Agent 3
    "data_backtest",      # Agent 4
    "ops_observability",  # Agent 5 — SystemHealthMonitor wrap
]

ConfidenceLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class AgentReview:
    """5-Agent 검토 결과의 영속 기록.

    Verdict 값 (Agent별 상이):
        - strategy_quant: "PASS" / "CONDITIONAL" / "FAIL"
        - risk: "APPROVE" / "REJECT" / "ESCALATE"
        - execution: "EXECUTE" / "DELAY" / "SKIP"
        - data_backtest: "VALID" / "INVALID" / "STALE"
        - ops_observability: "HEALTHY" / "DEGRADED" / "CRITICAL"
    """

    # === 필수 ===
    signal_id: str
    agent: AgentType
    verdict: str
    full_json: str

    # === 기본값 ===
    review_id: str = field(default_factory=lambda: str(uuid4()))
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    concerns: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: ConfidenceLevel = "MEDIUM"

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            self.ts = self.ts.replace(tzinfo=timezone.utc)
        # full_json 이 유효한 JSON 인지 검증 (Legible)
        try:
            json.loads(self.full_json)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"full_json must be valid JSON: {e}")

    def to_db_row(self) -> dict:
        """sqlite3 INSERT 용 dict (agent_reviews 테이블)."""
        return {
            "review_id": self.review_id,
            "signal_id": self.signal_id,
            "agent": self.agent,
            "ts": self.ts.isoformat(),
            "verdict": self.verdict,
            "full_json": self.full_json,
            "concerns_json": json.dumps(self.concerns),
            "recommendations_json": json.dumps(self.recommendations),
            "confidence": self.confidence,
        }

    def to_audit_payload(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    def to_audit_json(self) -> str:
        return json.dumps(self.to_audit_payload(), sort_keys=True, separators=(",", ":"))
