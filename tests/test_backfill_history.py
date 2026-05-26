"""
tests/test_backfill_history.py
=====================================================================
backtesting/backfill_history.py — 과거 OHLCV/펀딩 페이지네이션 backfill 단위 테스트.
client 는 MagicMock(실네트워크 없음 — CLAUDE.md). 멱등·진행가드·taker_buy_base 검증.
=====================================================================
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backtesting import backfill_history as bh


def _k(ot, taker=10.0):
    # [openTime, o,h,l,c,v, closeTime, qav, trades, takerBuyBase, takerBuyQuote, ignore]
    return [ot, "100", "110", "90", "105", "1000", ot + 1, "0", 0, str(taker), "0", "0"]


def _fund(ft, rate="0.0001"):
    return {"symbol": "BTCUSDT", "fundingTime": ft, "fundingRate": rate}


def test_interval_ms():
    assert bh.interval_ms("1h") == 3_600_000
    assert bh.interval_ms("1d") == 86_400_000


def test_fetch_klines_history_paginates_and_dedups():
    client = MagicMock()
    h = 3_600_000
    # 페이지1: 0,1h ; 페이지2: 2h(겹침 없음) ; 페이지3: [] → 종료
    client.futures_klines.side_effect = [
        [_k(0, 5.0), _k(h, 6.0)],
        [_k(2 * h, 7.0)],
        [],
    ]
    rows = bh.fetch_klines_history(client, "BTCUSDT", "1h", start_ms=0, pause=0.0)
    assert len(rows) == 3
    assert rows[0]["taker_buy_base"] == 5.0
    assert rows[2]["taker_buy_base"] == 7.0
    assert rows[0]["symbol"] == "BTCUSDT" and rows[0]["interval"] == "1h"
    assert "T" in rows[0]["ts"]            # ISO


def test_fetch_klines_history_progress_guard():
    client = MagicMock()
    # 같은 페이지를 계속 반환해도 진행가드(seen+nxt<=cur)로 무한루프 방지
    client.futures_klines.return_value = [_k(0)]
    rows = bh.fetch_klines_history(client, "BTCUSDT", "1h", start_ms=0, pause=0.0)
    assert len(rows) == 1                  # 1봉만, 두 번째 페이지에서 진행 없음→종료


def test_fetch_funding_history_paginates():
    client = MagicMock()
    eight_h = 8 * 3_600_000
    client.futures_funding_rate.side_effect = [
        [_fund(0, "0.0001"), _fund(eight_h, "-0.0002")],
        [],
    ]
    rows = bh.fetch_funding_history(client, "BTCUSDT", start_ms=0, pause=0.0)
    assert len(rows) == 2
    assert rows[1]["funding_rate"] == -0.0002


def test_backfill_symbol_pushes(monkeypatch):
    client = MagicMock()
    client.futures_klines.side_effect = [[_k(0), _k(3_600_000)], []]
    client.futures_funding_rate.side_effect = [[_fund(0)], []]
    persist = MagicMock()
    summary = bh.backfill_symbol(client, persist, "BTCUSDT", "1h", 0,
                                 push_supabase=True, chunk=1000)
    assert summary == {"ohlcv": 2, "funding": 1}
    tables = [c.args[0] for c in persist.upsert_many.call_args_list]
    assert "ohlcv" in tables and "funding_history" in tables


def test_push_chunked_splits():
    persist = MagicMock()
    rows = [{"x": i} for i in range(2500)]
    n = bh._push_chunked(persist, "ohlcv", rows, chunk=1000)
    assert n == 2500
    assert persist.upsert_many.call_count == 3      # 1000+1000+500


def test_resolve_backfill_symbols_top_n_excludes_protected(monkeypatch):
    client = MagicMock()

    def _fake_universe(c, exclude=None, top_n=None, min_quote_volume_usd=10_000_000.0):
        assert exclude is not None
        assert "BTCUSDT" in exclude
        return [type("E", (), {"symbol": "SOLUSDT"})()]

    monkeypatch.setattr(
        "backtesting.universe.resolve_liquid_universe", _fake_universe,
    )
    syms = bh.resolve_backfill_symbols(
        client, symbols_top_n=50, exclude_protected=True, include_index=False,
    )
    assert syms == ["SOLUSDT"]


def test_resolve_backfill_symbols_adds_index(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(
        "backtesting.universe.resolve_liquid_universe",
        lambda c, exclude=None, top_n=None, min_quote_volume_usd=10_000_000.0: [
            type("E", (), {"symbol": "SOLUSDT"})(),
        ],
    )
    syms = bh.resolve_backfill_symbols(
        client, symbols_top_n=1, exclude_protected=True, include_index=True,
    )
    assert syms == ["SOLUSDT", "BTCUSDT", "ETHUSDT"]
