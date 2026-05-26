"""tests/test_local_ohlcv_store.py — 로컬 ohlcv 저장소 검증."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtesting.local_ohlcv_store import LocalOhlcvStore, _to_iso
from db.init_db import init_db


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ohlcv.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def store(tmp_db):
    return LocalOhlcvStore(db_path=tmp_db)


def _sample_candles(n: int = 10):
    """(ts, o, h, l, c, v) tuples."""
    return [
        (1704067200 + i * 86400, 100.0 + i, 101.0 + i, 99.0 + i,
         100.5 + i, 1000.0 + i * 10)
        for i in range(n)
    ]


def test_to_iso_int_seconds():
    assert _to_iso(1704067200).startswith("2024-01-01")


def test_to_iso_int_milliseconds():
    assert _to_iso(1704067200000).startswith("2024-01-01")


def test_to_iso_datetime():
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert _to_iso(dt) == "2024-01-01T00:00:00+00:00"


def test_to_iso_naive_datetime():
    dt = datetime(2024, 1, 1)
    assert _to_iso(dt).startswith("2024-01-01")


def test_no_db_path_rejected():
    with pytest.raises(ValueError, match="db_path"):
        LocalOhlcvStore(db_path="")


def test_upsert_empty_no_op(store):
    n = store.upsert_many("SOLUSDT", "1d", [])
    assert n == 0
    assert store.count() == 0


def test_upsert_tuple_rows(store):
    candles = _sample_candles(5)
    n = store.upsert_many("SOLUSDT", "1d", candles)
    assert n == 5
    assert store.count(symbol="SOLUSDT", interval="1d") == 5


def test_upsert_dict_rows(store):
    rows = [
        {"ts": 1704067200, "open": 100, "high": 102, "low": 99, "close": 101,
         "volume": 1000, "taker_buy_base": 500}
    ]
    n = store.upsert_many("AVAXUSDT", "1d", rows)
    assert n == 1
    candles = store.get_candles("AVAXUSDT", "1d", limit=10)
    assert len(candles) == 1


def test_upsert_idempotent(store):
    candles = _sample_candles(5)
    store.upsert_many("SOLUSDT", "1d", candles)
    # 같은 데이터 다시 → REPLACE (수 동일)
    store.upsert_many("SOLUSDT", "1d", candles)
    assert store.count(symbol="SOLUSDT", interval="1d") == 5


def test_get_candles_chronological_order(store):
    candles = _sample_candles(5)
    store.upsert_many("SOLUSDT", "1d", candles)
    result = store.get_candles("SOLUSDT", "1d", limit=10)
    # 시간 ASC
    ts_list = [r[5] for r in result]
    assert ts_list == sorted(ts_list)


def test_get_candles_limit(store):
    candles = _sample_candles(20)
    store.upsert_many("SOLUSDT", "1d", candles)
    result = store.get_candles("SOLUSDT", "1d", limit=5)
    # 최신 5개 (limit) 반환
    assert len(result) == 5


def test_get_candles_format_for_breakout(store):
    """evaluate_breakout 입력 형식 (o, h, l, c, v, ts) tuple."""
    candles = _sample_candles(5)
    store.upsert_many("SOLUSDT", "1d", candles)
    result = store.get_candles("SOLUSDT", "1d", limit=10)
    for r in result:
        assert len(r) == 6
        assert isinstance(r[0], (int, float))  # open
        assert isinstance(r[5], str)  # ts ISO


def test_get_symbols(store):
    store.upsert_many("SOLUSDT", "1d", _sample_candles(3))
    store.upsert_many("AVAXUSDT", "1d", _sample_candles(2))
    store.upsert_many("LINKUSDT", "1h", _sample_candles(5))
    symbols = store.get_symbols()
    assert set(symbols) == {"SOLUSDT", "AVAXUSDT", "LINKUSDT"}


def test_get_date_range_none_when_empty(store):
    r = store.get_date_range("SOLUSDT", "1d")
    assert r is None


def test_get_date_range_with_data(store):
    candles = _sample_candles(5)
    store.upsert_many("SOLUSDT", "1d", candles)
    r = store.get_date_range("SOLUSDT", "1d")
    assert r is not None
    min_ts, max_ts = r
    assert min_ts < max_ts


def test_v3_2_2_migration_applied(tmp_db):
    """ohlcv_local 테이블 + v3.2.2 마이그레이션 등록."""
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv_local'"
        ).fetchone()
        assert row is not None
        v = conn.execute(
            "SELECT version FROM schema_migrations WHERE version='v3.2.2'"
        ).fetchone()
        assert v is not None
    finally:
        conn.close()
