"""
tests/test_expectancy.py
=====================================================================
ExpectancyAnalyzer 단위 테스트 — 3개 시나리오.

근거: docs/SPEC_v3.1.md §8-4 (ExpectancyAnalyzer 의존성)

구성:
  - DB는 tmp_path 임시 SQLite (db.init_db.init_db로 schema.sql + v3.1.1 적용)
  - trades 테이블에 가상 거래 삽입 후 집계 검증

3개 시나리오 expected:
  1. 거래 10건 (승 6 R=2.0 / 패 4 R=-1.0)
     → trade_count=10, win_rate=0.6, avg_R≈0.8, expectancy_R≈0.8, total_pnl=40
  2. 거래 0건 → default Stats (모든 필드 0)
  3. setup_tag별 분리 → TREND_PULLBACK 5건, RANGING_MR 3건
=====================================================================
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from analytics.expectancy import ExpectancyAnalyzer, Stats
from db.init_db import init_db


# ── fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> str:
    """schema.sql + v3.1.1 마이그레이션이 적용된 임시 DB 경로."""
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _insert_trade(
    conn: sqlite3.Connection,
    *,
    action: str = "LONG",
    entry: float = 100.0,
    exit_price: float = 102.0,
    stop: float = 99.0,
    net_pnl: float = 10.0,
    setup_tag: str = "TREND_PULLBACK",
    regime: str = "TREND_UP",
) -> None:
    """닫힌 거래 1건 삽입 (timestamp = 현재 UTC ISO)."""
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO trades "
        "(timestamp, symbol, action, entry_price, exit_price, quantity, "
        " stop_loss, pnl_usd, pnl_usd_net, setup_tag, regime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, "SOLUSDT", action, entry, exit_price, 1.0,
         stop, net_pnl, net_pnl, setup_tag, regime),
    )


# ── 시나리오 1: 10건 (승 6 / 패 4) → 통계 정확 ─────────────────────

def test_scenario_1_overall_stats(db_path):
    conn = sqlite3.connect(db_path)
    try:
        # 승 6건: LONG entry=100, stop=99 (risk=1), exit=102 (reward=2) → R=2.0
        for _ in range(6):
            _insert_trade(conn, exit_price=102.0, net_pnl=10.0)
        # 패 4건: LONG entry=100, stop=99 (risk=1), exit=99 (reward=-1) → R=-1.0
        for _ in range(4):
            _insert_trade(conn, exit_price=99.0, net_pnl=-5.0)
        conn.commit()
    finally:
        conn.close()

    stats = ExpectancyAnalyzer(db_path).overall(days=7)

    assert isinstance(stats, Stats)
    assert stats.trade_count == 10
    assert stats.win_rate == pytest.approx(0.6)
    # avg_R = (6*2.0 + 4*(-1.0)) / 10 = 0.8
    assert stats.avg_R == pytest.approx(0.8)
    assert stats.expectancy_R == pytest.approx(0.8)
    # total_pnl = 6*10 + 4*(-5) = 40
    assert stats.total_pnl_usdt == pytest.approx(40.0)


# ── 시나리오 2: 거래 0건 → default Stats ──────────────────────────

def test_scenario_2_empty_returns_default(db_path):
    stats = ExpectancyAnalyzer(db_path).overall(days=7)

    assert isinstance(stats, Stats)
    assert stats.trade_count == 0
    assert stats.win_rate == 0.0
    assert stats.avg_R == 0.0
    assert stats.expectancy_R == 0.0
    assert stats.total_pnl_usdt == 0.0


# ── 시나리오 3: setup_tag별 분리 ──────────────────────────────────

def test_scenario_3_by_setup_split(db_path):
    conn = sqlite3.connect(db_path)
    try:
        # TREND_PULLBACK 5건
        for _ in range(5):
            _insert_trade(conn, setup_tag="TREND_PULLBACK", exit_price=102.0, net_pnl=10.0)
        # RANGING_MR 3건
        for _ in range(3):
            _insert_trade(conn, setup_tag="RANGING_MR", exit_price=99.0, net_pnl=-5.0)
        conn.commit()
    finally:
        conn.close()

    by_setup = ExpectancyAnalyzer(db_path).by_setup(days=7)

    assert len(by_setup) == 2
    by_tag = {s.setup_tag: s for s in by_setup}
    assert set(by_tag) == {"TREND_PULLBACK", "RANGING_MR"}
    assert by_tag["TREND_PULLBACK"].trade_count == 5
    assert by_tag["TREND_PULLBACK"].win_rate == pytest.approx(1.0)
    assert by_tag["RANGING_MR"].trade_count == 3
    assert by_tag["RANGING_MR"].win_rate == pytest.approx(0.0)


# =====================================================================
# A4b [가드] — 합성 청산(_stop_fallback)을 win-rate/expectancy/avg_R 에서 제외
# =====================================================================


def _insert_stop_fallback_loss(conn: sqlite3.Connection, setup_tag="TREND_PULLBACK"):
    """청산가 미상으로 STOP가 합성 처리된 손실 행(exit_reason 에 _stop_fallback)."""
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO trades "
        "(timestamp, symbol, action, entry_price, exit_price, quantity, "
        " stop_loss, pnl_usd, pnl_usd_net, setup_tag, regime, exit_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, "SOLUSDT", "LONG", 100.0, 99.0, 1.0, 99.0, -1.0, -1.0,
         setup_tag, "TREND_UP", "exchange_stop_or_tp_stop_fallback"),
    )


def test_a4b_stop_fallback_excluded_from_overall(db_path):
    """합성 청산 손실은 overall 통계(trade_count/win_rate/avg_R)에서 제외."""
    conn = sqlite3.connect(db_path)
    try:
        _insert_trade(conn, exit_price=102.0, net_pnl=10.0)   # 정상 승
        _insert_trade(conn, exit_price=102.0, net_pnl=10.0)   # 정상 승
        _insert_stop_fallback_loss(conn)                       # 합성 손실 → 제외
        conn.commit()
    finally:
        conn.close()
    stats = ExpectancyAnalyzer(db_path).overall(days=7)
    assert stats.trade_count == 2                  # 합성 1건 제외
    assert stats.win_rate == pytest.approx(1.0)    # 합성 손실 빠져 승률 100%
    assert stats.avg_R == pytest.approx(2.0)       # 승 2건 R=2.0 만


def test_a4b_stop_fallback_excluded_from_get_win_rate(db_path):
    """get_win_rate/get_avg_R 도 합성 청산을 제외(사이징 입력 편향 방지)."""
    conn = sqlite3.connect(db_path)
    try:
        _insert_trade(conn, exit_price=102.0, net_pnl=10.0)   # 정상 승
        _insert_stop_fallback_loss(conn)                       # 합성 손실 → 제외
        conn.commit()
    finally:
        conn.close()
    wr, n = ExpectancyAnalyzer(db_path).get_win_rate("TREND_PULLBACK")
    assert n == 1                                  # 합성 제외 → 1건
    assert wr == pytest.approx(1.0)


def test_a4b_normal_exit_still_counted(db_path):
    """대조군: 합성 아닌 정상 손실(exit_reason 무관)은 그대로 집계."""
    conn = sqlite3.connect(db_path)
    try:
        _insert_trade(conn, exit_price=102.0, net_pnl=10.0)   # 승
        _insert_trade(conn, exit_price=99.0, net_pnl=-5.0)    # 정상 패 (exit_reason NULL)
        conn.commit()
    finally:
        conn.close()
    stats = ExpectancyAnalyzer(db_path).overall(days=7)
    assert stats.trade_count == 2                  # 둘 다 집계
    assert stats.win_rate == pytest.approx(0.5)
