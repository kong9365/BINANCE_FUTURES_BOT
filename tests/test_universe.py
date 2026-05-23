"""
tests/test_universe.py
=====================================================================
backtesting/universe.py — 유동성 유니버스 리졸버 단위 테스트(mock 티커).
=====================================================================
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backtesting import universe as uni


def _ticker(sym, qv):
    return {"symbol": sym, "quoteVolume": str(qv)}


def test_volume_tier_bands():
    assert uni.volume_tier(2e9) == 1        # >$1B
    assert uni.volume_tier(5e8) == 2        # >$300M
    assert uni.volume_tier(1.5e8) == 3      # >$100M
    assert uni.volume_tier(5e7) == 4        # >$30M (광범위 유니버스용)
    assert uni.volume_tier(1.5e7) == 5      # ≥$10M (저유동 한계)
    assert uni.volume_tier(0) == 5          # 경계: tier 최대


def test_resolve_filters_sorts_excludes():
    client = MagicMock()
    client.futures_ticker.return_value = [
        _ticker("BTCUSDT", 5e9),
        _ticker("SOLUSDT", 8e8),
        _ticker("TINYUSDT", 5e7),     # 거래대금 미달 → 제외
        _ticker("ETHUSDT", 3e9),
        _ticker("BTCUSDC", 9e9),      # USDT 아님 → 제외
        _ticker("DOGEUSDT", 4e8),
    ]
    out = uni.resolve_liquid_universe(
        client, min_quote_volume_usd=1e8, exclude=["ETHUSDT"]
    )
    syms = [e.symbol for e in out]
    assert syms == ["BTCUSDT", "SOLUSDT", "DOGEUSDT"]   # 거래대금 내림차순(5e9>8e8>4e8)
    assert "TINYUSDT" not in syms and "BTCUSDC" not in syms and "ETHUSDT" not in syms
    assert out[0].tier == 1 and out[1].tier == 2 and out[2].tier == 2


def test_resolve_top_n():
    client = MagicMock()
    client.futures_ticker.return_value = [
        _ticker("AUSDT", 5e9), _ticker("BUSDT", 4e9), _ticker("CUSDT", 3e9),
    ]
    out = uni.resolve_liquid_universe(client, min_quote_volume_usd=1e8, top_n=2)
    assert [e.symbol for e in out] == ["AUSDT", "BUSDT"]


def test_resolve_with_cmc_rank():
    client = MagicMock()
    client.futures_ticker.return_value = [_ticker("SOLUSDT", 8e8)]
    cmc = MagicMock()
    cmc.get_market_cap_rank.return_value = 5
    out = uni.resolve_liquid_universe(client, min_quote_volume_usd=1e8, cmc=cmc)
    assert out[0].cmc_rank == 5
    cmc.get_market_cap_rank.assert_called_with("SOL")


def test_resolve_ticker_failure_returns_empty():
    client = MagicMock()
    client.futures_ticker.side_effect = RuntimeError("net down")
    assert uni.resolve_liquid_universe(client) == []


def test_tiers_map():
    entries = [uni.UniverseEntry("BTCUSDT", 5e9, 1),
               uni.UniverseEntry("SOLUSDT", 8e8, 2)]
    assert uni.tiers_map(entries) == {"BTCUSDT": 1, "SOLUSDT": 2}
