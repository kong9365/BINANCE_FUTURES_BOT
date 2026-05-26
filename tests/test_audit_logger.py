"""
tests/test_audit_logger.py
=====================================================================
AuditLogger 검증 — append-only INSERT + chain hash 자동.

근거: audit/audit_logger.py + SYSTEM_DESIGN_BLUEPRINT.md §6.4 + §7.2
=====================================================================
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from audit.audit_log import EventType
from audit.audit_logger import AuditLogger
from audit.chain import verify_chain
from db.init_db import init_db


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "audit_test.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def logger(tmp_db):
    return AuditLogger(db_path=tmp_db)


@pytest.mark.asyncio
async def test_single_event_logged(logger, tmp_db):
    """단일 이벤트 INSERT 성공."""
    log = await logger.log_event(
        event_type=EventType.SYSTEM_START,
        payload={"version": "v3.2.0"},
        actor="test",
    )
    assert log.log_id
    assert log.payload_hash
    assert log.previous_log_hash is None  # 첫 row

    # DB 확인
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()
        assert row[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_chain_hash_links_consecutive_events(logger, tmp_db):
    """연속 INSERT 시 previous_log_hash = 직전 payload_hash."""
    log1 = await logger.log_event(
        event_type=EventType.SYSTEM_START,
        payload={"step": 1},
        actor="test",
    )
    log2 = await logger.log_event(
        event_type=EventType.SIGNAL_GENERATED,
        payload={"step": 2},
        actor="test",
    )
    log3 = await logger.log_event(
        event_type=EventType.SIGNAL_REVIEWED,
        payload={"step": 3},
        actor="test",
    )

    # chain 무결성
    assert log1.previous_log_hash is None
    assert log2.previous_log_hash == log1.payload_hash
    assert log3.previous_log_hash == log2.payload_hash

    # DB 에서 verify_chain 검증
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT payload_hash, previous_log_hash FROM audit_log "
            "ORDER BY ts ASC, log_id ASC"
        ).fetchall()
        row_dicts = [
            {"payload_hash": r[0], "previous_log_hash": r[1]} for r in rows
        ]
        valid, idx = verify_chain(row_dicts)
        assert valid, f"chain 깨짐 at index {idx}"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_invalid_event_type_rejected(logger):
    """청사진 §6.4 외 event_type 거부."""
    with pytest.raises(ValueError, match="event_type"):
        await logger.log_event(
            event_type="HACK_ATTEMPT",
            payload={"x": 1},
            actor="test",
        )


@pytest.mark.asyncio
async def test_non_dict_payload_rejected(logger):
    """payload 가 dict 아니면 거부."""
    with pytest.raises(TypeError):
        await logger.log_event(
            event_type=EventType.SYSTEM_START,
            payload="not a dict",  # type: ignore
            actor="test",
        )


@pytest.mark.asyncio
async def test_empty_actor_rejected(logger):
    """actor 가 빈 문자열이면 거부."""
    with pytest.raises(ValueError, match="actor"):
        await logger.log_event(
            event_type=EventType.SYSTEM_START,
            payload={"x": 1},
            actor="",
        )


@pytest.mark.asyncio
async def test_payload_hash_deterministic(logger, tmp_db):
    """동일 payload → 동일 payload_hash (Consistent)."""
    p = {"signal_id": "fixed", "action": "LONG"}
    log1 = await logger.log_event(
        event_type=EventType.SIGNAL_GENERATED,
        payload=p,
        actor="test",
    )
    log2 = await logger.log_event(
        event_type=EventType.SIGNAL_GENERATED,
        payload=p,
        actor="test",
    )
    # 동일 payload 면 hash 도 동일
    assert log1.payload_hash == log2.payload_hash


@pytest.mark.asyncio
async def test_related_ids_persisted(logger, tmp_db):
    """related_signal_id / related_position_id / related_setup_id 영속."""
    log = await logger.log_event(
        event_type=EventType.ORDER_PLACED,
        payload={"price": 142.5},
        actor="executor",
        related_signal_id="sig-abc",
        related_position_id="pos-123",
        related_setup_id="1d_tsmom_donchian_long_v1",
    )
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT related_signal_id, related_position_id, related_setup_id "
            "FROM audit_log WHERE log_id = ?",
            (log.log_id,),
        ).fetchone()
        assert row == ("sig-abc", "pos-123", "1d_tsmom_donchian_long_v1")
    finally:
        conn.close()
