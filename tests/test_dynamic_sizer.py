"""
tests/test_dynamic_sizer.py
=====================================================================
DynamicPositionSizer 단위 테스트 — 명세서 §8-3-3 필수 7개 시나리오.

  1. $1,000, W=55%, R=2.0/1.0, TREND_UP, conf=0.8, n=30
     → Quarter-Kelly (kelly_fraction_used == 0.25), 0 < size_pct < 5%
  2. $5,000, W=60%, R=2.0/1.0, TREND_UP, conf=0.8, n=30
     → Half-Kelly (kelly_fraction_used == 0.50), size_pct == 10.0% (cap 적용)
  3. regime=HIGH_VOL → size_pct == 0, reason에 "HIGH_VOL"
  4. W=0.3, R=1.5/1.0 (Kelly 음수) → size_pct == 0, kelly_raw < 0
  5. sample_count=5 → 추가 50% 축소 (reason에 "표본 부족")
  6. confidence=0.3 → 0.5로 자동 상향 (confidence_factor == 0.5)
  7. $20,000, RANGING → capital_cap 12%, regime_cap 6%, final_cap 6%

v3.1.2 정정 반영 (사용자 승인 — 세션 4):
  - #1: kelly_fraction_for_capital 경계값 `<= 1000` 정정 → $1,000도 Quarter-Kelly
  - #2: 본문 "~6%"는 cap 미반영 부정확 추정치. 실제 10.0% (regime/capital cap 둘 다 10%)
  - #4: _zero_result 인자 순서 버그 수정 → kelly_raw 필드에 원본 Kelly(음수)

외부 호출 없음 (mock 불필요).
=====================================================================
"""

from __future__ import annotations

import pytest

from sizing.dynamic_sizer import DynamicPositionSizer, SizingResult


# ── 시나리오 1: $1,000 → Quarter-Kelly, 0 < size_pct < 5% ──
def test_scenario_1_capital_1000_quarter_kelly():
    sizer = DynamicPositionSizer()

    # kelly_raw = 0.55/1.0 - 0.45/2.0 = 0.325
    # k_frac=0.25 → 0.08125, n=30 penalty 0.75 → 0.0609375,
    # conf 0.8 → 0.04875, cap(8%)/min(2%) 미적용 → size_pct = 4.875%
    result = sizer.calculate(
        capital=1000,
        win_rate=0.55,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP",
        confidence=0.8,
        sample_count=30,
    )

    assert isinstance(result, SizingResult)
    assert result.kelly_fraction_used == 0.25          # Quarter-Kelly 적용
    assert result.kelly_raw == pytest.approx(0.325, abs=1e-6)
    assert 0 < result.size_pct < 5                     # 0 < size_pct < 5%
    assert result.size_pct == pytest.approx(4.875, abs=1e-6)
    assert result.size_usdt == pytest.approx(48.75, abs=1e-6)
    assert result.confidence_factor == 0.8


# ── 시나리오 2: $5,000 → Half-Kelly, size_pct == 10.0% (cap) ──
def test_scenario_2_capital_5000_half_kelly_capped():
    sizer = DynamicPositionSizer()

    # kelly_raw = 0.60/1.0 - 0.40/2.0 = 0.40
    # k_frac=0.50 → 0.20, n=30 penalty 0.75 → 0.15, conf 0.8 → 0.12
    # regime_cap(TREND_UP)=10%, capital_cap=10% → final_cap=10% → size_pct=10.0%
    result = sizer.calculate(
        capital=5000,
        win_rate=0.60,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP",
        confidence=0.8,
        sample_count=30,
    )

    assert result.kelly_fraction_used == 0.50          # Half-Kelly 적용
    assert result.kelly_raw == pytest.approx(0.40, abs=1e-6)
    assert result.size_pct == pytest.approx(10.0, abs=1e-6)   # cap 적용 (본문 ~6%는 부정확)
    assert result.size_usdt == pytest.approx(500.0, abs=1e-6)
    assert result.final_cap_pct == 10.0
    assert "cap 적용" in result.reason


