"""
tests/test_microstructure_universe.py
=====================================================================
data/microstructure_universe.py — Tiered universe resolver 단위 테스트.

모든 테스트 mock (exchange_info / ticker_data) — 실 Binance API 호출 없음.
=====================================================================
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from data.microstructure_universe import (
    CATEGORY_COMMODITY_LIKE,
    CATEGORY_CRYPTO,
    MICROSTRUCTURE_UNIVERSE_CONFIG,
    MicrostructureUniverseConfig,
    RESOLVER_VERSION,
    STREAM_AGG_TRADE,
    STREAM_DEPTH,
    STREAM_FORCE_ORDER,
    TIER1_FULL,
    TIER2_LIGHT,
    TIER_CORE,
    TIER_FULL,
    TIER_LIGHT,
    UniverseSymbol,
    build_multiplex_streams,
    build_symbol_streams_dict,
    resolve_tiered_universe,
    save_universe_snapshot,
    symbols_by_stream,
)


# ── Mock data helpers ────────────────────────────────────
def _ei(symbols: list[str], extra: dict | None = None) -> dict:
    """exchange_info mock — PERPETUAL/USDT/TRADING default."""
    out = []
    for s in symbols:
        d = {
            "symbol": s,
            "contractType": "PERPETUAL",
            "quoteAsset": "USDT",
            "status": "TRADING",
        }
        if extra and s in extra:
            d.update(extra[s])
        out.append(d)
    return {"symbols": out}


def _ticker(volumes: dict[str, float]) -> list[dict]:
    """ticker_data mock — {symbol: quote_volume_usd}."""
    return [{"symbol": s, "quoteVolume": str(v)} for s, v in volumes.items()]


# ── Core symbols always included ─────────────────────────
def test_core_symbols_always_included_regardless_of_volume():
    """BTC/ETH 거래대금 낮아도 Tier 0 항상 포함."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=2, tier2_light_count=2,
        core_symbols=("BTCUSDT", "ETHUSDT"),
    )
    ei = _ei(["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"])
    # SOL/DOGE/XRP 거래대금 훨씬 큼
    tk = _ticker({"BTCUSDT": 100, "ETHUSDT": 50, "SOLUSDT": 5000,
                   "DOGEUSDT": 4000, "XRPUSDT": 3000})
    out = resolve_tiered_universe(ei, tk, cfg)
    symbols_in_tier = {u.tier: u.symbol for u in out for _ in [u]}
    syms = [u.symbol for u in out]
    assert "BTCUSDT" in syms
    assert "ETHUSDT" in syms
    btc = next(u for u in out if u.symbol == "BTCUSDT")
    assert btc.tier == TIER_CORE


# ── Tier priority dedup ──────────────────────────────────
def test_tier1_priority_when_core_overlaps_with_top_volume():
    """core symbol 이 거래대금 top 이어도 Tier 0 로만 분류 (Tier 1 중복 제거)."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=3, tier2_light_count=2,
        core_symbols=("BTCUSDT",),
    )
    ei = _ei(["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"])
    tk = _ticker({"BTCUSDT": 1000, "ETHUSDT": 500, "SOLUSDT": 400, "DOGEUSDT": 300})
    out = resolve_tiered_universe(ei, tk, cfg)
    # BTC 가 거래대금 1위지만 core 이므로 tier0 한 번만
    btcs = [u for u in out if u.symbol == "BTCUSDT"]
    assert len(btcs) == 1
    assert btcs[0].tier == TIER_CORE
    # Tier 1 에는 ETH/SOL/DOGE 3개
    tier1 = [u.symbol for u in out if u.tier == TIER_FULL]
    assert sorted(tier1) == sorted(["ETHUSDT", "SOLUSDT", "DOGEUSDT"])


def test_tier1_and_tier2_dedup():
    """Tier 1 에 채워진 심볼이 Tier 2 후보에도 들어가지 않음."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=2, tier2_light_count=2, core_symbols=(),
    )
    ei = _ei(["A_USDT".replace("_", ""), "BUSDT", "CUSDT", "DUSDT"])
    tk = _ticker({"AUSDT": 1000, "BUSDT": 800, "CUSDT": 500, "DUSDT": 200})
    out = resolve_tiered_universe(ei, tk, cfg)
    tier1 = [u.symbol for u in out if u.tier == TIER_FULL]
    tier2 = [u.symbol for u in out if u.tier == TIER_LIGHT]
    assert set(tier1) == {"AUSDT", "BUSDT"}
    assert set(tier2) == {"CUSDT", "DUSDT"}
    # 중복 없음
    assert set(tier1) & set(tier2) == set()


