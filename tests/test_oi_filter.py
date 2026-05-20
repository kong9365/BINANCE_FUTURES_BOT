"""
tests/test_oi_filter.py
=====================================================================
OIFilter 단위 테스트.

근거: docs/SPEC_v3.1.md §8-8 (Layer 3), D-2 (OIFilter → SignalGrade)

구성:
  - Candidate 는 data.oi_scanner.Candidate 실제 dataclass 사용
  - regime_state 는 SimpleNamespace 로 최소 필드(regime/confidence)만 구성
    (RegimeState 전체 필드 불필요 — OIFilter 는 .regime / .confidence 만 참조)

검증 시나리오:
  1. 추세 정렬 LONG + 높은 신뢰도 → A_STRONG (score 80)
  2. 추세 정렬 SHORT + 높은 신뢰도 → A_STRONG
  3. 추세 정렬이나 신뢰도 낮음 → B_MODERATE (score 65)
  4. RANGING/UNCERTAIN 중립 레짐 → B_MODERATE (score 55)
  5. 역추세 LONG (TREND_DOWN) → C_DANGER
  6. 역추세 SHORT (TREND_UP) → C_DANGER
  7. OI 변화 극단(>50%) → C_DANGER
  8. 24h 거래대금 부족 → C_DANGER
  9. HIGH_VOL 레짐 방어 → C_DANGER
 10. 가격 변화율 0% → C_DANGER
 11. action 방향 판정 (가격 부호)
=====================================================================
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from data.oi_scanner import Candidate
from strategy.oi_filter import (
    GRADE_A_STRONG,
    GRADE_B_MODERATE,
    GRADE_C_DANGER,
    OIFilter,
)
from strategy.regime_detector import Regime


# ── helpers ─────────────────────────────────────────────────────────

def _candidate(*, oi_change=10.0, price_change=5.0, volume_24h=50_000_000.0):
    return Candidate(
        symbol="SOLUSDT",
        price=150.0,
        oi_now=1100.0,
        oi_change_pct=oi_change,
        price_change_pct=price_change,
        volume_24h=volume_24h,
    )


def _regime(regime=Regime.TREND_UP, confidence=0.8):
    return SimpleNamespace(regime=regime, confidence=confidence)


@pytest.fixture
def oi_filter() -> OIFilter:
    return OIFilter()


# ── 1~2. A_STRONG ───────────────────────────────────────────────────

def test_aligned_long_high_confidence_is_a_strong(oi_filter):
    """가격↑ + TREND_UP + 신뢰도 0.8 → A_STRONG, LONG, score 80."""
    r = oi_filter.evaluate(_candidate(price_change=5.0),
                           _regime(Regime.TREND_UP, 0.8))
    assert r.grade == GRADE_A_STRONG
    assert r.action == "LONG"
    assert r.score == 80


def test_aligned_short_high_confidence_is_a_strong(oi_filter):
    """가격↓ + TREND_DOWN + 신뢰도 0.8 → A_STRONG, SHORT."""
    r = oi_filter.evaluate(_candidate(price_change=-5.0),
                           _regime(Regime.TREND_DOWN, 0.8))
    assert r.grade == GRADE_A_STRONG
    assert r.action == "SHORT"
    assert r.score == 80


# ── 3. B_MODERATE (정렬, 낮은 신뢰도) ───────────────────────────────

def test_aligned_low_confidence_is_b_moderate(oi_filter):
    """추세 정렬이나 신뢰도 0.5 < 0.7 → B_MODERATE, score 65."""
    r = oi_filter.evaluate(_candidate(price_change=5.0),
                           _regime(Regime.TREND_UP, 0.5))
    assert r.grade == GRADE_B_MODERATE
    assert r.score == 65


# ── 4. B_MODERATE (중립 레짐) ───────────────────────────────────────

@pytest.mark.parametrize("regime", [Regime.RANGING, Regime.UNCERTAIN])
def test_neutral_regime_is_b_moderate(oi_filter, regime):
    """RANGING/UNCERTAIN 레짐 → B_MODERATE, score 55."""
    r = oi_filter.evaluate(_candidate(price_change=5.0), _regime(regime, 0.9))
    assert r.grade == GRADE_B_MODERATE
    assert r.score == 55


# ── 5~6. 역추세 → C_DANGER ──────────────────────────────────────────

def test_counter_trend_long_is_danger(oi_filter):
    """가격↑(LONG)인데 regime=TREND_DOWN → 역추세 C_DANGER."""
    r = oi_filter.evaluate(_candidate(price_change=5.0),
                           _regime(Regime.TREND_DOWN, 0.8))
    assert r.grade == GRADE_C_DANGER
    assert any("역추세 LONG" in x for x in r.reasons)


def test_counter_trend_short_is_danger(oi_filter):
    """가격↓(SHORT)인데 regime=TREND_UP → 역추세 C_DANGER."""
    r = oi_filter.evaluate(_candidate(price_change=-5.0),
                           _regime(Regime.TREND_UP, 0.8))
    assert r.grade == GRADE_C_DANGER
    assert any("역추세 SHORT" in x for x in r.reasons)


# ── 7. OI 극단 → C_DANGER ───────────────────────────────────────────

def test_extreme_oi_change_is_danger(oi_filter):
    """OI 변화율 > 50% → 조작/저유동성 의심 C_DANGER."""
    r = oi_filter.evaluate(_candidate(oi_change=80.0, price_change=5.0),
                           _regime(Regime.TREND_UP, 0.8))
    assert r.grade == GRADE_C_DANGER
    assert any("OI 변화 극단" in x for x in r.reasons)


# ── 8. 저유동성 → C_DANGER ──────────────────────────────────────────

def test_low_volume_is_danger(oi_filter):
    """24h 거래대금 < $10M → 저유동성 C_DANGER."""
    r = oi_filter.evaluate(_candidate(volume_24h=1_000_000.0, price_change=5.0),
                           _regime(Regime.TREND_UP, 0.8))
    assert r.grade == GRADE_C_DANGER
    assert any("거래대금 부족" in x for x in r.reasons)


# ── 9. HIGH_VOL 방어 → C_DANGER ─────────────────────────────────────

def test_high_vol_regime_is_danger(oi_filter):
    """regime=HIGH_VOL 방어 가드 → C_DANGER (main 차단 누락 대비 이중 가드)."""
    r = oi_filter.evaluate(_candidate(price_change=5.0),
                           _regime(Regime.HIGH_VOL, 0.9))
    assert r.grade == GRADE_C_DANGER
    assert any("HIGH_VOL" in x for x in r.reasons)


# ── 10. 가격 0% → C_DANGER ──────────────────────────────────────────

def test_zero_price_change_is_danger(oi_filter):
    """가격 변화율 0% → 방향 불명 C_DANGER."""
    r = oi_filter.evaluate(_candidate(price_change=0.0),
                           _regime(Regime.TREND_UP, 0.8))
    assert r.grade == GRADE_C_DANGER
    assert any("방향 불명" in x for x in r.reasons)


# ── 11. action 방향 ─────────────────────────────────────────────────

def test_action_direction_follows_price_sign(oi_filter):
    """가격 부호가 action 방향을 결정한다 (중립 레짐 기준)."""
    long_r = oi_filter.evaluate(_candidate(price_change=3.0),
                                _regime(Regime.RANGING, 0.6))
    short_r = oi_filter.evaluate(_candidate(price_change=-3.0),
                                 _regime(Regime.RANGING, 0.6))
    assert long_r.action == "LONG"
    assert short_r.action == "SHORT"
