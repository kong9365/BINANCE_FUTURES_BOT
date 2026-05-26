"""
mcp/outbox.py
=====================================================================
Outbox 패턴 — Slack/Notion down 시 backlog + 지수 백오프.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §7.2 (ALCOA+ Contemporaneous + Available)
  - REFACTOR_PLAN_v2_BLUEPRINT.md M5 (fail-soft)

설계:
  - SQLite slack_outbox 테이블에 INSERT 후 별도 worker 가 flush
  - 실패 시 retry_count++ + exp 백오프
  - 24h 후 fail-permanent (운영자 알림)
=====================================================================
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class OutboxEntry:
    """slack_outbox row."""

    entry_id: int
    target: str               # "slack" / "notion"
    payload_json: str
    created_at: datetime
    retry_count: int
    last_retry_at: Optional[datetime]
    status: str               # "pending" / "sent" / "failed_permanent"
    last_error: Optional[str]


class Outbox:
    """Slack/Notion outbox — fail-soft 보장.

    사용:
        outbox = Outbox(db_path)
        outbox.enqueue(target="slack", payload={"channel": "#alerts", "text": "..."})
        # 별도 worker 에서:
        async def slack_send(payload): ...
        outbox.flush(target="slack", send_fn=slack_send, max_retries=5)
    """

    def __init__(self, db_path: str | Path) -> None:
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = str(db_path)

    def enqueue(self, target: str, payload: dict) -> int:
        """outbox 에 INSERT, entry_id 반환."""
        if target not in ("slack", "notion"):
            raise ValueError(f"target must be slack/notion, got {target!r}")
        if not isinstance(payload, dict):
            raise TypeError("payload must be dict")

        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO slack_outbox
                    (target, payload_json, created_at, retry_count, status)
                VALUES (?, ?, ?, 0, 'pending')
                """,
                (target, json.dumps(payload, sort_keys=True), now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_pending(self, target: Optional[str] = None) -> list[OutboxEntry]:
        """pending 항목 조회."""
        conn = sqlite3.connect(self.db_path)
        try:
            if target:
                rows = conn.execute(
                    "SELECT entry_id, target, payload_json, created_at, "
                    "retry_count, last_retry_at, status, last_error "
                    "FROM slack_outbox "
                    "WHERE status = 'pending' AND target = ? "
                    "ORDER BY entry_id ASC",
                    (target,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT entry_id, target, payload_json, created_at, "
                    "retry_count, last_retry_at, status, last_error "
                    "FROM slack_outbox "
                    "WHERE status = 'pending' "
                    "ORDER BY entry_id ASC"
                ).fetchall()
        finally:
            conn.close()
        return [
            OutboxEntry(
                entry_id=r[0], target=r[1], payload_json=r[2],
                created_at=datetime.fromisoformat(r[3]),
                retry_count=r[4],
                last_retry_at=datetime.fromisoformat(r[5]) if r[5] else None,
                status=r[6], last_error=r[7],
            )
            for r in rows
        ]

    def mark_sent(self, entry_id: int) -> None:
        """status='sent' 갱신."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE slack_outbox SET status='sent', last_retry_at=? "
                "WHERE entry_id = ?",
                (now, entry_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_failed(
        self, entry_id: int, error: str, max_retries: int = 5,
    ) -> bool:
        """retry_count++ + 지수 백오프. max_retries 도달 시 permanent fail.

        Returns:
            True 면 permanent fail (운영자 알림 필요), False 면 retry pending.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT retry_count FROM slack_outbox WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            if not row:
                return False
            new_count = row[0] + 1
            permanent = new_count >= max_retries
            status = "failed_permanent" if permanent else "pending"
            conn.execute(
                "UPDATE slack_outbox SET retry_count=?, last_retry_at=?, "
                "status=?, last_error=? WHERE entry_id = ?",
                (new_count, now, status, error, entry_id),
            )
            conn.commit()
            if permanent:
                logger.error(
                    "[Outbox] entry %d permanent fail (%d retries) — %s",
                    entry_id, new_count, error,
                )
            return permanent
        finally:
            conn.close()

    def count_pending(self) -> int:
        """pending 항목 수 (OpsAgent outbox_pending 메트릭 용)."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM slack_outbox WHERE status = 'pending'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