# ── commodity-like exclusion ─────────────────────────────
def test_commodity_like_excluded_by_default():
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=10, tier2_light_count=10, core_symbols=(),
        include_commodity_like=False,
    )
    ei = _ei(["XAUUSDT", "XAGUSDT", "BTCUSDT", "SOLUSDT", "DOGEUSDT"])
    tk = _ticker({"XAUUSDT": 5000, "XAGUSDT": 4000, "BTCUSDT": 100,
                   "SOLUSDT": 80, "DOGEUSDT": 60})
    out = resolve_tiered_universe(ei, tk, cfg)
    syms = [u.symbol for u in out]
    assert "XAUUSDT" not in syms
    assert "XAGUSDT" not in syms
    # crypto 만 포함됨
    for u in out:
        assert u.category == CATEGORY_CRYPTO


def test_commodity_like_included_when_flag_true():
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=10, tier2_light_count=10, core_symbols=(),
        include_commodity_like=True,
    )
    ei = _ei(["XAUUSDT", "XAGUSDT", "BTCUSDT"])
    tk = _ticker({"XAUUSDT": 5000, "XAGUSDT": 4000, "BTCUSDT": 100})
    out = resolve_tiered_universe(ei, tk, cfg)
    syms = {u.symbol for u in out}
    assert "XAUUSDT" in syms
    xau = next(u for u in out if u.symbol == "XAUUSDT")
    assert xau.category == CATEGORY_COMMODITY_LIKE


