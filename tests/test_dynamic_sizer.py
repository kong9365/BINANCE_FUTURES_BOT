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


# =====================================================================
# A1 [2.2] — risk-per-trade 기반 notional cap (손절거리 반영, min 결합)
# 불변식: 산출 notional 의 손절 손실(qty×|entry−sl|)이 risk_pct×capital 을
#         절대 초과하지 않는다. 사이즈는 기존 cap 대비 줄어들기만 한다.
# =====================================================================


def _loss_at_stop(size_usdt: float, entry: float, sl: float) -> float:
    """포지션을 손절가에 청산했을 때의 손실(USDT) = qty × |entry−sl|."""
    qty = size_usdt / entry
    return qty * abs(entry - sl)


def test_a1_risk_cap_binds_and_caps_loss_to_half_pct():
    """손절거리가 넓어 risk-cap 이 binding → notional 이 0.5% 룰로 축소."""
    sizer = DynamicPositionSizer()
    # baseline(시나리오 2 동일): size_usdt = 500 (10% of 5000)
    # entry=100, sl=90 (10% 손절거리) → risk_cap = 0.005×5000×100/10 = $250
    result = sizer.calculate(
        capital=5000, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
        entry_price=100.0, stop_loss=90.0, risk_per_trade_pct=0.005,
    )
    assert result.size_usdt == pytest.approx(250.0, abs=1e-6)   # 500 → 250 (risk cap)
    assert _loss_at_stop(result.size_usdt, 100.0, 90.0) == pytest.approx(25.0, abs=1e-6)
    assert _loss_at_stop(result.size_usdt, 100.0, 90.0) <= 0.005 * 5000 + 1e-9
    assert "risk cap" in result.reason


def test_a1_risk_cap_not_binding_leaves_size_unchanged():
    """손절거리가 좁아 risk-cap > baseline → 사이즈 불변(cap 은 줄이기만)."""
    sizer = DynamicPositionSizer()
    # entry=100, sl=99.9 (0.1% 손절거리) → risk_cap = 0.005×5000×100/0.1 = $25,000 ≫ 500
    result = sizer.calculate(
        capital=5000, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
        entry_price=100.0, stop_loss=99.9, risk_per_trade_pct=0.005,
    )
    assert result.size_usdt == pytest.approx(500.0, abs=1e-6)   # 불변
    assert "risk cap" not in result.reason


def test_a1_risk_cap_can_go_below_min_size_floor():
    """손절거리가 매우 넓으면 risk-cap 이 min_size(2%) 아래로도 내려간다(리스크 우선)."""
    sizer = DynamicPositionSizer()
    # entry=100, sl=70 (30% 손절거리) → risk_cap = 0.005×5000×100/30 = $83.33
    # min_size 2% = $100 보다 작지만 risk-cap 이 hard ceiling 으로 우선.
    result = sizer.calculate(
        capital=5000, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
        entry_price=100.0, stop_loss=70.0, risk_per_trade_pct=0.005,
    )
    assert result.size_usdt == pytest.approx(83.333, abs=1e-2)
    assert result.size_usdt < 100.0                              # min_size 하한 아래
    assert _loss_at_stop(result.size_usdt, 100.0, 70.0) <= 0.005 * 5000 + 1e-6


def test_a1_zero_stop_distance_skips_risk_cap_no_zerodiv():
    """sl==entry(손절거리 0) → ZeroDivision 없이 risk-cap 생략, 기존 cap 만 적용."""
    sizer = DynamicPositionSizer()
    result = sizer.calculate(
        capital=5000, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
        entry_price=100.0, stop_loss=100.0, risk_per_trade_pct=0.005,
    )
    assert result.size_usdt == pytest.approx(500.0, abs=1e-6)   # baseline 유지
    assert "risk-cap 생략" in result.reason


def test_a1_no_risk_params_is_backward_compatible():
    """entry/sl/risk_pct 미제공 → 기존 동작 100% 동일(시나리오 2와 일치)."""
    sizer = DynamicPositionSizer()
    result = sizer.calculate(
        capital=5000, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
    )
    assert result.size_usdt == pytest.approx(500.0, abs=1e-6)
    assert "risk cap" not in result.reason


@pytest.mark.parametrize("capital,entry,sl", [
    (1000, 50.0, 47.0),
    (5000, 100.0, 92.0),
    (3000, 2.5, 2.1),
    (10000, 30000.0, 28500.0),
    (5000, 100.0, 110.0),   # SHORT 방향(sl>entry)도 |거리| 동일 처리
])
def test_a1_loss_never_exceeds_half_pct_rule(capital, entry, sl):
    """다양한 (capital, entry, sl) 에서 손절 손실이 0.5% 룰을 절대 초과하지 않음."""
    sizer = DynamicPositionSizer()
    result = sizer.calculate(
        capital=capital, win_rate=0.60, avg_win_R=2.0, avg_loss_R=1.0,
        regime="TREND_UP", confidence=0.8, sample_count=30,
        entry_price=entry, stop_loss=sl, risk_per_trade_pct=0.005,
    )
    if result.size_usdt > 0:
        assert _loss_at_stop(result.size_usdt, entry, sl) <= 0.005 * capital + 1e-6
