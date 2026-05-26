"""
tests/test_ws_collector.py
=====================================================================
data/ws_collector.py — WS 메시지 파싱 / dedup / batch / latency 단위 테스트.

모든 테스트 mock — 실 Binance 연결 없음.
=====================================================================
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from data.ws_collector import (
    BATCH_MAX_AGE_SECONDS,
    BatchBuffer,
    CollectorStats,
    TABLE_AGG,
    TABLE_L2,
    TABLE_LIQ,
    WSCollector,
    WSCollectorConfig,
    compute_latency_ms,
    parse_agg_trade_message,
    parse_depth_message,
    parse_liquidation_message,
)


_NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)


# ── parse_depth_message ───────────────────────────────────────────
def test_parse_depth_diff_format():
    msg = {
        "E": 1748102400000,
        "b": [["50000.0", "1.5"], ["49999.5", "2.0"]],
        "a": [["50001.0", "0.8"], ["50001.5", "1.2"]],
    }
    row = parse_depth_message(msg, "BTCUSDT", _NOW)
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["bids"] == [[50000.0, 1.5], [49999.5, 2.0]]
    assert row["asks"] == [[50001.0, 0.8], [50001.5, 1.2]]
    assert row["mid_price"] == 50000.5
    # ts = bucket (truncated microsecond)
    assert ":00+00:00" in row["ts"]


def test_parse_depth_partial_format():
    """partialBookDepth 는 'bids'/'asks' 필드명 사용."""
    msg = {
        "T": 1748102400000,
        "bids": [["100.0", "1.0"]],
        "asks": [["101.0", "1.0"]],
    }
    row = parse_depth_message(msg, "SOLUSDT", _NOW)
    assert row is not None
    assert row["mid_price"] == 100.5


def test_parse_depth_missing_fields_returns_none():
    assert parse_depth_message({}, "X", _NOW) is None
    assert parse_depth_message({"E": 1, "b": []}, "X", _NOW) is None
    assert parse_depth_message({"E": 1, "b": [["1", "1"]], "a": []}, "X", _NOW) is None


def test_parse_depth_invalid_numbers_returns_none():
    msg = {"E": 1, "b": [["bad", "1"]], "a": [["1", "1"]]}
    assert parse_depth_message(msg, "X", _NOW) is None


def test_parse_depth_levels_clip():
    """levels=2 → top-2 만."""
    msg = {
        "E": 1,
        "b": [["100", "1"], ["99", "1"], ["98", "1"]],
        "a": [["101", "1"], ["102", "1"], ["103", "1"]],
    }
    row = parse_depth_message(msg, "X", _NOW, levels=2)
    assert len(row["bids"]) == 2
    assert len(row["asks"]) == 2


# ── parse_agg_trade_message ───────────────────────────────────────
def test_parse_agg_trade_basic():
    msg = {
        "e": "aggTrade", "E": 1748102400000, "s": "BTCUSDT",
        "a": 12345, "p": "50000.50", "q": "0.123",
        "f": 100, "l": 102, "T": 1748102400000, "m": False,
    }
    row = parse_agg_trade_message(msg, _NOW)
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["agg_trade_id"] == 12345
    assert row["price"] == 50000.5
    assert row["qty"] == 0.123
    assert row["is_buyer_maker"] is False


def test_parse_agg_trade_buyer_maker_true():
    msg = {"s": "X", "a": 1, "p": "1", "q": "1", "T": 1, "m": True}
    row = parse_agg_trade_message(msg, _NOW)
    assert row["is_buyer_maker"] is True


def test_parse_agg_trade_missing_returns_none():
    assert parse_agg_trade_message({}, _NOW) is None
    assert parse_agg_trade_message({"s": "X", "a": 1}, _NOW) is None


def test_parse_trade_format_uses_t_field():
    """@trade payload (t = tradeId) 도 동일 row 생성 — agg_trade_id 컬럼에 trade ID."""
    msg = {
        "e": "trade", "E": 1748102400000, "s": "BTCUSDT",
        "t": 999999, "p": "50000.0", "q": "0.01",
        "T": 1748102400000, "m": True, "X": "MARKET",
    }
    row = parse_agg_trade_message(msg, _NOW)
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["agg_trade_id"] == 999999
    assert row["is_buyer_maker"] is True
    assert row["source_stream"] == "trade"


def test_parse_aggTrade_format_marks_source():
    """@aggTrade payload (a = aggTradeId) → source_stream='aggTrade'."""
    msg = {
        "e": "aggTrade", "E": 1748102400000, "s": "BTCUSDT",
        "a": 12345, "p": "50000.0", "q": "0.5",
        "T": 1748102400000, "m": False,
    }
    row = parse_agg_trade_message(msg, _NOW)
    assert row is not None
    assert row["agg_trade_id"] == 12345
    assert row["source_stream"] == "aggTrade"


# ── parse_liquidation_message ─────────────────────────────────────
def test_parse_liquidation_wrapped_format():
    """forceOrder 표준 wrapper {'e':'forceOrder','o':{...}}"""
    msg = {
        "e": "forceOrder", "E": 1748102400000,
        "o": {
            "s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
            "q": "0.5", "p": "49500", "ap": "49500", "X": "FILLED",
            "l": "0.5", "z": "0.5", "T": 1748102400000,
        }
    }
    row = parse_liquidation_message(msg, _NOW)
    assert row is not None
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "SELL"
    assert row["price"] == 49500.0
    assert row["qty"] == 0.5


def test_parse_liquidation_unwrapped_format():
    """일부 stream 은 wrapper 없이 order 직접."""
    msg = {"s": "ETHUSDT", "S": "BUY", "p": "3000", "q": "1.0", "T": 1748102400000}
    row = parse_liquidation_message(msg, _NOW)
    assert row is not None
    assert row["side"] == "BUY"


def test_parse_liquidation_invalid_side_returns_none():
    msg = {"s": "X", "S": "HEDGE", "p": "1", "q": "1", "T": 1}
    assert parse_liquidation_message(msg, _NOW) is None


def test_parse_liquidation_missing_returns_none():
    assert parse_liquidation_message({}, _NOW) is None
    assert parse_liquidation_message({"o": {"s": "X"}}, _NOW) is None


# ── compute_latency_ms ────────────────────────────────────────────
def test_latency_computation():
    e = "2026-05-24T12:00:00+00:00"
    r = "2026-05-24T12:00:00.350+00:00"
    assert compute_latency_ms(e, r) == 350


def test_latency_negative_clock_skew():
    """수신 시각이 exchange 시각보다 앞서면 음수 (clock skew)."""
    e = "2026-05-24T12:00:01+00:00"
    r = "2026-05-24T12:00:00+00:00"
    assert compute_latency_ms(e, r) == -1000


# ── BatchBuffer ───────────────────────────────────────────────────
@pytest.fixture
def mock_persist():
    p = MagicMock()
    p.upsert_many = MagicMock(return_value=True)
    return p


@pytest.mark.asyncio
async def test_batch_buffer_auto_flushes_at_max_rows(mock_persist):
    buf = BatchBuffer(mock_persist, TABLE_L2, "symbol,ts", max_rows=3)
    await buf.add({"a": 1})
    await buf.add({"a": 2})
    assert mock_persist.upsert_many.call_count == 0   # 아직 flush 안 됨
    await buf.add({"a": 3})   # 3번째 → flush
    assert mock_persist.upsert_many.call_count == 1
    args = mock_persist.upsert_many.call_args
    assert args[0][0] == TABLE_L2
    assert len(args[0][1]) == 3


@pytest.mark.asyncio
async def test_batch_buffer_flush_explicit(mock_persist):
    buf = BatchBuffer(mock_persist, TABLE_AGG, "symbol,agg_trade_id", max_rows=100)
    await buf.add({"a": 1})
    n = await buf.flush()
    assert n == 1
    assert mock_persist.upsert_many.call_count == 1


@pytest.mark.asyncio
async def test_batch_buffer_flush_if_stale(mock_persist):
    buf = BatchBuffer(mock_persist, TABLE_LIQ, "symbol,exchange_ts,side,price,qty",
                       max_rows=100, max_age_seconds=0.0)   # 즉시 stale
    await buf.add({"a": 1})
    # 약간 대기 후 stale check
    await asyncio.sleep(0.01)
    await buf.flush_if_stale()
    assert mock_persist.upsert_many.call_count == 1


@pytest.mark.asyncio
async def test_batch_buffer_empty_flush_noop(mock_persist):
    buf = BatchBuffer(mock_persist, TABLE_L2, "symbol,ts")
    n = await buf.flush()
    assert n == 0
    assert mock_persist.upsert_many.call_count == 0


@pytest.mark.asyncio
async def test_batch_buffer_supabase_failure_does_not_raise(mock_persist):
    """upsert_many 가 예외 발생해도 collector 가 죽지 않음 (outbox 폴백)."""
    mock_persist.upsert_many.side_effect = Exception("network error")
    buf = BatchBuffer(mock_persist, TABLE_L2, "symbol,ts", max_rows=1)
    await buf.add({"a": 1})   # exception 발생하지만 swallow
    # 다음 add 도 OK
    await buf.add({"a": 2})   # _rows reset 됐으므로 새 batch 시작
    # 호출은 됐어야 함 (실패해도 시도)
    assert mock_persist.upsert_many.call_count >= 1


# ── WSCollector handlers ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_handle_agg_trade_increments_stats(mock_persist):
    cfg = WSCollectorConfig(symbols=["BTCUSDT"])
    col = WSCollector(cfg, mock_persist)
    msg = {"s": "BTCUSDT", "a": 1, "p": "1", "q": "1", "T": 1748102400000, "m": False}
    await col.handle_agg_trade(msg)
    assert col.stats.agg_rows_total == 1
    assert col.stats.last_agg_msg_at is not None


@pytest.mark.asyncio
async def test_handle_liquidation_increments_stats(mock_persist):
    cfg = WSCollectorConfig(symbols=["BTCUSDT"])
    col = WSCollector(cfg, mock_persist)
    msg = {"o": {"s": "BTCUSDT", "S": "SELL", "p": "50000", "q": "1", "T": 1748102400000}}
    await col.handle_liquidation(msg)
    assert col.stats.liq_rows_total == 1


@pytest.mark.asyncio
async def test_handle_depth_updates_cache_not_buffer(mock_persist):
    """depth 메시지는 메모리 캐시만 갱신, *batch 즉시 안 됨* (1초 commit task 가 처리)."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT"])
    col = WSCollector(cfg, mock_persist)
    msg = {"E": 1748102400000,
           "b": [["50000", "1"]], "a": [["50001", "1"]]}
    await col.handle_depth("BTCUSDT", msg)
    assert col.stats.last_l2_msg_at is not None
    # 캐시에 들어감
    assert "BTCUSDT" in col._depth_cache
    # 하지만 batch 에는 아직 안 들어감 (commit task 가 1초마다 처리)
    assert col.stats.l2_rows_total == 0