# ── 시나리오 3: HIGH_VOL → size_pct == 0 ──
def test_scenario_3_high_vol_blocked():
    sizer = DynamicPositionSizer()

    result = sizer.calculate(
        capital=5000,
        win_rate=0.60,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="HIGH_VOL",
        confidence=0.8,
        sample_count=30,
    )

    assert result.size_pct == 0
    assert result.size_usdt == 0
    assert "HIGH_VOL" in result.reason


# ── 시나리오 4: Kelly 음수 (W=0.3, R=1.5/1.0) → size_pct == 0, kelly_raw < 0 ──
def test_scenario_4_negative_kelly_blocked():
    sizer = DynamicPositionSizer()

    # kelly_raw = 0.3/1.0 - 0.7/1.5 = 0.3 - 0.46667 = -0.16667 < 0
    result = sizer.calculate(
        capital=5000,
        win_rate=0.30,
        avg_win_R=1.5, avg_loss_R=1.0,
        regime="TREND_UP",
        confidence=0.8,
        sample_count=30,
    )

    assert result.size_pct == 0
    assert result.size_usdt == 0
    assert result.kelly_raw < 0                        # 원본 Kelly 음수 (v3.1.2 인자 순서 정정)
    assert result.kelly_raw == pytest.approx(-1 / 6, abs=1e-6)
    assert "Kelly 음수" in result.reason


# ── 시나리오 5: sample_count=5 → 추가 50% 축소 ──
def test_scenario_5_low_sample_count_penalty():
    sizer = DynamicPositionSizer()

    # kelly_raw=0.40, k_frac=0.50 → 0.20, n=5 penalty 0.5 → 0.10,
    # conf 0.8 → 0.08, cap 미적용 → size_pct = 8.0%
    result = sizer.calculate(
        capital=5000,
        win_rate=0.60,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP",
        confidence=0.8,
        sample_count=5,
    )

    assert "표본 부족" in result.reason
    assert "50% 축소" in result.reason
    assert result.size_pct == pytest.approx(8.0, abs=1e-6)
    assert result.size_usdt == pytest.approx(400.0, abs=1e-6)


# ── 시나리오 6: confidence=0.3 → 0.5로 자동 상향 ──
def test_scenario_6_low_confidence_raised_to_floor():
    sizer = DynamicPositionSizer()

    # conf_factor = max(0.5, 0.3) = 0.5
    # kelly_raw=0.40, k_frac=0.50 → 0.20, n=30 penalty 0.75 → 0.15,
    # conf 0.5 → 0.075, cap 미적용 → size_pct = 7.5%
    result = sizer.calculate(
        capital=5000,
        win_rate=0.60,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP",
        confidence=0.3,
        sample_count=30,
    )

    assert result.confidence_factor == 0.5             # 0.3 → 0.5 자동 상향
    assert result.size_pct == pytest.approx(7.5, abs=1e-6)
    assert result.size_usdt == pytest.approx(375.0, abs=1e-6)


# ── 시나리오 7: $20,000 RANGING → capital_cap 12%, regime_cap 6%, final 6% ──
def test_scenario_7_capital_20000_ranging_caps():
    sizer = DynamicPositionSizer()

    # kelly_raw=0.40, k_frac=0.50 → 0.20, n=30 penalty 0.75 → 0.15,
    # conf 0.8 → 0.12, regime_cap(RANGING)=6%, capital_cap=12% → final_cap=6%
    result = sizer.calculate(
        capital=20000,
        win_rate=0.60,
        avg_win_R=2.0, avg_loss_R=1.0,
        regime="RANGING",
        confidence=0.8,
        sample_count=30,
    )

    assert result.regime_cap_pct == 6.0
    assert result.capital_cap_pct == 12.0
    assert result.final_cap_pct == 6.0
    assert result.size_pct == pytest.approx(6.0, abs=1e-6)
    assert result.size_usdt == pytest.approx(1200.0, abs=1e-6)
    assert "cap 적용" in result.reason
