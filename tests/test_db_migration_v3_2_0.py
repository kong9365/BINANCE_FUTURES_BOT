"""
tests/test_db_migration_v3_2_0.py
=====================================================================
DB 마이그레이션 v3.2.0 검증 — 청사진 §6 데이터 모델 6개 테이블 + trigger.

근거:
  - db/migrations/v3_1_2_to_v3_2_0.sql
  - SYSTEM_DESIGN_BLUEPRINT.md §6 + §7.2 (audit_log append-only)
=====================================================================
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db


@pytest.fixture
def tmp_db():
    """tempfile DB + 모든 마이그레이션 적용."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_v3_2_0.db"
        init_db(db_path)
        yield db_path


def test_v3_2_0_registered(tmp_db):
    """schema_migrations 에 v3.2.0 등록 확인."""
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        versions = [r[0] for r in rows]
        assert "v3.2.0" in versions, f"v3.2.0 미등록: {versions}"
    finally:
        conn.close()


@pytest.mark.parametrize(
    "table",
    [
        "setup_registry",
        "setup_evaluations",
        "signal_decisions",
        "agent_reviews",
        "audit_log",
        "kill_switch_events",
    ],
)
def test_new_tables_exist(tmp_db, table):
    """v3.2.0 6개 신규 테이블 모두 존재."""
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        assert row is not None, f"{table} 테이블이 없음"
    finally:
        conn.close()


def test_legacy_tables_intact(tmp_db):
    """기존 11개 테이블 그대로 작동 (회귀 0)."""
    conn = sqlite3.connect(tmp_db)
    try:
        legacy = [
            "trades", "shadow_decisions", "regime_history", "regime_candidates",
            "weekly_reports", "gpt_call_log", "system_health_log",
            "pair_whitelist_history", "macro_events_cache", "backtest_runs",
            "schema_migrations",
        ]
        for t in legacy:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (t,),
            ).fetchone()
            assert row is not None, f"기존 테이블 {t} 손상"
    finally:
        conn.close()


def test_audit_log_update_blocked(tmp_db):
    """audit_log UPDATE 시 trigger 가 차단 (ALCOA+ Accurate)."""
    conn = sqlite3.connect(tmp_db)
    try:
        # 테스트용 1 row INSERT
        conn.execute(
            """
            INSERT INTO audit_log
                (log_id, ts, event_type, actor, payload_json, payload_hash)
            VALUES
                ('test-1', datetime('now'), 'SYSTEM_START', 'test',
                 '{"k":"v"}', 'abc')
            """
        )
        conn.commit()

        # UPDATE 시도 → trigger ABORT
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE audit_log SET actor='hacker' WHERE log_id='test-1'"
            )
            conn.commit()
    finally:
        conn.close()


def test_audit_log_delete_blocked(tmp_db):
    """audit_log DELETE 시 trigger 가 차단."""
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute(
            """
            INSERT INTO audit_log
                (log_id, ts, event_type, actor, payload_json, payload_hash)
            VALUES
                ('test-del', datetime('now'), 'SYSTEM_START', 'test',
                 '{"k":"v"}', 'abc')
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM audit_log WHERE log_id='test-del'")
            conn.commit()
    finally:
        conn.close()


def test_idempotent_reinit(tmp_db):
    """init_db 재실행 안전 (멱등)."""
    # 1차 실행 후 한 번 더 실행
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        versions = [r[0] for r in rows]
        # 중복 INSERT 없음 (PK)
        assert versions.count("v3.2.0") == 1
    finally:
        conn.close()


def test_setup_registry_7_criteria_columns(tmp_db):
    """운영자 7기준 컬럼 모두 존재 (REFACTOR_PLAN_v2 추가 결정)."""
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute("PRAGMA table_info(setup_registry)").fetchall()
        cols = {r[1] for r in rows}  # 컬럼명만
        required = {
            "last_metric_n",
            "last_metric_pf",
            "last_metric_expectancy_r",
            "last_metric_avg_win_loss_ratio",
            "last_metric_mdd_pct",
            "last_metric_single_symbol_max_pct",
            "last_metric_top3_excluded_pf",  # ZEC 단일종목 행운 방어
        }
        missing = required - cols
        assert not missing, f"7기준 컬럼 누락: {missing}"
    finally:
        conn.close()
