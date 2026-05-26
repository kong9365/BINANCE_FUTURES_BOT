"""tests/test_dashboard_data_loader.py — DB 쿼리 단위 테스트."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from dashboard.data_loader import (
    _resolve_db_path,
    count_audit_events_by_type,
    get_db_path,
    load_agent_reviews,
    load_audit_log,
    load_capital_state,
    load_kill_switch_events,
    load_setup_registry,
    load_signal_decisions,
    load_trades,
)
from db.init_db import init_db


@pytest.fixture(autouse=True)
def _disable_cache(monkeypatch):
    """st.cache_data 미사용 (테스트는 매번 fresh)."""
    yield


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dashboard.db"
        init_db(db_path)
        yield str(db_path)


def test_resolve_db_path_testnet(monkeypatch):
    monkeypatch.setenv("USE_TESTNET", "true")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert _resolve_db_path() == "data/bot.db"


def test_resolve_db_path_live(monkeypatch):
    monkeypatch.setenv("USE_TESTNET", "false")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert _resolve_db_path() == "data/bot_live.db"


def test_resolve_db_path_env_override(monkeypatch):
    monkeypatch.setenv("DB_PATH", "/custom/path.db")
    assert _resolve_db_path() == "/custom/path.db"


def test_load_audit_log_empty(tmp_db):
    rows = load_audit_log(db_path=tmp_db, limit=10)
    assert rows == []


def test_load_signal_decisions_empty(tmp_db):
    rows = load_signal_decisions(db_path=tmp_db, limit=10)
    assert rows == []


def test_load_agent_reviews_empty(tmp_db):
    rows = load_agent_reviews(db_path=tmp_db, limit=10)
    assert rows == []


def test_load_setup_registry_empty(tmp_db):
    rows = load_setup_registry(db_path=tmp_db)
    assert rows == []


def test_load_kill_switch_events_empty(tmp_db):
    rows = load_kill_switch_events(db_path=tmp_db, limit=10)
    assert rows == []


def test_load_trades_empty(tmp_db):
    rows = load_trades(db_path=tmp_db, limit=10)
    assert rows == []


def test_count_audit_events_empty(tmp_db):
    counts = count_audit_events_by_type(db_path=tmp_db)
    assert counts == {}


def test_load_audit_log_with_data(tmp_db):
    """audit_log INSERT 후 load 검증."""
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute(
            """
            INSERT INTO audit_log
                (log_id, ts, event_type, actor, payload_json, payload_hash)
            VALUES
                ('t1', datetime('now'), 'SYSTEM_START', 'test', '{"x":1}', 'aaa')
            """
        )
        conn.commit()
    finally:
        conn.close()

    rows = load_audit_log(db_path=tmp_db, limit=10)
    assert len(rows) == 1
    assert rows[0]["log_id"] == "t1"
    assert rows[0]["event_type"] == "SYSTEM_START"


def test_count_audit_events_by_type(tmp_db):
    """event_type 별 카운트."""
    conn = sqlite3.connect(tmp_db)
    try:
        for i in range(3):
            conn.execute(
                "INSERT INTO audit_log (log_id, ts, event_type, actor, payload_json, payload_hash) "
                "VALUES (?, datetime('now'), 'SIGNAL_GENERATED', 'test', '{}', ?)",
                (f"s{i}", f"hash{i}"),
            )
        conn.execute(
            "INSERT INTO audit_log (log_id, ts, event_type, actor, payload_json, payload_hash) "
            "VALUES ('a1', datetime('now'), 'AGENT_REVIEW', 'test', '{}', 'a1h')"
        )
        conn.commit()
    finally:
        conn.close()
    counts = count_audit_events_by_type(db_path=tmp_db)
    assert counts == {"SIGNAL_GENERATED": 3, "AGENT_REVIEW": 1}


def test_load_capital_state_empty(tmp_db):
    """capital_initial / capital_daily_snapshot 비어있거나 미존재 — None."""
    result = load_capital_state(db_path=tmp_db)
    assert result is None or (
        result.get("initial") is None and result.get("daily") is None
    )
