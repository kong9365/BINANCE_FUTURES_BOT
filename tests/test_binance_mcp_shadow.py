"""tests/test_binance_mcp_shadow.py — Binance MCP shadow 검증."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db.init_db import init_db
from mcp.binance_mcp import BinanceMCPClient, MCPDiffResult


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "mcp_shadow.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def shadow_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_BINANCE_MCP_SHADOW", "true")


@pytest.fixture
def shadow_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_BINANCE_MCP_SHADOW", "false")


def test_is_shadow_enabled_default_off(monkeypatch):
    """env 미설정 → OFF."""
    monkeypatch.delenv("ENABLE_BINANCE_MCP_SHADOW", raising=False)
    assert BinanceMCPClient.is_shadow_enabled() is False


def test_is_shadow_enabled_true(shadow_enabled):
    assert BinanceMCPClient.is_shadow_enabled() is True


@pytest.mark.asyncio
async def test_compare_method_disabled_returns_matched(shadow_disabled, tmp_db):
    """shadow OFF → matched=True (no comparison)."""
    mcp_fn = AsyncMock(return_value={})
    client = BinanceMCPClient(db_path=tmp_db, mcp_call_fn=mcp_fn)
    result = await client.compare_method(
        method="futures_account",
        python_binance_response={"totalWalletBalance": "1000.50"},
    )
    assert result.matched is True
    mcp_fn.assert_not_called()  # shadow OFF → MCP 호출 X


@pytest.mark.asyncio
async def test_compare_method_matched(shadow_enabled, tmp_db):
    """동일 응답 → matched=True, diff < 1%."""
    mcp_fn = AsyncMock(return_value={
        "totalWalletBalance": "1000.50",
        "availableBalance": "950.00",
    })
    client = BinanceMCPClient(db_path=tmp_db, mcp_call_fn=mcp_fn)
    result = await client.compare_method(
        method="futures_account",
        python_binance_response={
            "totalWalletBalance": "1000.50",
            "availableBalance": "950.00",
        },
    )
    assert result.matched is True
    assert result.diff_pct == 0.0


@pytest.mark.asyncio
async def test_compare_method_diff_above_threshold(shadow_enabled, tmp_db):
    """5% diff → matched=False, mcp_diff_log 적재."""
    mcp_fn = AsyncMock(return_value={
        "totalWalletBalance": "1050.50",  # 5% 높음
        "availableBalance": "950.00",
    })
    client = BinanceMCPClient(db_path=tmp_db, mcp_call_fn=mcp_fn)
    result = await client.compare_method(
        method="futures_account",
        python_binance_response={
            "totalWalletBalance": "1000.50",
            "availableBalance": "950.00",
        },
    )
    assert result.matched is False
    assert result.diff_pct > 1.0

    # mcp_diff_log 적재 확인
    conn = sqlite3.connect(tmp_db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM mcp_diff_log WHERE matched = 0"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_compare_method_mcp_error(shadow_enabled, tmp_db):
    """MCP 호출 실패 → matched=False, error 기록."""
    mcp_fn = AsyncMock(side_effect=Exception("connection refused"))
    client = BinanceMCPClient(db_path=tmp_db, mcp_call_fn=mcp_fn)
    result = await client.compare_method(
        method="futures_account",
        python_binance_response={"totalWalletBalance": "1000.50"},
    )
    assert result.matched is False
    assert "connection refused" in result.error


@pytest.mark.asyncio
async def test_compare_method_no_mcp_fn(shadow_enabled, tmp_db):
    """mcp_call_fn=None → matched=False (호출 불가)."""
    client = BinanceMCPClient(db_path=tmp_db, mcp_call_fn=None)
    result = await client.compare_method(
        method="futures_account",
        python_binance_response={"x": 1},
    )
    assert result.matched is False
    assert "mcp_call_fn" in result.error