@pytest.mark.asyncio
async def test_depth_commit_loop_emits_1_per_second(mock_persist):
    """1초 commit task 가 정확히 심볼당 1 row 적재."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT"], batch_max_rows=1)
    col = WSCollector(cfg, mock_persist)
    col._running = True
    # 메시지 여러 개 (같은 bucket)
    msg = {"E": 1748102400000, "b": [["50000", "1"]], "a": [["50001", "1"]]}
    for _ in range(10):
        await col.handle_depth("BTCUSDT", msg)
    # commit loop 한 번 실행 (1초 대기 우회 — 직접 한 iteration)
    last_committed: Dict[str, str] = {}
    for symbol, row in col._depth_cache.items():
        ts_key = row["ts"]
        if last_committed.get(symbol) == ts_key:
            continue
        await col._buffers[TABLE_L2].add(row)
        col.stats.l2_rows_total += 1
        last_committed[symbol] = ts_key
    # 10 메시지 → 같은 1초 bucket → 1 row 만 적재
    assert col.stats.l2_rows_total == 1


# ── 라이프사이클 (mock factory) ──────────────────────────────────
@pytest.mark.asyncio
async def test_collector_with_mock_factory(mock_persist):
    """ws_factory 주입으로 collector 가 라이프사이클 통과."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT"], duration_seconds=2,
                              batch_max_age_seconds=0.5, batch_max_rows=10)

    async def fake_ws():
        # 3 가지 메시지 yield
        yield "aggTrade", {"s": "BTCUSDT", "a": 1, "p": "1", "q": "1",
                              "T": 1748102400000, "m": False}
        yield "aggTrade", {"s": "BTCUSDT", "a": 2, "p": "1", "q": "1",
                              "T": 1748102400001, "m": True}
        yield "depth:BTCUSDT", {"E": 1748102400000,
                                  "b": [["50000", "1"]], "a": [["50001", "1"]]}
        yield "forceOrder", {"o": {"s": "BTCUSDT", "S": "SELL",
                                       "p": "50000", "q": "1",
                                       "T": 1748102400000}}

    col = WSCollector(cfg, mock_persist)
    await col.start(ws_factory=fake_ws)
    # stop 후 stats 확인
    assert col.stats.agg_rows_total == 2
    assert col.stats.liq_rows_total == 1
    # depth 는 캐시에 들어가지만 1초 commit task 가 적어도 1번은 돌아야 함
    # — 2초 duration 이라 적어도 한 번


