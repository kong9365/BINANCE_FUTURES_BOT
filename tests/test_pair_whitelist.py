"""
tests/test_pair_whitelist.py
=====================================================================
PairWhitelist 단위 테스트.

  - 시나리오 1~7: §8-7-3 기존 시나리오
  - 시나리오 8~11: 부록 E-3-3 보호 종목 통합 시나리오

binance_client는 unittest.mock.MagicMock으로 mock.
외부 API 호출(상장일·거래량·펀딩비)이 필요한 시나리오는 인스턴스의
private 메서드를 직접 교체하여 격리한다.
=====================================================================
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from config.settings import PAIR_WHITELIST_CONFIG
from strategy.pair_whitelist import PairWhitelist


# ── 시나리오 1: 기본 Tier 1 — BTCUSDT, ETHUSDT는 항상 활성 ──
def test_tier1_pairs_active_by_default():
    pw = PairWhitelist(MagicMock())
    assert pw.get_tier("BTCUSDT") == 1
    assert pw.get_tier("ETHUSDT") == 1
    assert pw.is_allowed("BTCUSDT", capital=10000) is True
    assert pw.is_allowed("ETHUSDT", capital=10000) is True


# ── 시나리오 2: 신규 상장 (listed_at = 10일 전) → blocked ──
def test_new_listing_blocked():
    pw = PairWhitelist(MagicMock())
    recent = datetime.now() - timedelta(days=10)
    pw._get_listing_date = lambda s: recent
    pw._get_volume_24h = lambda s: None
    pw._get_avg_funding_7d = lambda s: None

    pw.refresh()
    assert pw.get_tier("SOLUSDT") == 0
    assert any("신규 상장" in r for r in pw.get_info("SOLUSDT").blocked_reasons)


# ── 시나리오 3: 거래량 < $100M → blocked ──
def test_low_volume_blocked():
    pw = PairWhitelist(MagicMock())
    pw._get_listing_date = lambda s: None
    pw._get_volume_24h = lambda s: 50_000_000
    pw._get_avg_funding_7d = lambda s: None

    pw.refresh()
    assert pw.get_tier("SOLUSDT") == 0
    assert any("거래량 부족" in r for r in pw.get_info("SOLUSDT").blocked_reasons)


# ── 시나리오 4: 펀딩비 평균 0.10% → blocked ──
def test_extreme_funding_blocked():
    pw = PairWhitelist(MagicMock())
    pw._get_listing_date = lambda s: None
    pw._get_volume_24h = lambda s: None
    pw._get_avg_funding_7d = lambda s: 0.001  # 0.10% > max 0.05%

    pw.refresh()
    assert pw.get_tier("SOLUSDT") == 0
    assert any("펀딩비 극단" in r for r in pw.get_info("SOLUSDT").blocked_reasons)


# ── 시나리오 5: 자본 $500 → get_active()는 Tier 1만 ──
def test_get_active_capital_500_tier1_only():
    pw = PairWhitelist(MagicMock())
    active = pw.get_active(capital=500)
    assert set(active) == {"BTCUSDT", "ETHUSDT"}


# ── 시나리오 6: 자본 $5000 + RANGING 레짐 → Tier 1만 ──
def test_get_active_ranging_regime_tier1_only():
    pw = PairWhitelist(MagicMock())
    active = pw.get_active(capital=5000, regime="RANGING")
    assert set(active) == {"BTCUSDT", "ETHUSDT"}


# ── 시나리오 7: manual_block("SOLUSDT") → 차단됨 ──
def test_manual_block():
    pw = PairWhitelist(MagicMock())
    assert pw.get_tier("SOLUSDT") == 2
    pw.manual_block("SOLUSDT", "test")
    assert pw.get_tier("SOLUSDT") == 0
    assert pw.is_allowed("SOLUSDT", capital=5000) is False


# ── 시나리오 8: protected_symbols=["BTCUSDT"] → is_allowed False ──
def test_protected_symbol_not_allowed():
    pw = PairWhitelist(MagicMock(), protected_symbols=["BTCUSDT"])
    assert pw.is_allowed("BTCUSDT", capital=100000) is False
    assert pw.get_tier("BTCUSDT") == 0
    # 다른 페어는 정상
    assert pw.is_allowed("ETHUSDT", capital=100000) is True


# ── 시나리오 9: refresh() 후에도 보호 종목 차단 유지 ──
def test_protected_symbol_blocked_after_refresh():
    pw = PairWhitelist(MagicMock(), protected_symbols=["BTCUSDT"])
    pw._get_listing_date = lambda s: None
    pw._get_volume_24h = lambda s: None
    pw._get_avg_funding_7d = lambda s: None

    pw.refresh()
    assert pw.get_tier("BTCUSDT") == 0
    assert pw.is_allowed("BTCUSDT", capital=100000) is False
    assert _PROTECTED_REASON_IN(pw.get_info("BTCUSDT").blocked_reasons)


# ── 시나리오 10: manual_unblock("BTCUSDT") 거부 (경고 로그만) ──
def test_protected_symbol_manual_unblock_rejected(caplog):
    pw = PairWhitelist(MagicMock(), protected_symbols=["BTCUSDT"])
    with caplog.at_level(logging.WARNING, logger="strategy.pair_whitelist"):
        pw.manual_unblock("BTCUSDT")

    assert pw.get_tier("BTCUSDT") == 0
    assert pw.is_allowed("BTCUSDT", capital=100000) is False
    assert any("unblock 거부" in r.message for r in caplog.records)


# ── 시나리오 11: get_active() 결과에 보호 종목 미포함 ──
def test_get_active_excludes_protected_symbol():
    pw = PairWhitelist(MagicMock(), protected_symbols=["BTCUSDT"])
    active = pw.get_active(capital=10000)
    assert "BTCUSDT" not in active
    assert "ETHUSDT" in active  # 보호되지 않은 Tier 1은 포함


# ── 시나리오 12: 기본 보호종목 6개 전부 차단 (INJUSDT 포함) ──
def test_all_default_protected_symbols_blocked():
    """config 기본 보호종목 6개(BTC/ETH/HOLO/CFX/LYN/INJ)가 모두 차단된다."""
    expected = {"BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"}
    assert expected.issubset(set(PAIR_WHITELIST_CONFIG.protected_symbols))
    pw = PairWhitelist(MagicMock(),
                       protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols)
    for sym in expected:
        assert pw.is_allowed(sym, capital=100000) is False, f"{sym} 미차단"
    # 비보호 종목은 영향 없음
    assert pw.is_allowed("SOLUSDT", capital=100000) is True


# ── 시나리오 13: INJUSDT manual_unblock 거부 ──
def test_injusdt_manual_unblock_rejected(caplog):
    """INJUSDT 도 manual_unblock 으로 풀리지 않는다(보호 절대 룰)."""
    pw = PairWhitelist(MagicMock(),
                       protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols)
    with caplog.at_level(logging.WARNING, logger="strategy.pair_whitelist"):
        pw.manual_unblock("INJUSDT")
    assert pw.is_allowed("INJUSDT", capital=100000) is False
    assert "INJUSDT" not in pw.get_active(capital=100000)


def _PROTECTED_REASON_IN(reasons: list) -> bool:
    """blocked_reasons에 보호 종목 사유가 들어있는지."""
    return any("protected_symbol" in r for r in reasons)
