"""
audit/audit_logger.py
=====================================================================
AuditLogger — append-only INSERT + chain hash 자동 계산

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.4 (AuditLog)
  - SYSTEM_DESIGN_BLUEPRINT.md §7.2 (ALCOA+ — Contemporaneous + Accurate + Enduring)
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.1 (Outbox 패턴)

설계 메모:
  - sqlite3 는 동기 라이브러리 → log_event() 는 async, 내부에서 to_thread 로 래핑.
  - chain hash 는 INSERT 직전에 직전 row 의 payload_hash 조회 후 계산.
  - 동시 INSERT 시 race 방지 위해 트랜잭션 + immediate transaction 사용.
  - 실패 시 *재시도* 가 아니라 *로그 + 예외 전파* (ALCOA+ Contemporaneous).
    재시도 큐는 M5 Outbox 패턴에서 구현 (Notion 전송 실패 등).
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from audit.audit_log import AuditLog, EventType
from audit.chain import compute_payload_hash

logger = logging.getLogger(__name__)


class AuditLogger:
    """ALCOA+ append-only AuditLog 기록기.

    사용:
        logger = AuditLogger(db_path="data/bot_live.db")
        await logger.log_event(
            event_type=EventType.SIGNAL_GENERATED,
            payload={"signal_id": "...", ...},
            actor="strategy_skill",
            related_signal_id="...",
        )
    """

    def __init__(self, db_path: str | Path) -> None:
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = str(db_path)

    async def log_event(
        self,
        event_type: str,
        payload: dict,
        actor: str,
        related_signal_id: Optional[str] = None,
        related_position_id: Optional[str] = None,
        related_setup_id: Optional[str] = None,
    ) -> AuditLog:
        """이벤트를 audit_log 에 append-only INSERT.

        Returns:
            방금 INSERT 된 AuditLog (log_id, payload_hash, previous_log_hash 포함).

        Raises:
            ValueError: event_type 미정의, payload 가 dict 아님 등 입력 오류.
            sqlite3.OperationalError: trigger 가 UPDATE/DELETE 시도 차단 등.
        """
        if event_type not in EventType.ALL:
            raise ValueError(f"event_type {event_type!r} not in EventType.ALL")
        if not isinstance(payload, dict):
            raise TypeError(f"payload must be dict, got {type(payload).__name__}")
        if not actor:
            raise ValueError("actor must not be empty")

        # 정규화된 payload JSON + hash 계산
        import json
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = compute_payload_hash(payload_json)

        # 직전 row 의 payload_hash 조회 + INSERT 를 한 트랜잭션에서 처리
        return await asyncio.to_thread(
            self._insert_with_chain,
            event_type=event_type,
            actor=actor,
            payload_json=payload_json,
            payload_hash=payload_hash,
            related_signal_id=related_signal_id,
            related_position_id=related_position_id,
            related_setup_id=related_setup_id,
        )

    def _insert_with_chain(
        self,
        event_type: str,
        actor: str,
        payload_json: str,
        payload_hash: str,
        related_signal_id: Optional[str],
        related_position_id: Optional[str],
        related_setup_id: Optional[str],
    ) -> AuditLog:
        """동기 INSERT (asyncio.to_thread 안에서 실행)."""
        conn = sqlite3.connect(self.db_path, isolation_level="IMMEDIATE")
        try:
            # 직전 row 의 payload_hash 조회 (chain seed)
            row = conn.execute(
                "SELECT payload_hash FROM audit_log "
                "ORDER BY ts DESC, log_id DESC LIMIT 1"
            ).fetchone()
            previous_log_hash = row[0] if row else None

            audit = AuditLog(
                event_type=event_type,
                actor=actor,
                payload_json=payload_json,
                payload_hash=payload_hash,
                related_signal_id=related_signal_id,
                related_position_id=related_position_id,
                related_setup_id=related_setup_id,
                previous_log_hash=previous_log_hash,
            )
            db_row = audit.to_db_row()

            conn.execute(
                """
                INSERT INTO audit_log
                    (log_id, ts, event_type, related_signal_id, related_position_id,
                     related_setup_id, actor, payload_json, payload_hash,
                     previous_log_hash)
                VALUES
                    (:log_id, :ts, :event_type, :related_signal_id, :related_position_id,
                     :related_setup_id, :actor, :payload_json, :payload_hash,
                     :previous_log_hash)
                """,
                db_row,
            )
            conn.commit()
            logger.info(
                "[Audit] %s logged — log_id=%s actor=%s",
                event_type, audit.log_id, actor,
            )
            return audit
        finally:
            conn.close()