# ── Healthcheck ───────────────────────────────────────────────────
def test_is_healthy_recent_message(mock_persist):
    cfg = WSCollectorConfig(symbols=["X"], healthcheck_stale_seconds=60)
    col = WSCollector(cfg, mock_persist)
    col.stats.last_agg_msg_at = datetime.now(timezone.utc)
    assert col.is_healthy() is True


def test_is_healthy_stale_returns_false(mock_persist):
    cfg = WSCollectorConfig(symbols=["X"], healthcheck_stale_seconds=10)
    col = WSCollector(cfg, mock_persist)
    col.stats.last_agg_msg_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert col.is_healthy() is False


def test_is_healthy_no_messages_returns_false(mock_persist):
    cfg = WSCollectorConfig(symbols=["X"])
    col = WSCollector(cfg, mock_persist)
    assert col.is_healthy() is False


# ── 통계 ──────────────────────────────────────────────────────────
def test_avg_latency_ms():
    stats = CollectorStats()
    stats.latency_ms_sum = 1500.0
    stats.latency_ms_count = 10
    assert stats.avg_latency_ms == 150.0


def test_avg_latency_zero_count():
    stats = CollectorStats()
    assert stats.avg_latency_ms == 0.0


def test_p95_latency_basic():
    """record_latency 가 p95 산출 가능하게 sample 누적."""
    stats = CollectorStats()
    for v in range(1, 101):
        stats.record_latency(float(v))
    assert stats.avg_latency_ms == 50.5
    # p95: 95th value 근처
    assert 94 <= stats.p95_latency_ms <= 96


