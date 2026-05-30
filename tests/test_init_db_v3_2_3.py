"""
tests/test_init_db_v3_2_3.py
=====================================================================
B7 [4-4] — v3.2.3 마이그레이션(unfilled_signals) 적용 검증.

init_db 가 unfilled_signals 테이블과 forward-return 컬럼을 만들고
schema_migrations 에 'v3.2.3' 을 등록하는지 확인한다.
"""

from __future__ import annotations

import sqlite3

from db.init_db import get_applied_versions, init_db


def test_v3_2_3_applied(tmp_path):
    """init_db 후 'v3.2.3' 이 적용 버전에 포함된다."""
    p = tmp_path / "bot.db"
    init_db(p)
    conn = sqlite3.connect(str(p))
    try:
        applied = get_applied_versions(conn)
    finally:
        conn.close()
    assert "v3.2.3" in applied


def test_unfilled_signals_table_and_columns(tmp_path):
    """unfilled_signals 테이블 + 핵심/forward-return 컬럼이 존재한다."""
    p = tmp_path / "bot.db"
    init_db(p)
    conn = sqlite3.connect(str(p))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(unfilled_signals)")}
    finally:
        conn.close()
    # 핵심 메타
    for c in ("ts", "symbol", "action", "setup_tag", "signal_price", "reason"):
        assert c in cols, f"누락 컬럼: {c}"
    # forward-return 채움 컬럼
    for c in ("fwd_return_1bar", "fwd_return_3bar", "fwd_return_6bar", "fwd_filled_at"):
        assert c in cols, f"누락 forward-return 컬럼: {c}"


def test_init_db_idempotent_with_v3_2_3(tmp_path):
    """init_db 두 번 호출해도 v3.2.3 재적용 없이 멱등."""
    p = tmp_path / "bot.db"
    init_db(p)
    init_db(p)   # 두 번째 호출 — 예외 없이 통과해야 함
    conn = sqlite3.connect(str(p))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 'v3.2.3'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1   # 중복 등록 없음