def test_commodity_like_exact_set_not_prefix():
    """prefix 단독 사용 금지 — 'XAUUSDT' 정확 매치, 'XAUMUSDT' 같은 가짜는 crypto 분류."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=10, tier2_light_count=10, core_symbols=(),
        include_commodity_like=False,
        commodity_like_exact_symbols=("XAUUSDT", "XAGUSDT"),
    )
    # 'XAUM' (가상) 같은 정확 매치 안 되는 symbol 은 crypto 로 통과
    ei = _ei(["XAUUSDT", "XAUMUSDT", "BTCUSDT"])
    tk = _ticker({"XAUUSDT": 1000, "XAUMUSDT": 500, "BTCUSDT": 100})
    out = resolve_tiered_universe(ei, tk, cfg)
    syms = {u.symbol for u in out}
    assert "XAUUSDT" not in syms  # exact 매치 → 제외
    assert "XAUMUSDT" in syms     # prefix 만 매치 → 통과
    assert "BTCUSDT" in syms


# ── exchangeInfo 필터 ─────────────────────────────────────
def test_non_perpetual_excluded():
    cfg = MicrostructureUniverseConfig(tier1_full_count=10, core_symbols=())
    ei = {"symbols": [
        {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "BTCUSDC", "contractType": "PERPETUAL", "quoteAsset": "USDC", "status": "TRADING"},  # USDT 아님
        {"symbol": "BTC0926", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT", "status": "TRADING"},  # PERPETUAL 아님
        {"symbol": "DELISTED", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "BREAK"},  # TRADING 아님
    ]}
    tk = _ticker({"BTCUSDT": 100, "BTCUSDC": 50, "BTC0926": 30, "DELISTED": 10})
    out = resolve_tiered_universe(ei, tk, cfg)
    syms = {u.symbol for u in out}
    assert syms == {"BTCUSDT"}


def test_core_symbol_skipped_if_not_eligible():
    """core 라도 exchangeInfo 통과 못 하면 (예 status=BREAK) 스킵 — warning."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=1, core_symbols=("BTCUSDT", "ETHUSDT"),
    )
    ei = {"symbols": [
        {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "ETHUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "BREAK"},  # break
        {"symbol": "SOLUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
    ]}
    tk = _ticker({"BTCUSDT": 100, "SOLUSDT": 50})
    out = resolve_tiered_universe(ei, tk, cfg)
    syms = {u.symbol for u in out}
    assert "ETHUSDT" not in syms     # break → 스킵
    assert "BTCUSDT" in syms          # OK
    assert "SOLUSDT" in syms          # OK


# ── Stream types ─────────────────────────────────────────
def test_tier1_stream_types_include_depth():
    cfg = MicrostructureUniverseConfig(tier1_full_count=1, tier2_light_count=0, core_symbols=())
    ei = _ei(["BTCUSDT"])
    tk = _ticker({"BTCUSDT": 100})
    out = resolve_tiered_universe(ei, tk, cfg)
    assert out[0].stream_types == TIER1_FULL
    assert STREAM_DEPTH in out[0].stream_types


def test_tier2_stream_types_exclude_depth():
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=0, tier2_light_count=1, core_symbols=(),
    )
    ei = _ei(["BTCUSDT"])
    tk = _ticker({"BTCUSDT": 100})
    out = resolve_tiered_universe(ei, tk, cfg)
    assert out[0].tier == TIER_LIGHT
    assert out[0].stream_types == TIER2_LIGHT
    assert STREAM_DEPTH not in out[0].stream_types
    assert STREAM_AGG_TRADE in out[0].stream_types
    assert STREAM_FORCE_ORDER in out[0].stream_types


def test_core_symbols_get_full_streams():
    """Tier 0 (core) 도 depth 포함 (시장 기준선 + microstructure 자료)."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=0, tier2_light_count=0,
        core_symbols=("BTCUSDT",),
    )
    ei = _ei(["BTCUSDT"])
    tk = _ticker({"BTCUSDT": 100})
    out = resolve_tiered_universe(ei, tk, cfg)
    assert len(out) == 1
    assert out[0].tier == TIER_CORE
    assert STREAM_DEPTH in out[0].stream_types


# ── Empty/edge cases ─────────────────────────────────────
def test_empty_ticker_returns_only_core():
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=5, tier2_light_count=5,
        core_symbols=("BTCUSDT",),
    )
    ei = _ei(["BTCUSDT", "ETHUSDT"])
    tk = []  # 비어 있음
    out = resolve_tiered_universe(ei, tk, cfg)
    # core 만 반환 (Tier 1/2 후보 ranking 불가)
    assert len(out) == 1
    assert out[0].symbol == "BTCUSDT"
    assert out[0].tier == TIER_CORE


def test_empty_exchange_info_returns_empty():
    cfg = MicrostructureUniverseConfig(core_symbols=("BTCUSDT",))
    out = resolve_tiered_universe({"symbols": []}, _ticker({"BTCUSDT": 100}), cfg)
    assert out == []   # core 가 exchangeInfo 통과 못 함


def test_universe_size_capped_by_available():
    """가용 < tier1+tier2 면 가능한 만큼만 채움(에러 안 던짐)."""
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=50, tier2_light_count=50, core_symbols=(),
    )
    ei = _ei(["AUSDT", "BUSDT", "CUSDT"])
    tk = _ticker({"AUSDT": 100, "BUSDT": 50, "CUSDT": 30})
    out = resolve_tiered_universe(ei, tk, cfg)
    assert len(out) == 3   # 3 만 가용


# ── symbols_by_stream helper ─────────────────────────────
def test_symbols_by_stream_groups_correctly():
    universe = [
        UniverseSymbol("BTCUSDT", TIER_FULL, 100.0, CATEGORY_CRYPTO, TIER1_FULL),
        UniverseSymbol("ETHUSDT", TIER_FULL, 80.0, CATEGORY_CRYPTO, TIER1_FULL),
        UniverseSymbol("SOLUSDT", TIER_LIGHT, 40.0, CATEGORY_CRYPTO, TIER2_LIGHT),
    ]
    by_stream = symbols_by_stream(universe)
    assert set(by_stream[STREAM_DEPTH]) == {"BTCUSDT", "ETHUSDT"}
    assert set(by_stream[STREAM_AGG_TRADE]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert set(by_stream[STREAM_FORCE_ORDER]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


# ── build_symbol_streams_dict ────────────────────────────
def test_build_symbol_streams_dict_basic():
    universe = [
        UniverseSymbol("BTCUSDT", TIER_CORE, 100.0, CATEGORY_CRYPTO, TIER1_FULL),
        UniverseSymbol("SOLUSDT", TIER_LIGHT, 40.0, CATEGORY_CRYPTO, TIER2_LIGHT),
    ]
    d = build_symbol_streams_dict(universe)
    assert set(d["BTCUSDT"]) == set(TIER1_FULL)
    assert set(d["SOLUSDT"]) == set(TIER2_LIGHT)


# ── build_multiplex_streams ──────────────────────────────
def test_build_multiplex_streams_format():
    sd = {"BTCUSDT": TIER1_FULL, "SOLUSDT": TIER2_LIGHT}
    streams = build_multiplex_streams(sd)
    # USDT-M combined stream: depth20 (no @100ms suffix), @trade(=aggTrade 폴백)
    assert "btcusdt@depth20" in streams
    assert "btcusdt@trade" in streams
    assert "solusdt@trade" in streams
    assert "solusdt@depth20" not in streams   # Tier 2 = depth 없음
    assert "!forceOrder@arr" in streams
    assert streams.count("!forceOrder@arr") == 1     # 중복 안 됨


def test_build_multiplex_streams_no_force_order():
    """forceOrder stream type 없으면 !forceOrder@arr 도 없음."""
    sd = {"BTCUSDT": (STREAM_DEPTH,)}   # depth 만
    streams = build_multiplex_streams(sd)
    assert "btcusdt@depth20" in streams
    assert "!forceOrder@arr" not in streams


# ── save_universe_snapshot ───────────────────────────────
def test_save_snapshot_calls_upsert_many():
    universe = [
        UniverseSymbol("BTCUSDT", TIER_CORE, 100.0, CATEGORY_CRYPTO, TIER1_FULL),
        UniverseSymbol("ETHUSDT", TIER_FULL, 80.0, CATEGORY_CRYPTO, TIER1_FULL),
    ]
    persist = MagicMock()
    persist.upsert_many = MagicMock(return_value=True)
    ok = save_universe_snapshot(universe, persist)
    assert ok is True
    persist.upsert_many.assert_called_once()
    args = persist.upsert_many.call_args
    assert args[0][0] == "collection_universe_snapshots"
    rows = args[0][1]
    assert len(rows) == 2
    assert all("ts" in r for r in rows)
    assert all(r["resolver_version"] == RESOLVER_VERSION for r in rows)
    assert all(r["source"] == MICROSTRUCTURE_UNIVERSE_CONFIG.source for r in rows)
    # stream_types 가 list 로 저장됨 (JSONB 호환)
    assert all(isinstance(r["stream_types"], list) for r in rows)


def test_save_snapshot_empty_universe_returns_true_noop():
    persist = MagicMock()
    persist.upsert_many = MagicMock()
    ok = save_universe_snapshot([], persist)
    assert ok is True
    persist.upsert_many.assert_not_called()


def test_save_snapshot_persistence_failure_returns_false():
    universe = [UniverseSymbol("BTCUSDT", TIER_CORE, 100.0, CATEGORY_CRYPTO, TIER1_FULL)]
    persist = MagicMock()
    persist.upsert_many = MagicMock(side_effect=Exception("network"))
    ok = save_universe_snapshot(universe, persist)
    assert ok is False