def test_p95_empty_returns_zero():
    stats = CollectorStats()
    assert stats.p95_latency_ms == 0.0


def test_record_latency_caps_samples():
    """p95 sample 메모리 cap — N개 초과 시 최근 N개만 유지."""
    stats = CollectorStats()
    for v in range(20000):
        stats.record_latency(float(v), max_samples=10000)
    assert len(stats.latency_samples) == 10000
    # 가장 오래된 sample 은 truncate 됨
    assert min(stats.latency_samples) >= 10000


# ── Phase 2: symbol_streams (Tiered) + multiplex dispatch ─────────
def test_resolve_symbol_streams_backward_compat(mock_persist):
    """symbol_streams=None → 모든 symbols 에 전 stream 부여 (Phase 1-C 동일)."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT", "ETHUSDT"], symbol_streams=None)
    col = WSCollector(cfg, mock_persist)
    resolved = col._resolve_symbol_streams()
    assert set(resolved.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym, types in resolved.items():
        assert "depth" in types
        assert "aggTrade" in types
        assert "forceOrder" in types


def test_resolve_symbol_streams_tiered_override(mock_persist):
    """cfg.symbol_streams 제공 시 그대로 사용 (Tier 1 / Tier 2 분리)."""
    cfg = WSCollectorConfig(
        symbols=["BTCUSDT", "SOLUSDT"],   # 무시됨
        symbol_streams={
            "BTCUSDT": ("depth", "aggTrade", "forceOrder"),  # Tier 1
            "ARBUSDT": ("aggTrade", "forceOrder"),           # Tier 2 (depth 없음)
        },
    )
    col = WSCollector(cfg, mock_persist)
    resolved = col._resolve_symbol_streams()
    assert set(resolved.keys()) == {"BTCUSDT", "ARBUSDT"}
    assert "depth" in resolved["BTCUSDT"]
    assert "depth" not in resolved["ARBUSDT"]
    assert "aggTrade" in resolved["ARBUSDT"]


@pytest.mark.asyncio
async def test_dispatch_multiplex_routes_by_stream_name(mock_persist):
    """multiplex envelope ({stream, data}) → 올바른 handler dispatch."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT"],
                              symbol_streams={"BTCUSDT": ("depth", "aggTrade", "forceOrder")})
    col = WSCollector(cfg, mock_persist)
    # @aggTrade (legacy)
    await col._dispatch_multiplex({
        "stream": "btcusdt@aggTrade",
        "data": {"s": "BTCUSDT", "a": 1, "p": "1", "q": "1",
                  "T": 1748102400000, "m": False},
    })
    assert col.stats.agg_rows_total == 1
    # @trade (현재 폴백 — 같은 handler)
    await col._dispatch_multiplex({
        "stream": "btcusdt@trade",
        "data": {"s": "BTCUSDT", "t": 2, "p": "1", "q": "1",
                  "T": 1748102400001, "m": True, "X": "MARKET"},
    })
    assert col.stats.agg_rows_total == 2
    # depth (USDT-M canonical: @depth20)
    await col._dispatch_multiplex({
        "stream": "btcusdt@depth20",
        "data": {"E": 1748102400000, "b": [["50000", "1"]], "a": [["50001", "1"]]},
    })
    assert col.stats.last_l2_msg_at is not None
    # forceOrder
    await col._dispatch_multiplex({
        "stream": "!forceOrder@arr",
        "data": {"o": {"s": "BTCUSDT", "S": "SELL", "p": "50000", "q": "1",
                        "T": 1748102400000}},
    })
    assert col.stats.liq_rows_total == 1


