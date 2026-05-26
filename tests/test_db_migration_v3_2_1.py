"""tests/test_db_migration_v3_2_1.py — slack_outbox + mcp_diff_log 검증."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "v3_2_1.db"
        init_db(db_path)
        yield str(db_path)


def test_v3_2_1_registered(tmp_db):
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        versions = [r[0] for r in rows]
        assert "v3.2.1" in versions
    finally:
        conn.close()


@pytest.mark.parametrize("table", ["slack_outbox", "mcp_diff_log"])
def test_new_tables_exist(tmp_db, table):
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_slack_outbox_columns(tmp_db):
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute("PRAGMA table_info(slack_outbox)").fetchall()
        cols = {r[1] for r in rows}
        required = {"entry_id", "target", "payload_json", "created_at",
                    "retry_count", "last_retry_at", "status", "last_error"}
        assert required <= cols
    finally:
        conn.close()


def test_mcp_diff_log_columns(tmp_db):
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute("PRAGMA table_info(mcp_diff_log)").fetchall()
        cols = {r[1] for r in rows}
        required = {"diff_id", "ts", "method", "matched", "diff_pct",
                    "python_binance_response", "mcp_response", "error"}
        assert required <= cols
    finally:
        conn.close()


def test_v3_2_0_still_present(tmp_db):
    """v3.2.0 (M1) 테이블들 그대로 작동."""
    conn = sqlite3.connect(tmp_db)
    try:
        for table in [
            "setup_registry", "setup_evaluations", "signal_decisions",
            "agent_reviews", "audit_log", "kill_switch_events",
        ]:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"M1 테이블 {table} 손상"
    finally:
        conn.close()


def test_idempotent_reinit(tmp_db):
    init_db(tmp_db)  # 2회 실행
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations WHERE version='v3.2.1'"
        ).fetchall()
        assert len(rows) == 1  # 중복 INSERT 없음
    finally:
        conn.close()
