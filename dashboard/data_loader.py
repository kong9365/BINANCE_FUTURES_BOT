"""
dashboard/data_loader.py
=====================================================================
DB 쿼리 + Streamlit 캐시 (5초 주기) — 모든 페이지에서 공유.

근거:
  - 청사진 §6 (signal_decisions / agent_reviews / audit_log / setup_registry)
  - v3.2.0 마이그레이션 + v3.2.1 (slack_outbox / mcp_diff_log)

설계:
  - 모든 쿼리는 `@st.cache_data(ttl=5)` (5초 캐시 — DB 부하 ↓)
  - 읽기 전용 (SELECT only) — TIER 1 #10 정합
  - sqlite3 연결은 매 호출 즉시 close (long connection 회피)
=====================================================================
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

# Streamlit 의존성 (옵션) — pytest 시 mock 가능
try:
    import streamlit as st
    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


def _resolve_db_path() -> str:
    """USE_TESTNET 따라 DB 경로 결정 (config.settings 동일 로직)."""
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    use_testnet = os.environ.get("USE_TESTNET", "").strip().lower() in (
        "1", "true", "yes",
    )
    return "data/bot.db" if use_testnet else "data/bot_live.db"


def _cached(ttl: int = 5):
    """st.cache_data 또는 no-op (pytest 시)."""
    if _HAS_STREAMLIT:
        return st.cache_data(ttl=ttl)
    def _decorator(fn):
        return fn
    return _decorator


@contextmanager
def _connect(db_path: Optional[str] = None):
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@_cached(ttl=5)
def load_audit_log(db_path: Optional[str] = None, limit: int = 200) -> list[dict]:
    """audit_log 최근 N row (chain 검증용)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT log_id, ts, event_type, related_signal_id, related_setup_id, "
            "       actor, payload_hash, previous_log_hash "
            "FROM audit_log ORDER BY ts DESC, log_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_signal_decisions(db_path: Optional[str] = None, limit: int = 100) -> list[dict]:
    """signal_decisions 최근 N row."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT signal_id, setup_id, symbol, action, confidence, "
                "       ts_signal_generated, status, ev_estimated, reasoning "
                "FROM signal_decisions ORDER BY ts_signal_generated DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            # v3.2.0 마이그레이션 미적용
            return []
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_agent_reviews(db_path: Optional[str] = None, limit: int = 200) -> list[dict]:
    """agent_reviews 최근 N row."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT review_id, signal_id, agent, ts, verdict, confidence "
                "FROM agent_reviews ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_setup_registry(db_path: Optional[str] = None) -> list[dict]:
    """setup_registry 전체 + 7기준 메트릭."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT setup_id, name, category, status, last_evaluation_ts, "
                "       last_metric_n, last_metric_pf, last_metric_expectancy_r, "
                "       last_metric_avg_win_loss_ratio, last_metric_mdd_pct, "
                "       last_metric_single_symbol_max_pct, "
                "       last_metric_top3_excluded_pf, "
                "       last_metric_win_rate, last_metric_response_latency_p95_ms "
                "FROM setup_registry ORDER BY updated_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_kill_switch_events(db_path: Optional[str] = None, limit: int = 50) -> list[dict]:
    """kill_switch_events 이력."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT event_id, activated_at, deactivated_at, reason, source, deactivated_by "
                "FROM kill_switch_events ORDER BY activated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_trades(db_path: Optional[str] = None, limit: int = 100) -> list[dict]:
    """trades 최근 N row (운영 상태 페이지)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, timestamp, symbol, action, entry_price, exit_price, "
            "       quantity, leverage, pnl_usd, pnl_pct, fees_usd, "
            "       duration_seconds, exit_reason, regime, trade_status "
            "FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@_cached(ttl=5)
def load_capital_state(db_path: Optional[str] = None) -> Optional[dict]:
    """최근 capital_initial / capital_daily_snapshot (있다면)."""
    with _connect(db_path) as conn:
        # capital_initial
        try:
            row = conn.execute(
                "SELECT initial_wallet_balance, recorded_at FROM capital_initial "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            initial = dict(row) if row else None
        except sqlite3.OperationalError:
            initial = None
        # capital_daily_snapshot (v3.1.1 추가)
        try:
            row = conn.execute(
                "SELECT wallet_balance, available_balance, locked_margin, "
                "       margin_utilization_pct, snapshot_date "
                "FROM capital_daily_snapshot ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
            daily = dict(row) if row else None
        except sqlite3.OperationalError:
            daily = None
    if initial is None and daily is None:
        return None
    return {"initial": initial, "daily": daily}


@_cached(ttl=5)
def count_audit_events_by_type(db_path: Optional[str] = None) -> dict:
    """audit_log event_type 별 카운트 (대시보드 운영 상태 요약용)."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) FROM audit_log GROUP BY event_type"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {r[0]: r[1] for r in rows}


@_cached(ttl=5)
def get_db_path() -> str:
    """현재 사용 중인 DB 경로 (대시보드 표시용)."""
    return _resolve_db_path()
