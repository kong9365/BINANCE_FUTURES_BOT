"""
tests/test_oi_scanner.py
=====================================================================
OIScanner 단위 테스트 (OI 이력 룩백 윈도우 기준).

근거: docs/SPEC_v3.1.md §8-8 (Layer 3), D-2 (OIScanner.scan() → [candidates])

구성:
  - collector 는 AsyncMock 주입 (실제 네트워크 호출 없음 — CLAUDE.md 규칙)
  - OI 변화율은 collector.get_open_interest_history(period, limit) 이력으로 산출
    (직전 스캔 30초 대비 아님 — 정량 진단에 따른 윈도우 교정)
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 시나리오:
  1. OI 이력 부족 → 빈 결과
  2. OI 급증 + 가격 급등 → 후보 편입 (단일 스캔, warmup 불필요)
  3. OI 변화율 임계 미달 → 제외
  4. 가격 변화율 임계 미달 → 제외
  5. top_n 정렬 + 상한
  6. 개별 페어 조회 실패 → 스킵, 나머지는 계속
  7. 시세 None → 스킵
  8. collector=None 또는 빈 페어 → 빈 결과
  9. 기준 OI 0 이하 → 스킵
 10. lookback_count>1 → 여러 주기 전과 비교
 11. 가격 변화 캔들 간격이 oi_lookback_period 와 정합
=====================================================================
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from data.oi_scanner import Candidate, OIScanner


# ── helpers ─────────────────────────────────────────────────────────

def _make_collector(*, oi_hist, price, candles=None, volume=1_000_000.0):
    """AsyncMock collector. oi_hist 는 get_open_interest_history 반환값
    (list[float], 오래된→최신) 또는 side_effect 리스트."""
    c = AsyncMock()
    # 요소가 list(페어별 이력) 또는 예외면 per-call side_effect, 아니면 단일 반환값.
    per_call = (
        isinstance(oi_hist, list) and oi_hist
        and isinstance(oi_hist[0], (list, BaseException))
    )
    if per_call:
        c.get_open_interest_history = AsyncMock(side_effect=oi_hist)
    else:
        c.get_open_interest_history = AsyncMock(return_value=oi_hist)
    c.get_ticker_price = AsyncMock(
        side_effect=price if isinstance(price, list) else None,
        return_value=price if not isinstance(price, list) else None,
    )
    # 기본 캔들: 직전 종가 100 → 최신 종가 105 (가격 +5%)
    default_candles = candles if candles is not None else [
        (0, 0, 0, 100.0, 0, 1),
        (0, 0, 0, 105.0, 0, 2),
    ]
    c.get_candles = AsyncMock(return_value=default_candles)
    c.get_24h_quote_volume = AsyncMock(return_value=volume)
    return c


# ── 1. OI 이력 부족 ─────────────────────────────────────────────────

async def test_insufficient_history_returns_empty():
    """OI 이력이 count+1 미만이면 후보를 내지 않는다."""
    collector = _make_collector(oi_hist=[1000.0], price=50.0)  # 1개 < 2
    scanner = OIScanner(collector)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 2. OI 급증 + 가격 급등 → 후보 ───────────────────────────────────

async def test_surge_detected():
    """OI 이력 [1000, 1100] (+10%), 가격 +5% → 후보 편입 (단일 스캔)."""
    collector = _make_collector(oi_hist=[1000.0, 1100.0], price=50.0)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    result = await scanner.scan(["SOLUSDT"])
    assert len(result) == 1
    c = result[0]
    assert isinstance(c, Candidate)
    assert c.symbol == "SOLUSDT"
    assert c.oi_now == 1100.0
    assert c.oi_change_pct == pytest.approx(10.0)
    assert c.price_change_pct == pytest.approx(5.0)
    assert c.volume_24h == 1_000_000.0
    # OI 이력은 lookback period/limit 로 조회됨
    collector.get_open_interest_history.assert_awaited_with(
        "SOLUSDT", period="15m", limit=2
    )


# ── 3. OI 임계 미달 → 제외 ──────────────────────────────────────────

async def test_oi_below_threshold_excluded():
    """OI 변화율이 임계(5%) 미만이면 제외 ([1000,1020] = +2%)."""
    collector = _make_collector(oi_hist=[1000.0, 1020.0], price=50.0)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 4. 가격 임계 미달 → 제외 ────────────────────────────────────────

async def test_price_below_threshold_excluded():
    """가격 변화율이 임계(2%) 미만이면 OI 급증해도 제외."""
    flat_candles = [(0, 0, 0, 100.0, 0, 1), (0, 0, 0, 100.0, 0, 2)]
    collector = _make_collector(oi_hist=[1000.0, 1200.0], price=50.0,
                                candles=flat_candles)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 5. top_n 정렬 + 상한 ────────────────────────────────────────────

async def test_top_n_sorted_and_capped():
    """후보는 oi_change_pct 내림차순 정렬되고 top_n 으로 잘린다."""
    # A +30%, B +10%, C +20%
    collector = _make_collector(
        oi_hist=[[1000.0, 1300.0], [1000.0, 1100.0], [1000.0, 1200.0]],
        price=50.0,
    )
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0, top_n=2)
    result = await scanner.scan(["A", "B", "C"])
    assert [c.symbol for c in result] == ["A", "C"]   # 30%, 20% (B 10% 잘림)


# ── 6. 개별 페어 실패 → 스킵 ────────────────────────────────────────

async def test_pair_exception_skipped():
    """한 페어가 예외를 던져도 나머지 페어 스캔은 계속된다."""
    collector = _make_collector(
        oi_hist=[RuntimeError("boom"), [1000.0, 1100.0]],  # BAD 예외, GOOD +10%
        price=50.0,
    )
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0)
    result = await scanner.scan(["BAD", "GOOD"])
    assert [c.symbol for c in result] == ["GOOD"]


# ── 7. 시세 None → 스킵 ─────────────────────────────────────────────

async def test_none_price_skipped():
    """시세가 None 이면 해당 페어는 스킵된다."""
    collector = _make_collector(oi_hist=[1000.0, 1200.0], price=None)
    scanner = OIScanner(collector)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 8. collector None / 빈 페어 ─────────────────────────────────────

async def test_empty_pairs_returns_empty():
    scanner = OIScanner(_make_collector(oi_hist=[1000.0, 1100.0], price=50.0))
    assert await scanner.scan([]) == []


async def test_none_collector_returns_empty():
    scanner = OIScanner(None)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 9. 기준 OI 0 이하 → 스킵 ────────────────────────────────────────

async def test_nonpositive_baseline_skipped():
    """기준(가장 오래된) OI 가 0 이하면 변화율 산출 불가 → 스킵."""
    collector = _make_collector(oi_hist=[0.0, 1100.0], price=50.0)
    scanner = OIScanner(collector)
    assert await scanner.scan(["SOLUSDT"]) == []


# ── 10. lookback_count > 1 ──────────────────────────────────────────

async def test_lookback_count_multiple_periods():
    """count=2 → limit=3 으로 조회, 가장 오래된 값과 비교."""
    collector = _make_collector(oi_hist=[1000.0, 1050.0, 1200.0], price=50.0)
    scanner = OIScanner(collector, oi_change_threshold_pct=5.0,
                        price_change_threshold_pct=2.0, oi_lookback_count=2)
    result = await scanner.scan(["SOLUSDT"])
    assert len(result) == 1
    assert result[0].oi_change_pct == pytest.approx(20.0)  # (1200-1000)/1000
    collector.get_open_interest_history.assert_awaited_with(
        "SOLUSDT", period="15m", limit=3
    )


# ── 11. 가격 캔들 간격 정합 ─────────────────────────────────────────

async def test_price_candles_use_lookback_period():
    """가격 변화율 캔들 간격이 oi_lookback_period 와 동일하게 조회된다."""
    collector = _make_collector(oi_hist=[1000.0, 1100.0], price=50.0)
    scanner = OIScanner(collector, oi_lookback_period="1h")
    await scanner.scan(["SOLUSDT"])
    collector.get_candles.assert_awaited_with("SOLUSDT", "1h", 2)
