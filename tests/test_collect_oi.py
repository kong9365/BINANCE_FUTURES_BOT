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
        symbols=["SOLUSDT"], client=client, out_dir=tmp_path
    )
    assert summary == {"SOLUSDT": 2}
    assert (tmp_path / "SOLUSDT_1h.csv").exists()


def test_collect_once_accumulates_on_rerun(tmp_path):
    klines = [_kline(0, 105), _kline(3_600_000, 112)]
    oih = [{"timestamp": 0, "sumOpenInterest": "5000"},
           {"timestamp": 3_600_000, "sumOpenInterest": "5250"}]
    client = _client(klines, oih)
    collect_oi.collect_once(symbols=["SOLUSDT"], client=client, out_dir=tmp_path)

    client.futures_klines.return_value = [_kline(7_200_000, 118)]
    client.futures_open_interest_hist.return_value = [
        {"timestamp": 7_200_000, "sumOpenInterest": "5400"}
    ]
    summary = collect_oi.collect_once(
        symbols=["SOLUSDT"], client=client, out_dir=tmp_path
    )
    assert summary == {"SOLUSDT": 3}   # 2 + 1 누적
