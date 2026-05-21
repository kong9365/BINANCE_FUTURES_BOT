"""
tests/test_collect_oi.py
=====================================================================
backtesting/collect_oi.py — OI 누적 수집 CLI 단위 테스트.

  - client 는 MagicMock 주입 (실제 네트워크 없음 — CLAUDE.md 규칙)
  - 저장은 tmp_path
검증:
  1. resolve_symbols — 기본 목록 / env override / 빈 env 폴백
  2. collect_once — collect_universe 호출 + 행수 요약
  3. collect_once 재실행 — 누적(멱등)
=====================================================================
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backtesting import collect_oi


def _kline(ts_ms, c=100.0):
    return [ts_ms, "100", "110", "90", str(c), "1000",
            ts_ms + 1, "0", 0, "0", "0", "0"]


def _client(klines, oih):
    c = MagicMock()
    c.futures_klines.return_value = klines
    c.futures_open_interest_hist.return_value = oih
    return c


def test_resolve_symbols_default(monkeypatch):
    monkeypatch.delenv("OI_COLLECT_SYMBOLS", raising=False)
    syms = collect_oi.resolve_symbols()
    assert "SOLUSDT" in syms and len(syms) >= 10
    # 보호종목은 수집 유니버스에 없어야 함
    for prot in ("BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"):
        assert prot not in syms


def test_resolve_symbols_env_override(monkeypatch):
    monkeypatch.setenv("OI_COLLECT_SYMBOLS", "AAAUSDT, BBBUSDT")
    assert collect_oi.resolve_symbols() == ["AAAUSDT", "BBBUSDT"]


def test_resolve_symbols_empty_env_falls_back(monkeypatch):
    monkeypatch.setenv("OI_COLLECT_SYMBOLS", "  , ")
    syms = collect_oi.resolve_symbols()
    assert "SOLUSDT" in syms   # 빈 결과 → 기본 목록 폴백


def test_collect_once_calls_loader_and_summarizes(tmp_path):
    klines = [_kline(0, 105), _kline(3_600_000, 112)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    client = _client(klines, oih)
    summary = collect_oi.collect_once(
        symbols=["SOLUSDT"], client=client, out_dir=tmp_path, push_supabase=False
    )
    assert summary == {"SOLUSDT": 2}
    assert (tmp_path / "SOLUSDT_1h.csv").exists()


def test_collect_once_accumulates_on_rerun(tmp_path):
    klines = [_kline(0, 105), _kline(3_600_000, 112)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    client = _client(klines, oih)
    collect_oi.collect_once(symbols=["SOLUSDT"], client=client, out_dir=tmp_path,
                            push_supabase=False)

    client.futures_klines.return_value = [_kline(7_200_000, 118)]
    client.futures_open_interest_hist.return_value = [
        {"timestamp": 7_200_000, "sumOpenInterest": "5400"}
    ]
    summary = collect_oi.collect_once(
        symbols=["SOLUSDT"], client=client, out_dir=tmp_path, push_supabase=False
    )
    assert summary == {"SOLUSDT": 3}   # 2 + 1 누적


def _funding(ts_ms, rate="0.0001"):
    return {"symbol": "SOLUSDT", "fundingTime": ts_ms, "fundingRate": rate}


def test_fetch_funding_parses():
    client = MagicMock()
    client.futures_funding_rate.return_value = [
        _funding(0, "0.0001"), _funding(28_800_000, "-0.0002")
    ]
    rows = collect_oi.fetch_funding(client, "SOLUSDT", limit=10)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "SOLUSDT"
    assert rows[0]["funding_rate"] == 0.0001
    assert rows[1]["funding_rate"] == -0.0002
    assert "T" in rows[0]["ts"]            # ISO 문자열


def test_fetch_funding_error_returns_empty():
    client = MagicMock()
    client.futures_funding_rate.side_effect = RuntimeError("net down")
    assert collect_oi.fetch_funding(client, "SOLUSDT") == []


def test_collect_once_pushes_to_supabase(tmp_path):
    klines = [_kline(0, 105), _kline(3_600_000, 112)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    client = _client(klines, oih)
    client.futures_funding_rate.return_value = [_funding(0, "0.0001")]
    persist = MagicMock()

    collect_oi.collect_once(
        symbols=["SOLUSDT"], client=client, out_dir=tmp_path,
        persist=persist, push_supabase=True, push_recent=None,
    )

    pushed_tables = [c.args[0] for c in persist.upsert_many.call_args_list]
    assert "ohlcv" in pushed_tables
    assert "oi_history" in pushed_tables
    assert "funding_history" in pushed_tables
    persist.flush_outbox.assert_called_once()


def test_push_recent_slices_rows():
    """push_recent=2 → 최근 2행만 적재."""
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": range(5), "high": range(5), "low": range(5),
        "close": range(5), "volume": range(5), "open_interest": range(5),
    }, index=idx)
    persist = MagicMock()
    counts = collect_oi.push_to_supabase(
        persist, "SOLUSDT", df, funding_rows=[], recent=2
    )
    assert counts["ohlcv"] == 2 and counts["oi"] == 2
