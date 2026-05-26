"""
audit/audit_log.py
=====================================================================
AuditLog — 모든 시스템 이벤트의 영속 기록 (blockchain-like chain hash)

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.4 (AuditLog dataclass)
  - SYSTEM_DESIGN_BLUEPRINT.md §7.2 (ALCOA+ Accurate — append-only)

DB 강제:
  - SQLite trigger 가 audit_log UPDATE/DELETE 차단 (v3.2.0 migration)
  - previous_log_hash 가 직전 row 의 payload_hash 와 일치해야 chain 유효
=====================================================================
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4


# 청사진 §6.4 의 event_type 16종
class EventType:
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_REVIEWED = "SIGNAL_REVIEWED"
    AGENT_REVIEW = "AGENT_REVIEW"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELED = "ORDER_CANCELED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    SL_UPDATED = "SL_UPDATED"
    TP_HIT = "TP_HIT"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    KILL_SWITCH_DEACTIVATED = "KILL_SWITCH_DEACTIVATED"
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    PHASE_TRANSITION = "PHASE_TRANSITION"
    SETUP_STATUS_CHANGE = "SETUP_STATUS_CHANGE"

    ALL = frozenset({
        SIGNAL_GENERATED, SIGNAL_REVIEWED, AGENT_REVIEW,
        ORDER_PLACED, ORDER_FILLED, ORDER_CANCELED,
        POSITION_OPENED, POSITION_CLOSED, SL_UPDATED, TP_HIT,
        KILL_SWITCH_ACTIVATED, KILL_SWITCH_DEACTIVATED,
        SYSTEM_START, SYSTEM_STOP, PHASE_TRANSITION, SETUP_STATUS_CHANGE,
    })


@dataclass
class AuditLog:
    """청사진 §6.4 의 AuditLog 영속 기록.

    chain 무결성:
        previous_log_hash = (직전 row 의 payload_hash). None 은 첫 row 만.
        payload_hash = sha256(payload_json) — chain.compute_payload_hash 계산.
    """

    # === 필수 ===
    event_type: str
    actor: str
    payload_json: str
    payload_hash: str

    # === 기본값 ===
    log_id: str = field(default_factory=lambda: str(uuid4()))
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    related_signal_id: Optional[str] = None
    related_position_id: Optional[str] = None
    related_setup_id: Optional[str] = None
    previous_log_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.event_type not in EventType.ALL:
            raise ValueError(
                f"event_type {self.event_type!r} not in {sorted(EventType.ALL)}"
            )
        if not self.actor:
            raise ValueError("actor must not be empty")
        if not self.payload_hash:
            raise ValueError("payload_hash must not be empty (ALCOA+ Original)")
        if self.ts.tzinfo is None:
            self.ts = self.ts.replace(tzinfo=timezone.utc)
        # payload_json 이 유효한 JSON 인지 (Legible)
        try:
            json.loads(self.payload_json)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"payload_json must be valid JSON: {e}")

    def to_db_row(self) -> dict:
        return {
            "log_id": self.log_id,
            "ts": self.ts.isoformat(),
            "event_type": self.event_type,
            "related_signal_id": self.related_signal_id,
            "related_position_id": self.related_position_id,
            "related_setup_id": self.related_setup_id,
            "actor": self.actor,
            "payload_json": self.payload_json,
            "payload_hash": self.payload_hash,
            "previous_log_hash": self.previous_log_hash,
        }
