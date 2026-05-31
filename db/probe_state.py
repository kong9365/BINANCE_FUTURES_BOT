"""
db/probe_state.py
=====================================================================
프로브 상태/카운트 sqlite 헬퍼 — **fail-soft, 거래 경로 외**.

probe_state 단일행 get-or-init(멱등) + oi_surge 체결/미체결 카운트.
모든 실패는 None 반환(예외 전파 0) — caller(probe_guard 평가)가:
  - probe_state None → 기간 자동정지 fail-open(skip).
  - count None → 데이터목표 자동정지 fail-open(skip).
계측/상태 조회 실패가 매매 루프를 깨면 안 된다(CLAUDE.md §4).
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def get_or_init_probe_state(
    db_path, *, initial_capital: float, budget_usdt: float, n_fill: int,
    n_unfill: int, max_weeks: int, now_iso: str,
) -> Optional[dict]:
    """probe_state 단일행(id=1) get-or-init(INSERT OR IGNORE → 멱등). dict 또는 None(실패).

    initial_capital 은 *첫 삽입에만* 기록(이후 IGNORE) → 재시작에도 프로브 시작 자본 불변.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO probe_state "
                "(id, probe_start_at, initial_capital_usdt, budget_usdt, n_fill_target, "
                " n_unfill_target, max_weeks, created_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso, initial_capital, budget_usdt, n_fill, n_unfill, max_weeks, now_iso),
            )
            conn.commit()
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM probe_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception as e:  # noqa: BLE001 — fail-soft(매매 무영향)
        logger.warning("[ProbeState] get_or_init 실패: %s", e)
        return None


def _count_setup(db_path, table: str, ts_col: str, setup_prefix: str,
                 since_iso: str) -> Optional[int]:
    """table 의 setup_tag LIKE prefix% AND ts_col >= since 행수. 실패 시 None.

    table/ts_col 은 *하드코드 상수*만(주입 아님). setup_prefix/since 는 파라미터 바인딩.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} "  # noqa: S608 — table/ts_col 하드코드 상수
                f"WHERE setup_tag LIKE ? AND {ts_col} >= ?",
                (setup_prefix + "%", since_iso),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:  # noqa: BLE001 — fail-soft
        logger.warning("[ProbeState] count(%s) 실패: %s", table, e)
        return None


def count_oi_surge_fills(db_path, since_iso: str) -> Optional[int]:
    """체결(진입) oi_surge trades 건수 (exit 무관, ts ≥ probe_start_at)."""
    return _count_setup(db_path, "trades", "timestamp", "oi_surge", since_iso)


def count_oi_surge_unfilled(db_path, since_iso: str) -> Optional[int]:
    """미체결 oi_surge unfilled_signals 건수 (ts ≥ probe_start_at)."""
    return _count_setup(db_path, "unfilled_signals", "ts", "oi_surge", since_iso)
