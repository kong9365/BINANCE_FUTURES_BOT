"""db/init_db.py 검증 — 전체 테이블 존재 + v3.1.1 스키마 검증.

명세서 §13 + 부록 E-7.
"""

from __future__ import annotations

import sqlite3

import pytest

from db.init_db import get_applied_versions, init_db

# §13-1, §13-2 + schema_migrations
V3_1_TABLES = {
    "trades",
    "shadow_decisions",
    "regime_history",
    "regime_candidates",
    "weekly_reports",
    "gpt_call_log",
    "system_health_log",
    "pair_whitelist_history",
    "macro_events_cache",
    "backtest_runs",
    "schema_migrations",
}

# 부록 E-7-2, E-7-3
V3_1_1_TABLES = {
    "capital_initial",
    "capital_daily_snapshot",
}

ALL_TABLES = V3_1_TABLES | V3_1_1_TABLES

# 부록 E-7-1 — trades 진입 시점 자본 상태 컬럼
V3_1_1_TRADES_COLUMNS = {
    "wallet_balance_at_entry",
    "available_at_entry",
    "locked_margin_at_entry",
}

# v3.1.2 — trades 거래소 주문 추적 컬럼
V3_1_2_TRADES_COLUMNS = {
    "entry_order_id",
    "sl_order_id",
    "tp_order_id",
    "trade_status",
}


@pytest.fixture()
def db_path(tmp_path):
    """임시 디렉토리에 초기화된 DB 경로."""
    return init_db(tmp_path / "bot.db")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_db_file_created(db_path):
    assert db_path.exists()


def test_all_tables_exist(db_path):
    conn = sqlite3.connect(db_path)
    try:
        names = _table_names(conn)
    finally:
        conn.close()
    missing = ALL_TABLES - names
    assert not missing, f"누락 테이블: {missing}"


def test_v3_1_1_trades_columns_exist(db_path):
    """부록 E-7-1: trades 에 진입 시점 자본 상태 컬럼이 추가됐는지."""
    conn = sqlite3.connect(db_path)
    try:
        cols = _column_names(conn, "trades")
    finally:
        conn.close()
    missing = V3_1_1_TRADES_COLUMNS - cols
    assert not missing, f"누락 trades 컬럼: {missing}"


def test_v3_1_columns_present(db_path):
    """§13-1: v3.1 신규 컬럼도 정상 존재."""
    conn = sqlite3.connect(db_path)
    try:
        cols = _column_names(conn, "trades")
    finally:
        conn.close()
    for col in ("regime", "cost_guard_ev", "sizing_kelly_raw", "pnl_usd_net"):
        assert col in cols, f"v3.1 컬럼 누락: {col}"


def test_v3_1_2_trades_columns_exist(db_path):
    """v3.1.2: trades 에 거래소 주문 추적 컬럼이 추가됐는지."""
    conn = sqlite3.connect(db_path)
    try:
        cols = _column_names(conn, "trades")
    finally:
        conn.close()
    missing = V3_1_2_TRADES_COLUMNS - cols
    assert not missing, f"누락 v3.1.2 trades 컬럼: {missing}"


def test_migrations_registered(db_path):
    """schema_migrations 에 v3.1, v3.1.1, v3.1.2 등록 확인."""
    conn = sqlite3.connect(db_path)
    try:
        versions = get_applied_versions(conn)
    finally:
        conn.close()
    assert "v3.1" in versions
    assert "v3.1.1" in versions
    assert "v3.1.2" in versions


def test_indexes_exist(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        idx = {r[0] for r in rows}
    finally:
        conn.close()
    for expected in (
        "idx_trades_timestamp",
        "idx_regime_history_ts",
        "idx_gpt_log_ts",
        "idx_capital_initial_active",
        "idx_capital_daily_date",
    ):
        assert expected in idx, f"누락 인덱스: {expected}"


def test_idempotent_reinit(tmp_path):
    """init_db 2회 실행해도 에러 없고 버전 중복 없음 (재실행 안전)."""
    path = tmp_path / "bot.db"
    init_db(path)
    init_db(path)  # 재실행 — ALTER TABLE 중복 없이 통과해야 함

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    finally:
        conn.close()
    versions = [r[0] for r in rows]
    assert versions.count("v3.1") == 1
    assert versions.count("v3.1.1") == 1
    assert versions.count("v3.1.2") == 1