@pytest.mark.asyncio
async def test_maybe_handle_liq_respects_symbol_streams_universe(mock_persist):
    """forceOrder broadcast — symbol_streams 키셋 안의 심볼만 적재."""
    cfg = WSCollectorConfig(
        symbols=["ZZZUSDT"],   # 옛 backward-compat 필드 (무시됨)
        symbol_streams={"BTCUSDT": ("aggTrade", "forceOrder")},
    )
    col = WSCollector(cfg, mock_persist)
    # BTC 는 symbol_streams 에 있으니 적재됨
    await col._maybe_handle_liq({"o": {"s": "BTCUSDT", "S": "SELL", "p": "1", "q": "1",
                                         "T": 1748102400000}})
    assert col.stats.liq_rows_total == 1
    # XYZ 는 symbol_streams 키 아님 → 무시
    await col._maybe_handle_liq({"o": {"s": "XYZUSDT", "S": "SELL", "p": "1", "q": "1",
                                         "T": 1748102400000}})
    assert col.stats.liq_rows_total == 1   # 증가 안 함


@pytest.mark.asyncio
async def test_maybe_handle_liq_backward_compat_uses_symbols(mock_persist):
    """symbol_streams=None 이면 기존처럼 cfg.symbols 사용."""
    cfg = WSCollectorConfig(symbols=["BTCUSDT"], symbol_streams=None)
    col = WSCollector(cfg, mock_persist)
    await col._maybe_handle_liq({"o": {"s": "BTCUSDT", "S": "SELL", "p": "1", "q": "1",
                                         "T": 1748102400000}})
    assert col.stats.liq_rows_total == 1
    await col._maybe_handle_liq({"o": {"s": "XYZUSDT", "S": "SELL", "p": "1", "q": "1",
                                         "T": 1748102400000}})
    assert col.stats.liq_rows_total == 1
