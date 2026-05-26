"""
tests/test_stage3_rule.py
=====================================================================
Stage 3 백스톱 검증 (90d -10% / MDD -25%).
=====================================================================
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.init_db import init_db
from governance.risk_hierarchy import RiskHierarchyConfig
from governance.stage3_rule import evaluate_stage3


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "stage3.db"
        init_db(db_path)
        yield str(db_path)


def _insert_trade(
    conn: sqlite3.Connection,
    symbol: str,
    pnl_usd: float,
    days_ago: int,
):
    """trades 테이블에 1 trade INSERT."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """
        INSERT INTO trades
            (timestamp, symbol, action, entry_price, exit_price,
             quantity, pnl_usd, pnl_usd_net)
        VALUES (?, ?, 'LONG', 100, 100, 1, ?, ?)
        """,
        (ts, symbol, pnl_usd, pnl_usd),
    )
    conn.commit()


def test_no_trades_not_triggered(tmp_db):
    """trades 없음 → 발동 X."""
    result = evaluate_stage3(tmp_db)
    assert result.triggered is False
    assert result.rolling_90d_pnl_pct == 0.0


def test_minor_loss_not_triggered(tmp_db):
    """-5% 손실 (< -10%) → 발동 X."""
    conn = sqlite3.connect(tmp_db)
    try:
        _insert_trade(conn, "SOLUSDT", -50.0, days_ago=30)
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db)
    assert result.triggered is False


def test_90d_pnl_breach_triggered(tmp_db):
    """90d 누적 -10%+ → 발동."""
    conn = sqlite3.connect(tmp_db)
    try:
        # -150 USDT (15% loss on 1000 base capital)
        _insert_trade(conn, "SOLUSDT", -100.0, days_ago=30)
        _insert_trade(conn, "AVAXUSDT", -50.0, days_ago=20)
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db)
    assert result.triggered is True
    assert "90d PnL" in result.reason
    assert "STAGE 3" in result.options_message


def test_90d_mdd_breach_triggered(tmp_db):
    """90d MDD > 25% → 발동 (큰 단일 trade)."""
    conn = sqlite3.connect(tmp_db)
    try:
        # 큰 손실 1건 → MDD 30%
        _insert_trade(conn, "SOLUSDT", -300.0, days_ago=20)
        _insert_trade(conn, "AVAXUSDT", +50.0, days_ago=10)  # 일부 회복
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db)
    assert result.triggered is True


def test_old_trades_excluded(tmp_db):
    """90일 이전 trade → 평가 제외."""
    conn = sqlite3.connect(tmp_db)
    try:
        # 100일 전 큰 손실 (90일 cutoff 초과)
        _insert_trade(conn, "OLDUSDT", -500.0, days_ago=100)
        # 30일 전 작은 손익
        _insert_trade(conn, "SOLUSDT", -10.0, days_ago=30)
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db)
    assert result.triggered is False  # 90일 내 -1% 만


def test_options_message_contains_abc(tmp_db):
    """options_message 에 A/B/C 옵션 모두 포함."""
    conn = sqlite3.connect(tmp_db)
    try:
        _insert_trade(conn, "SOLUSDT", -150.0, days_ago=30)
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db)
    assert result.triggered is True
    msg = result.options_message
    assert "A. Manual" in msg
    assert "B. Buy-and-hold" in msg
    assert "C. 운영자" in msg


def test_custom_threshold(tmp_db):
    """custom RiskHierarchyConfig 적용 (낮은 임계)."""
    cfg = RiskHierarchyConfig(
        stage3_rolling_90d_pct=0.05,    # -5% 만으로 trigger
        stage3_mdd_pct=0.10,
    )
    conn = sqlite3.connect(tmp_db)
    try:
        _insert_trade(conn, "SOLUSDT", -60.0, days_ago=30)  # -6%
    finally:
        conn.close()
    result = evaluate_stage3(tmp_db, cfg=cfg)
    assert result.triggered is True
