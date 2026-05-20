"""
tests/test_quality_gate.py
=====================================================================
QualityGate 단위 테스트.

근거: docs/SPEC_v3.1.md §8-8 (Layer 3), D-2 (QualityGate.check() → required_score)

구성:
  - Candidate 는 data.oi_scanner.Candidate 실제 dataclass 사용
  - oi_result 는 strategy.oi_filter.OIResult 실제 dataclass 사용

검증 시나리오:
  1. score >= required_score → 통과
  2. score < required_score → 차단
  3. OIFilter C_DANGER → 점수 무관 차단
  4. tp/sl LONG 계산 (R:R 2:1)
  5. tp/sl SHORT 계산
  6. 거래대금 보너스 (+5 / +10)
  7. OI 변화 보너스 (+5)
  8. 가격 과열 패널티 (-10)
  9. setup_tag = oi_surge_{action}
 10. to_dict 직렬화
 11. tp_pct/sl_pct <= 0 → ValueError
=====================================================================
"""

from __future__ import annotations

import pytest

from data.oi_scanner import Candidate
from strategy.oi_filter import (
    GRADE_A_STRONG,
    GRADE_B_MODERATE,
    GRADE_C_DANGER,
    OIResult,
)
from strategy.quality_gate import QualityGate, QualityResult


# ── helpers ─────────────────────────────────────────────────────────

def _candidate(*, price=100.0, oi_change=10.0, price_change=5.0,
               volume_24h=50_000_000.0):
    return Candidate(
        symbol="SOLUSDT",
        price=price,
        oi_now=1100.0,
        oi_change_pct=oi_change,
        price_change_pct=price_change,
        volume_24h=volume_24h,
    )


def _oi_result(*, grade=GRADE_A_STRONG, score=80, action="LONG"):
    return OIResult(
        symbol="SOLUSDT",
        grade=grade,
        score=score,
        action=action,
        oi_change_pct=10.0,
        price_change_pct=5.0,
        reasons=[],
    )


@pytest.fixture
def gate() -> QualityGate:
    return QualityGate()


# ── 1~2. 임계 통과/차단 ────────────────────────────────────────────

def test_score_above_required_passes(gate):
    """최종 점수 >= required_score → 통과."""
    # base 80 + 거래대금 50M(<100M, 보너스 없음) + 가격 5%(+3) = 83
    r = gate.check(_candidate(), _oi_result(score=80), required_score=55)
    assert r.passed is True
    assert r.score >= 55


def test_score_below_required_blocked(gate):
    """최종 점수 < required_score → 차단."""
    # base 55 + 가격 5%(+3) = 58 < 65
    r = gate.check(_candidate(), _oi_result(grade=GRADE_B_MODERATE, score=55),
                   required_score=65)
    assert r.passed is False


# ── 3. C_DANGER 방어 ────────────────────────────────────────────────

def test_c_danger_blocked_regardless_of_score(gate):
    """OIFilter grade=C_DANGER → 점수와 무관하게 차단."""
    r = gate.check(_candidate(), _oi_result(grade=GRADE_C_DANGER, score=0,
                                            action=""), required_score=1)
    assert r.passed is False
    assert any("C_DANGER" in x for x in r.reasons)


# ── 4~5. tp/sl 계산 ─────────────────────────────────────────────────

def test_tp_sl_long(gate):
    """LONG: tp = entry×1.02, sl = entry×0.99 (R:R 2:1)."""
    r = gate.check(_candidate(price=100.0), _oi_result(action="LONG"),
                   required_score=50)
    assert r.tp == pytest.approx(102.0)
    assert r.sl == pytest.approx(99.0)


def test_tp_sl_short(gate):
    """SHORT: tp = entry×0.98, sl = entry×1.01."""
    r = gate.check(_candidate(price=100.0), _oi_result(action="SHORT"),
                   required_score=50)
    assert r.tp == pytest.approx(98.0)
    assert r.sl == pytest.approx(101.0)


# ── 6. 거래대금 보너스 ──────────────────────────────────────────────

def test_volume_bonus_tiers(gate):
    """거래대금 100M → +5, 500M → +10."""
    base = gate.check(_candidate(volume_24h=50_000_000.0),
                      _oi_result(score=60), required_score=1).score
    mid = gate.check(_candidate(volume_24h=150_000_000.0),
                     _oi_result(score=60), required_score=1).score
    high = gate.check(_candidate(volume_24h=600_000_000.0),
                      _oi_result(score=60), required_score=1).score
    assert mid - base == 5
    assert high - base == 10


# ── 7. OI 변화 보너스 ───────────────────────────────────────────────

def test_oi_change_bonus(gate):
    """OI 변화율 >= 15% → +5."""
    low = gate.check(_candidate(oi_change=10.0), _oi_result(score=60),
                     required_score=1).score
    high = gate.check(_candidate(oi_change=20.0), _oi_result(score=60),
                      required_score=1).score
    assert high - low == 5


# ── 8. 가격 과열 패널티 ─────────────────────────────────────────────

def test_price_overheated_penalty(gate):
    """가격 변화율 절댓값 > 15% → -10 패널티."""
    normal = gate.check(_candidate(price_change=5.0), _oi_result(score=80),
                        required_score=1).score
    overheated = gate.check(_candidate(price_change=20.0), _oi_result(score=80),
                            required_score=1).score
    # normal: +3 (5%), overheated: -10 → 차이 13
    assert normal - overheated == 13


# ── 9. setup_tag ────────────────────────────────────────────────────

def test_setup_tag_follows_action(gate):
    """setup_tag = oi_surge_{action소문자}."""
    long_r = gate.check(_candidate(), _oi_result(action="LONG"),
                        required_score=50)
    short_r = gate.check(_candidate(), _oi_result(action="SHORT"),
                         required_score=50)
    assert long_r.setup_tag == "oi_surge_long"
    assert short_r.setup_tag == "oi_surge_short"


# ── 10. to_dict ─────────────────────────────────────────────────────

def test_to_dict_serializable(gate):
    """to_dict() 는 decision 구성용 dict 를 반환한다."""
    r = gate.check(_candidate(), _oi_result(), required_score=50)
    d = r.to_dict()
    assert isinstance(d, dict)
    assert d["passed"] == r.passed
    assert d["setup_tag"] == r.setup_tag
    assert set(d) >= {"passed", "score", "setup_tag", "action",
                      "entry_price", "tp", "sl"}


# ── 11. 입력 검증 ───────────────────────────────────────────────────

def test_nonpositive_pct_raises():
    """tp_pct / sl_pct <= 0 → ValueError."""
    with pytest.raises(ValueError):
        QualityGate(tp_pct=0.0)
    with pytest.raises(ValueError):
        QualityGate(sl_pct=-0.01)
