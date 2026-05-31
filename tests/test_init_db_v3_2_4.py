"""tests/test_init_db_v3_2_4.py — Probe probe_state 마이그레이션(v3.2.4) 검증."""

from __future__ import annotations

import sqlite3

import pytest

from db.init_db import init_db

_COLS = {"id", "probe_start_at", "initial_capital_usdt", "budget_usdt",
         "n_fill_target", "n_unfill_target", "max_weeks", "created_at"}


def test_probe_state_table_created(tmp_path):
    p = tmp_path / "bot.db"
    init_db(p)
    conn = sqlite3.connect(p)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(probe_state)").fetchall()}
        assert _COLS <= cols
        vers = [r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()]
        assert "v3.2.4" in vers
    finally:
        conn.close()


def test_migration_idempotent(tmp_path):
    p = tmp_path / "bot.db"
    init_db(p)
    init_db(p)   # 2회 — 멱등
    conn = sqlite3.connect(p)
    try:
        vers = [r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()]
        assert vers.count("v3.2.4") == 1
    finally:
        conn.close()


def test_probe_state_single_row_check(tmp_path):
    """id=1 단일행 제약(CHECK id=1) — id≠1 삽입 거부."""
    p = tmp_path / "bot.db"
    init_db(p)
    conn = sqlite3.connect(p)
    ins = ("INSERT INTO probe_state (id, probe_start_at, initial_capital_usdt, budget_usdt, "
           "n_fill_target, n_unfill_target, max_weeks, created_at) "
           "VALUES (?, '2026-06-01', 200, 200, 30, 30, 4, '2026-06-01')")
    try:
        conn.execute(ins, (1,))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, (2,))
            conn.commit()
    finally:
        conn.close()


def test_v3_2_5_b3_columns(tmp_path):
    """v3.2.5: trades B-3 필드 + unfilled order_send_ts (additive, 멱등)."""
    p = tmp_path / "bot.db"
    init_db(p)
    init_db(p)   # 멱등
    conn = sqlite3.connect(p)
    try:
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(unfilled_signals)").fetchall()}
        assert {"entry_limit_price", "entry_order_send_ts"} <= tcols
        assert "order_send_ts" in ucols
        vers = [r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()]
        assert vers.count("v3.2.5") == 1
    finally:
        conn.close()
