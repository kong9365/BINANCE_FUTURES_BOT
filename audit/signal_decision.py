"""
audit/signal_decision.py
=====================================================================
SignalDecision — Strategy Skill 평가 결과의 ALCOA+ 완전 기록

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.1 (SignalDecision dataclass 명세)
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.1 (ALCOA+ 자동 추적 사례)
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.3 (Single-Position Rotation EV 1위)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2 (Strategy Skill wrapper 입력 구조)

설계 메모:
  - 모든 필드는 *발생 시점 불변*. 변경 시 새 signal_id 생성.
  - immutable=True 는 의도적 표식 (실제 강제는 audit_log SQLite trigger).
  - to_db_row() / from_db_row() 는 sqlite3 row 와 1:1 매핑.
=====================================================================
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

ActionType = Literal["LONG", "SHORT", "FLAT", "EXIT"]
StatusType = Literal[
    "GENERATED", "REVIEWED", "APPROVED", "REJECTED", "EXECUTED", "FAILED"
]
SignalSourceType = Literal["strategy_skill", "manual", "test", "backtest"]


@dataclass
class SignalDecision:
    """ALCOA+ 완전 호환 시그널 결정.

    필수 필드 (청사진 §6.1):
        signal_id: UUID (Attributable)
        setup_id: "1d_tsmom_donchian_long_v1" 등 (Attributable)
        params_hash: sha256 of PARAMS (Consistent — 재현성)
        signal_source: "strategy_skill" / "manual" / "test"
        reasoning: 자연어 (Legible)
        ts_signal_generated: 생성 시각 (Contemporaneous)
        raw_data_hash: sha256 of input candles (Original)
        features_snapshot_json: 의사결정 시점 features (Original + Complete)
        symbol, action, confidence: 결정
        ev_estimated, rank_in_universe: Single-Position Rotation
        expires_at: 만료 시각
        status: 생애주기

    선택 필드:
        claude_session_id: Claude Code 세션 추적
        cost_estimate_json: 4-case 비용 분포
        ts_db_recorded: DB 적재 시각 (자동)
    """

    # === 필수 ===
    setup_id: str
    params_hash: str
    signal_source: SignalSourceType
    reasoning: str
    ts_signal_generated: datetime
    raw_data_hash: str
    features_snapshot_json: str
    symbol: str
    action: ActionType
    confidence: float
    expires_at: datetime

    # === 기본값 있는 필드 ===
    signal_id: str = field(default_factory=lambda: str(uuid4()))
    claude_session_id: Optional[str] = None
    cost_estimate_json: Optional[str] = None
    ev_estimated: float = 0.0
    rank_in_universe: int = 0
    ts_db_recorded: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    status: StatusType = "GENERATED"
    immutable: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0~1.0, got {self.confidence}")
        if not self.signal_id:
            raise ValueError("signal_id must not be empty")
        # tz-aware 강제 (CLAUDE.md TIER 2)
        if self.ts_signal_generated.tzinfo is None:
            raise ValueError("ts_signal_generated must be tz-aware (UTC)")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be tz-aware (UTC)")
        if self.ts_db_recorded.tzinfo is None:
            self.ts_db_recorded = self.ts_db_recorded.replace(tzinfo=timezone.utc)

    def to_db_row(self) -> dict:
        """sqlite3 INSERT 용 dict (signal_decisions 테이블 컬럼 매핑)."""
        return {
            "signal_id": self.signal_id,
            "setup_id": self.setup_id,
            "params_hash": self.params_hash,
            "claude_session_id": self.claude_session_id,
            "signal_source": self.signal_source,
            "reasoning": self.reasoning,
            "ts_signal_generated": self.ts_signal_generated.isoformat(),
            "ts_db_recorded": self.ts_db_recorded.isoformat(),
            "raw_data_hash": self.raw_data_hash,
            "features_snapshot_json": self.features_snapshot_json,
            "symbol": self.symbol,
            "action": self.action,
            "confidence": self.confidence,
            "cost_estimate_json": self.cost_estimate_json,
            "ev_estimated": self.ev_estimated,
            "rank_in_universe": self.rank_in_universe,
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }

    def to_audit_payload(self) -> dict:
        """AuditLog 의 payload_json 입력 (SYSTEM_DESIGN_BLUEPRINT §6.1.to_audit_record)."""
        d = asdict(self)
        d["ts_signal_generated"] = self.ts_signal_generated.isoformat()
        d["ts_db_recorded"] = self.ts_db_recorded.isoformat()
        d["expires_at"] = self.expires_at.isoformat()
        return d

    def to_audit_json(self) -> str:
        """payload_hash 계산을 위한 *정규화된* JSON (정렬, 분리자 고정)."""
        return json.dumps(self.to_audit_payload(), sort_keys=True, separators=(",", ":"))
