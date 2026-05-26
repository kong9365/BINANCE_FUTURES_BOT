"""
tests/test_aggregator_1m.py
=====================================================================
data/aggregator_1m.py — 1m OHLC/volume/CVD 집계기 단위 테스트.
=====================================================================
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from data.aggregator_1m import (
    Bucket1m,
    TABLE_AGG_1M,
    TradeAggregator1m,
    floor_to_minute,
)


_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


# ── floor_to_minute ──────────────────────────────────────
def test_floor_to_minute_basic():
    ts = datetime(2026, 5, 25, 12, 0, 45, 123456, tzinfo=timezone.utc)
    assert floor_to_minute(ts) == datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_floor_to_minute_exact_boundary():
    ts = datetime(2026, 5, 25, 12, 0, 0, 0, tzinfo=timezone.utc)
    assert floor_to_minute(ts) == ts


def test_floor_to_minute_59s():
    ts = datetime(2026, 5, 25, 12, 0, 59, 999999, tzinfo=timezone.utc)
    assert floor_to_minute(ts) == datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


# ── Bucket1m ─────────────────────────────────────────────
def test_bucket_first_trade_sets_OHLC():
    b = Bucket1m()
    b.update(100.0, 1.0, is_buyer_maker=False)
    assert b.open_price == 100.0
    assert b.high_price == 100.0
    assert b.low_price == 100.0
    assert b.close_price == 100.0
    assert b.volume == 1.0
    assert b.buy_volume == 1.0   # taker buy (not buyer-maker)
    assert b.sell_volume == 0.0
    assert b.n_trades == 1


def test_bucket_multiple_trades_OHLC():
    b = Bucket1m()
    b.update(100.0, 1.0, False)   # open=100, buy
    b.update(102.0, 0.5, True)    # high=102, sell
    b.update(99.0, 2.0, True)     # low=99, sell
    b.update(101.0, 0.3, False)   # close=101, buy
    assert b.open_price == 100.0
    assert b.high_price == 102.0
    assert b.low_price == 99.0
    assert b.close_price == 101.0
    assert b.volume == pytest.approx(3.8)
    assert b.buy_volume == pytest.approx(1.3)    # 1.0 + 0.3
    assert b.sell_volume == pytest.approx(2.5)   # 0.5 + 2.0
    assert b.n_trades == 4
    # CVD via to_row
    row = b.to_row("BTCUSDT", "2026-05-25T12:00:00+00:00")
    assert row["cvd_delta"] == pytest.approx(1.3 - 2.5)


def test_bucket_notional_accumulates():
    b = Bucket1m()
    b.update(50000.0, 0.1, False)   # 5000
    b.update(50100.0, 0.2, True)    # 10020
    assert b.notional_usd == pytest.approx(15020.0)


# ── TradeAggregator1m ────────────────────────────────────
@pytest.fixture
def mock_persist():
    p = MagicMock()
    p.upsert_many = MagicMock(return_value=True)
    return p


@pytest.mark.asyncio
async def test_record_trade_creates_bucket(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, _NOW)
    assert agg.n_pending == 1
    assert "BTCUSDT" in agg._buckets
    bucket_iso = _NOW.replace(second=0, microsecond=0).isoformat()
    assert bucket_iso in agg._buckets["BTCUSDT"]


@pytest.mark.asyncio
async def test_record_trade_same_bucket_accumulates(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    ts1 = _NOW.replace(second=10)
    ts2 = _NOW.replace(second=50)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, ts1)
    await agg.record_trade("BTCUSDT", 50100.0, 0.2, True, ts2)
    assert agg.n_pending == 1   # 같은 bucket
    bucket = list(agg._buckets["BTCUSDT"].values())[0]
    assert bucket.n_trades == 2


@pytest.mark.asyncio
async def test_record_trade_different_minute_creates_separate_bucket(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    ts1 = _NOW.replace(second=30)             # 12:00
    ts2 = _NOW.replace(minute=1, second=10)   # 12:01
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, ts1)
    await agg.record_trade("BTCUSDT", 50100.0, 0.2, True, ts2)
    assert agg.n_pending == 2


@pytest.mark.asyncio
async def test_record_trade_zero_or_negative_ignored(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    await agg.record_trade("BTCUSDT", 0.0, 1.0, False, _NOW)
    await agg.record_trade("BTCUSDT", -1.0, 1.0, False, _NOW)
    await agg.record_trade("BTCUSDT", 100.0, 0.0, False, _NOW)
    await agg.record_trade("BTCUSDT", 100.0, -1.0, False, _NOW)
    assert agg.n_pending == 0


@pytest.mark.asyncio
async def test_flush_only_completed_buckets(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    # bucket 12:00 (완료될 것)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False,
                            _NOW.replace(second=30))
    # bucket 12:01 (진행 중)
    await agg.record_trade("BTCUSDT", 50100.0, 0.2, True,
                            _NOW.replace(minute=1, second=10))
    assert agg.n_pending == 2

    # now=12:01:45 → bucket 12:01 미만(12:00) 만 flush
    now = _NOW.replace(minute=1, second=45)
    flushed = await agg.flush_completed_buckets(now)
    assert flushed == 1
    assert agg.n_pending == 1   # 12:01 만 남음
    # upsert 호출 검증
    mock_persist.upsert_many.assert_called_once()
    args = mock_persist.upsert_many.call_args
    assert args[0][0] == TABLE_AGG_1M
    rows = args[0][1]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["n_trades"] == 1
    assert rows[0]["ts"] == "2026-05-25T12:00:00+00:00"


@pytest.mark.asyncio
async def test_flush_empty_returns_zero(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    n = await agg.flush_completed_buckets(_NOW + timedelta(minutes=10))
    assert n == 0
    mock_persist.upsert_many.assert_not_called()


@pytest.mark.asyncio
async def test_flush_all(mock_persist):
    agg = TradeAggregator1m(mock_persist)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, _NOW)
    await agg.record_trade("ETHUSDT", 3000.0, 1.0, True, _NOW)
    n = await agg.flush_all()
    assert n == 2
    assert agg.n_pending == 0


@pytest.mark.asyncio
async def test_flush_failure_does_not_raise(mock_persist):
    """upsert_many 예외 발생 시 swallow (outbox 폴백)."""
    mock_persist.upsert_many.side_effect = Exception("network down")
    agg = TradeAggregator1m(mock_persist)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, _NOW)
    n = await agg.flush_completed_buckets(_NOW + timedelta(minutes=10))
    assert n == 0   # 실패 시 0 반환


@pytest.mark.asyncio
async def test_write_sem_serializes_calls(mock_persist):
    """write_sem 이 주입되면 동시 호출 직렬화."""
    sem = asyncio.Semaphore(1)
    agg = TradeAggregator1m(mock_persist, write_sem=sem)
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, _NOW)
    n = await agg.flush_completed_buckets(_NOW + timedelta(minutes=10))
    assert n == 1
    # sem 이 release 됐는지 확인 (acquire 가능해야 함)
    assert sem.locked() is False


@pytest.mark.asyncio
async def test_source_stream_label():
    persist = MagicMock()
    persist.upsert_many = MagicMock(return_value=True)
    agg = TradeAggregator1m(persist, source_stream="aggTrade")
    await agg.record_trade("BTCUSDT", 50000.0, 0.1, False, _NOW)
    await agg.flush_all()
    rows = persist.upsert_many.call_args[0][1]
    assert rows[0]["source_stream"] == "aggTrade"


@pytest.mark.asyncio
async def test_to_row_has_all_required_columns():
    persist = MagicMock()
    persist.upsert_many = MagicMock(return_value=True)
    agg = TradeAggregator1m(persist)
    await agg.record_trade("BTCUSDT", 100.0, 1.0, False, _NOW)
    await agg.flush_all()
    row = persist.upsert_many.call_args[0][1][0]
    required = {"ts", "symbol", "open_price", "high_price", "low_price",
                 "close_price", "volume", "buy_volume", "sell_volume",
                 "n_trades", "notional_usd", "cvd_delta", "source_stream"}
    assert required.issubset(row.keys())


@pytest.mark.asyncio
async def test_n_flushed_total_increments():
    persist = MagicMock()
    persist.upsert_many = MagicMock(return_value=True)
    agg = TradeAggregator1m(persist)
    await agg.record_trade("BTCUSDT", 100.0, 1.0, False, _NOW)
    await agg.record_trade("ETHUSDT", 3000.0, 0.5, True, _NOW)
    assert agg.n_flushed_total == 0
    await agg.flush_all()
    assert agg.n_flushed_total == 2
