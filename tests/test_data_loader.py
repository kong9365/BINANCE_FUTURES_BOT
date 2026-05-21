"""
tests/test_data_loader.py
=====================================================================
backtesting/data_loader.py — OI+OHLCV 수집/적재 단위 테스트.

  - client 는 MagicMock 주입 (실제 네트워크 없음 — CLAUDE.md 규칙)
  - 저장은 tmp_path
검증:
  1. fetch_symbol — klines∩OI 교집합 → OHLCV+open_interest DF
  2. fetch_symbol — 교집합 없음 → 빈 DF
  3. save/load 라운드트립 (UTC tz-aware index 보존)
  4. merge_into — union(신규 우선) 누적
  5. collect_universe — 저장 + 재실행 시 누적(멱등)
  6. load_universe — 저장된 것만 로드
=====================================================================
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from backtesting import data_loader as dl


def _kline(ts_ms, o, h, low, c, v):
    return [ts_ms, str(o), str(h), str(low), str(c), str(v),
            ts_ms + 1, "0", 0, "0", "0", "0"]


def _client(klines, oih):
    c = MagicMock()
    c.futures_klines.return_value = klines
    c.futures_open_interest_hist.return_value = oih
    return c


def test_fetch_symbol_merges_klines_and_oi():
    klines = [_kline(0, 100, 110, 90, 105, 1000),
              _kline(3_600_000, 105, 115, 100, 112, 1200)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    df = dl.fetch_symbol(_client(klines, oih), "SOLUSDT")
    assert list(df.columns) == ["open", "high", "low", "close", "volume",
                                "open_interest"]
    assert len(df) == 2
    assert df["open_interest"].tolist() == [5000.0, 5250.0]
    assert df["close"].tolist() == [105.0, 112.0]
    assert str(df.index.tz) == "UTC"


def test_fetch_symbol_no_overlap_returns_empty():
    klines = [_kline(0, 100, 110, 90, 105, 1000)]
    oih = [{"timestamp": 999, "sumOpenInterest": "5000"}]  # 다른 timestamp
    df = dl.fetch_symbol(_client(klines, oih), "SOLUSDT")
    assert df.empty


def test_save_load_roundtrip(tmp_path):
    klines = [_kline(0, 100, 110, 90, 105, 1000),
              _kline(3_600_000, 105, 115, 100, 112, 1200)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    df = dl.fetch_symbol(_client(klines, oih), "SOLUSDT")
    p = tmp_path / "SOLUSDT_1h.csv"
    dl.save_dataframe(df, p)
    loaded = dl.load_dataframe(p)
    assert str(loaded.index.tz) == "UTC"
    assert loaded["open_interest"].tolist() == [5000.0, 5250.0]
    assert loaded.index.equals(df.index)


def test_merge_into_accumulates_union():
    idx1 = pd.DatetimeIndex([pd.Timestamp(0, unit="ms", tz="UTC"),
                             pd.Timestamp(3_600_000, unit="ms", tz="UTC")])
    a = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2],
                      "close": [1, 2], "volume": [1, 2], "open_interest": [10, 20]},
                     index=idx1)
    idx2 = pd.DatetimeIndex([pd.Timestamp(3_600_000, unit="ms", tz="UTC"),
                             pd.Timestamp(7_200_000, unit="ms", tz="UTC")])
    b = pd.DataFrame({"open": [99, 3], "high": [99, 3], "low": [99, 3],
                      "close": [99, 3], "volume": [99, 3], "open_interest": [99, 30]},
                     index=idx2)
    merged = dl.merge_into(a, b)
    assert len(merged) == 3                      # union: 3 timestamps
    # 겹치는 timestamp 는 신규(b) 우선
    assert merged.iloc[1]["open_interest"] == 99


def test_collect_universe_saves_and_accumulates(tmp_path):
    klines = [_kline(0, 100, 110, 90, 105, 1000),
              _kline(3_600_000, 105, 115, 100, 112, 1200)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    client = _client(klines, oih)
    out = dl.collect_universe(client, ["SOLUSDT"], out_dir=tmp_path)
    assert (tmp_path / "SOLUSDT_1h.csv").exists()
    assert len(out["SOLUSDT"]) == 2

    # 재실행 — 새 bar 추가 → 누적
    klines2 = [_kline(7_200_000, 112, 120, 110, 118, 1300)]
    oih2 = [{"timestamp": 7_200_000, "sumOpenInterest": "5400"}]
    client.futures_klines.return_value = klines2
    client.futures_open_interest_hist.return_value = oih2
    out2 = dl.collect_universe(client, ["SOLUSDT"], out_dir=tmp_path)
    assert len(out2["SOLUSDT"]) == 3             # 2 + 1 누적


def test_load_universe_only_existing(tmp_path):
    klines = [_kline(0, 100, 110, 90, 105, 1000),
              _kline(3_600_000, 105, 115, 100, 112, 1200)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    dl.collect_universe(_client(klines, oih), ["SOLUSDT"], out_dir=tmp_path)
    loaded = dl.load_universe(["SOLUSDT", "XRPUSDT"], out_dir=tmp_path)
    assert set(loaded) == {"SOLUSDT"}            # XRPUSDT 미저장 → 스킵
    assert len(loaded["SOLUSDT"]) == 2
