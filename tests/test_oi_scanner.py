"""
tests/test_oi_scanner.py
=====================================================================
OIScanner 단위 테스트.

근거: docs/SPEC_v3.1.md §8-8 (Layer 3), D-2 (OIScanner.scan() → [candidates])

구성:
  - collector 는 AsyncMock 주입 (실제 네트워크 호출 없음 — CLAUDE.md 규칙)
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 시나리오:
  1. 첫 스캔 → baseline 만 채우고 빈 결과
  2. 두 번째 스캔 OI 급증 + 가격 급등 → 후보 편입
  3. OI 변화율 임계 미달 → 제외
  4. 가격 변화율 임계 미달 → 제외
  5. top_n 정렬 + 상한
  6. 개별 페어 조회 실패 → 스킵, 나머지는 계속
  7. OI/시세 None → 스킵
  8. collector=None 또는 빈 페어 → 빈 결과
  9. 직전 OI 0 이하 → 스킵
=====================================================================
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from data.oi_scanner import Candidate, OIScanner


# ── helpers ─────────────────────────────────────────────────────────

def _make_collector(*, oi, price, candles=None, volume=1_000_000.0):
    """AsyncMock collector. oi/price 는 단일값 또는 side_effect 리스트."""
    c = AsyncMock()
    c.get_open_interest = AsyncMock(
        side_effect=oi if isinstance(oi, list) else None,
        return_value=oi if not isinstance(oi, list) else None,
    )
    c.get_ticker_price = AsyncMock(
        side_effect=price if isinstance(price, list) else None,
        return_value=price if not isinstance(price, list) else None,
    )
    # 기본 1H 캔들: 직전 종가 100 → 최신 종가 105 (가격 +5%)
    default_candles = candles if candles is not None else [
        (0, 0, 0, 100.0, 0, 1),
        (0, 0, 0, 105.0, 0, 2),
    ]
    c.get_candles = AsyncMock(return_value=default_candles)
    c.get_24h_quote_volume = AsyncMock(return_value=volume)
    return c


# ── 1. 첫 스캔 baseline ─────────────────────────────────────────────

async def test_first_scan_returns_empty_baseline():
    """첫 스캔은 baseline 만 기록하고 후보를 내지 않는다 (룩어헤드 방지)."""
    collector = _make_collector(oi=1000.0, price=50.0)
    scanner = OIScanner(collector)
    result = await scanner.scan(["SOLUSDT"])
    assert result == []
    assert scanner._prev_oi["SOLUSDT"] == 1000.0


# ── 2. OI 급증 + 가격 급등 → 후보 ───────────────────────────────────

async def test_second_scan_detects_surge():
    """두 번째 스캔에서 OI +10%, 가격 +5% → 후보 편입."""
    # 1차 OI 1000, 2차 OI 1100 (+10%)
    collector = _make_collector(oi=[1000.0, 1100.0], price=[50.0, 50.0])
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    await scanner.scan(["SOLUSDT"])            # baseline
    result = await scanner.scan(["SOLUSDT"])   # 변화율 산출
    assert len(result) == 1
    c = result[0]
    assert isinstance(c, Candidate)
    assert c.symbol == "SOLUSDT"
    assert c.oi_change_pct == pytest.approx(10.0)
    assert c.price_change_pct == pytest.approx(5.0)
    assert c.volume_24h == 1_000_000.0


# ── 3. OI 임계 미달 → 제외 ──────────────────────────────────────────

async def test_oi_below_threshold_excluded():
    """OI 변화율이 임계(5%) 미만이면 후보에서 제외."""
    collector = _make_collector(oi=[1000.0, 1020.0], price=[50.0, 50.0])  # +2%
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    await scanner.scan(["SOLUSDT"])
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 4. 가격 임계 미달 → 제외 ────────────────────────────────────────

async def test_price_below_threshold_excluded():
    """가격 변화율이 임계(2%) 미만이면 OI 가 급증해도 제외."""
    # 가격 변화율 0% (종가 100 → 100)
    flat_candles = [(0, 0, 0, 100.0, 0, 1), (0, 0, 0, 100.0, 0, 2)]
    collector = _make_collector(oi=[1000.0, 1200.0], price=[50.0, 50.0],
                                candles=flat_candles)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    await scanner.scan(["SOLUSDT"])
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 5. top_n 정렬 + 상한 ────────────────────────────────────────────

async def test_top_n_sorted_and_capped():
    """후보는 oi_change_pct 내림차순 정렬되고 top_n 으로 잘린다."""
    collector = AsyncMock()
    # baseline 모두 1000, 2차에서 A +30%, B +10%, C +20%
    collector.get_open_interest = AsyncMock(
        side_effect=[1000.0, 1000.0, 1000.0, 1300.0, 1100.0, 1200.0]
    )
    collector.get_ticker_price = AsyncMock(return_value=50.0)
    collector.get_candles = AsyncMock(
        return_value=[(0, 0, 0, 100.0, 0, 1), (0, 0, 0, 105.0, 0, 2)]
    )
    collector.get_24h_quote_volume = AsyncMock(return_value=1_000_000.0)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0, top_n=2)
    await scanner.scan(["A", "B", "C"])
    result = await scanner.scan(["A", "B", "C"])
    assert [c.symbol for c in result] == ["A", "C"]   # 30%, 20% (B 10% 잘림)


# ── 6. 개별 페어 실패 → 스킵 ────────────────────────────────────────

async def test_pair_exception_skipped():
    """한 페어가 예외를 던져도 나머지 페어 스캔은 계속된다."""
    collector = AsyncMock()
    collector.get_open_interest = AsyncMock(
        side_effect=[RuntimeError("boom"), 1000.0, RuntimeError("boom"), 1100.0]
    )
    collector.get_ticker_price = AsyncMock(return_value=50.0)
    collector.get_candles = AsyncMock(
        return_value=[(0, 0, 0, 100.0, 0, 1), (0, 0, 0, 105.0, 0, 2)]
    )
    collector.get_24h_quote_volume = AsyncMock(return_value=1_000_000.0)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    await scanner.scan(["BAD", "GOOD"])          # BAD 예외, GOOD baseline
    result = await scanner.scan(["BAD", "GOOD"])  # BAD 예외, GOOD +10%
    assert [c.symbol for c in result] == ["GOOD"]


# ── 7. OI/시세 None → 스킵 ──────────────────────────────────────────

async def test_none_oi_or_price_skipped():
    """collector 가 None 을 반환하면 해당 페어는 스킵된다."""
    collector = _make_collector(oi=None, price=50.0)
    scanner = OIScanner(collector)
    assert await scanner.scan(["SOLUSDT"]) == []
    assert "SOLUSDT" not in scanner._prev_oi


# ── 8. collector None / 빈 페어 ─────────────────────────────────────

async def test_empty_pairs_returns_empty():
    """빈 active_pairs → 빈 결과."""
    scanner = OIScanner(_make_collector(oi=1000.0, price=50.0))
    assert await scanner.scan([]) == []


async def test_none_collector_returns_empty():
    """collector=None → scan() 은 항상 빈 결과."""
    scanner = OIScanner(None)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 9. 직전 OI 0 이하 → 스킵 ────────────────────────────────────────

async def test_nonpositive_prev_oi_skipped():
    """직전 OI 가 0 이하면 변화율 산출 불가 → 스킵."""
    collector = _make_collector(oi=[0.0, 1100.0], price=[50.0, 50.0])
    scanner = OIScanner(collector)
    await scanner.scan(["SOLUSDT"])              # baseline 0.0
    assert await scanner.scan(["SOLUSDT"]) == []
