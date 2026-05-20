"""
tests/test_cost_guard.py
=====================================================================
CostGuard 단위 테스트 — 명세서 §8-2-3 필수 8개 시나리오.

  1. R:R 2.5:1, W=55%, Tier 1 → passed=True
  2. R:R 1.5:1, W=50%, Tier 2 → passed=False (W 부족)
  3. 데이터 부족 (n<10) → default_win_rate 사용
  4. SL > Entry (LONG) → passed=False, "비합리적 TP/SL" 사유
  5. position_usdt=0 → passed=False
  6. action 오타 → passed=False
  7. Tier 3 (slippage 0.15%): 동일 R:R에서 Tier 1 대비 더 까다로움
  8. get_min_winrate_for_passing() — R:R 2.0:1, Tier 1, $100 → 약 0.42 반환

외부 호출 없음 (mock 불필요).
=====================================================================
"""

from __future__ import annotations

from strategy.cost_guard import CostGuard, CostGuardResult


# ── 시나리오 1: R:R 2.5:1, W=55%, Tier 1 → passed=True ──
def test_scenario_1_rr_2_5_winrate_55_tier1_passes():
    guard = CostGuard()
    guard.update_win_rates({"trend_long": 0.55}, sample_counts={"trend_long": 30})

    # entry 50000 / tp 51000 (+2.0%) / sl 49600 (-0.8%) → R:R = 2.5:1
    result = guard.check(
        setup_tag="trend_long",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=100, action="LONG", pair_tier=1,
    )

    assert isinstance(result, CostGuardResult)
    assert result.passed is True
    assert result.expected_value > 0
    assert result.assumed_win_rate == 0.55          # 실측 승률 사용
    assert "empirical" in result.reason
    assert result.expected_value == 0.6585          # 0.55*1.9185 - 0.45*0.8815


# ── 시나리오 2: R:R 1.5:1, W=50%, Tier 2 → passed=False ──
def test_scenario_2_rr_1_5_winrate_50_tier2_blocked():
    guard = CostGuard()
    guard.update_win_rates({"weak_setup": 0.50}, sample_counts={"weak_setup": 25})

    # entry 50000 / tp 50300 (+0.6%) / sl 49800 (-0.4%) → R:R = 1.5:1
    # 작은 손익폭이라 왕복 비용 비중이 커져 EV가 음수가 된다.
    result = guard.check(
        setup_tag="weak_setup",
        entry_price=50000, tp_price=50300, sl_price=49800,
        position_usdt=100, action="LONG", pair_tier=2,
    )

    assert result.passed is False
    assert result.expected_value < 0
    assert result.assumed_win_rate == 0.50
    assert "차단" in result.reason


# ── 시나리오 3: 데이터 부족 (n<10) → default_win_rate 사용 ──
def test_scenario_3_insufficient_samples_uses_default():
    guard = CostGuard(default_win_rate=0.45)
    # 표본 5개 (< min_samples 10) → 실측 승률이 있어도 default 사용
    guard.update_win_rates({"new_setup": 0.70}, sample_counts={"new_setup": 5})

    result = guard.check(
        setup_tag="new_setup",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=100, action="LONG", pair_tier=1,
    )

    assert result.assumed_win_rate == 0.45          # 0.70이 아닌 default
    assert "default" in result.reason
    assert "5<10" in result.reason

    # 표본 자체가 전혀 없는 경우도 default
    result_no_data = guard.check(
        setup_tag="unknown_tag",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=100, action="LONG", pair_tier=1,
    )
    assert result_no_data.assumed_win_rate == 0.45
    assert "default" in result_no_data.reason


# ── 시나리오 4: SL > Entry (LONG) → passed=False, "비합리적 TP/SL" ──
def test_scenario_4_sl_above_entry_long_blocked():
    guard = CostGuard()

    # LONG인데 sl(50500) > entry(50000) → risk% 음수
    result = guard.check(
        setup_tag="trend_long",
        entry_price=50000, tp_price=51000, sl_price=50500,
        position_usdt=100, action="LONG", pair_tier=1,
    )

    assert result.passed is False
    assert "비합리적 TP/SL" in result.reason
    assert result.expected_value == 0


# ── 시나리오 5: position_usdt=0 → passed=False ──
def test_scenario_5_zero_position_blocked():
    guard = CostGuard()

    result = guard.check(
        setup_tag="trend_long",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=0, action="LONG", pair_tier=1,
    )

    assert result.passed is False
    assert result.reason == "잘못된 입력값"

    # 음수 entry_price도 동일하게 차단
    result_neg = guard.check(
        setup_tag="trend_long",
        entry_price=-1, tp_price=51000, sl_price=49600,
        position_usdt=100, action="LONG", pair_tier=1,
    )
    assert result_neg.passed is False
    assert result_neg.reason == "잘못된 입력값"


# ── 시나리오 6: action 오타 → passed=False ──
def test_scenario_6_invalid_action_blocked():
    guard = CostGuard()

    result = guard.check(
        setup_tag="trend_long",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=100, action="BUY", pair_tier=1,   # "LONG" 오타
    )

    assert result.passed is False
    assert "잘못된 action" in result.reason
    assert "BUY" in result.reason


# ── 시나리오 7: Tier 3 슬리피지 차등 (동일 R:R에서 더 까다로움) ──
def test_scenario_7_tier3_stricter_than_tier1():
    guard = CostGuard()
    guard.update_win_rates({"trend_long": 0.55}, sample_counts={"trend_long": 30})

    common = dict(
        setup_tag="trend_long",
        entry_price=50000, tp_price=51000, sl_price=49600,
        position_usdt=100, action="LONG",
    )
    result_t1 = guard.check(**common, pair_tier=1)
    result_t3 = guard.check(**common, pair_tier=3)

    # Tier 3 슬리피지(0.15%)가 Tier 1(0.05%)보다 크므로
    # 비용↑ → 기대값↓ 이어야 한다.
    assert result_t3.slippage_assumed == 0.0015
    assert result_t1.slippage_assumed == 0.0005
    assert result_t3.estimated_cost > result_t1.estimated_cost
    assert result_t3.expected_value < result_t1.expected_value
    # 통과에 필요한 최소 승률도 Tier 3이 더 높다.
    w_t1 = guard.get_min_winrate_for_passing(50000, 51000, 49600, 100, "LONG", 1)
    w_t3 = guard.get_min_winrate_for_passing(50000, 51000, 49600, 100, "LONG", 3)
    assert w_t3 > w_t1


# ── 시나리오 8: get_min_winrate_for_passing() — R:R 2.0:1, Tier 1, $100 ──
def test_scenario_8_get_min_winrate_for_passing():
    guard = CostGuard()

    # entry 50000 / tp 50300 (+0.6%) / sl 49850 (-0.3%) → R:R = 2.0:1
    w_min = guard.get_min_winrate_for_passing(
        entry_price=50000, tp_price=50300, sl_price=49850,
        position_usdt=100, action="LONG", pair_tier=1,
    )

    # 정확값 ≈ 0.4239, 부동소수점 견고성 위해 tolerance ±0.03
    assert abs(w_min - 0.42) <= 0.03
    # 비용 무시 시(순수 R:R 2:1)의 손익분기 0.333보다는 높아야 한다.
    assert w_min > 1 / 3

    # 비합리적 입력(LONG인데 sl>entry)은 1.0 반환
    w_bad = guard.get_min_winrate_for_passing(
        entry_price=50000, tp_price=50300, sl_price=50500,
        position_usdt=100, action="LONG", pair_tier=1,
    )
    assert w_bad == 1.0
