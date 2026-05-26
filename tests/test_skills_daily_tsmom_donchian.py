"""
tests/test_skills_daily_tsmom_donchian.py
=====================================================================
DailyTSMOMDonchianSkill 검증 — strategy/breakout.py 재사용 wrap.
=====================================================================
"""

from __future__ import annotations

import math

import pytest

from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill


def _make_flat_candles(n: int = 250, base_price: float = 100.0) -> list:
    """평탄한 가격 — Donchian 돌파 없음."""
    return [
        (base_price, base_price + 0.1, base_price - 0.1, base_price, 1000.0, i * 86400)
        for i in range(n)
    ]


def _make_uptrend_candles(n: int = 250) -> list:
    """강한 상승 추세 — Donchian breakout + ADX > 25 + EMA200 위."""
    candles = []
    for i in range(n):
        # 천천히 시작해서 강한 상승 (마지막 50봉)
        if i < n - 50:
            price = 100.0 + i * 0.05
        else:
            # 강력한 추세 (Donchian 20일 채널 명백히 상회)
            price = 100.0 + (n - 50) * 0.05 + (i - (n - 50)) * 3.0
        candles.append(
            (price, price + 0.5, price - 0.5, price, 1000.0, i * 86400)
        )
    return candles


def test_setup_id_and_params_hash():
    """SETUP_ID + PARAMS_HASH 정확."""
    assert DailyTSMOMDonchianSkill.SETUP_ID == "1d_tsmom_donchian_long_v1"
    assert len(DailyTSMOMDonchianSkill.PARAMS_HASH) == 64
    assert DailyTSMOMDonchianSkill.CATEGORY == "trend"


def test_no_signal_on_flat_candles():
    """평탄한 가격 → FLAT (돌파 없음)."""
    skill = DailyTSMOMDonchianSkill()
    candles = _make_flat_candles(250)
    decision = skill.evaluate("SOLUSDT", candles)
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0
    assert "ADX" in decision.reasoning or "조건 미충족" in decision.reasoning


def test_long_signal_on_uptrend():
    """강한 상승 추세 → LONG signal."""
    skill = DailyTSMOMDonchianSkill()
    candles = _make_uptrend_candles(250)
    decision = skill.evaluate("SOLUSDT", candles)
    assert decision.action == "LONG"
    assert decision.confidence >= skill.PARAMS["base_confidence"]
    assert "LONG" in decision.reasoning
    assert "Donchian" in decision.reasoning


def test_signal_decision_fields_populated():
    """SignalDecision 모든 ALCOA+ 필드 채워짐."""
    skill = DailyTSMOMDonchianSkill()
    candles = _make_uptrend_candles(250)
    decision = skill.evaluate("SOLUSDT", candles)
    assert decision.signal_id
    assert decision.setup_id == skill.SETUP_ID
    assert decision.params_hash == skill.PARAMS_HASH
    assert decision.raw_data_hash
    assert decision.features_snapshot_json
    assert decision.ts_signal_generated.tzinfo is not None  # tz-aware


def test_consistency_same_input_same_hash():
    """동일 candles → 동일 raw_data_hash + 동일 params_hash."""
    skill = DailyTSMOMDonchianSkill()
    candles = _make_uptrend_candles(250)
    d1 = skill.evaluate("SOLUSDT", candles)
    d2 = skill.evaluate("SOLUSDT", candles)
    assert d1.raw_data_hash == d2.raw_data_hash
    assert d1.params_hash == d2.params_hash
    # signal_id 만 다름 (UUID4)
    assert d1.signal_id != d2.signal_id


def test_short_signal_on_downtrend():
    """강한 하락 추세 → SHORT signal."""
    skill = DailyTSMOMDonchianSkill()
    # 강력한 하락 패턴
    candles = []
    for i in range(250):
        if i < 200:
            price = 200.0 - i * 0.05
        else:
            # 강력한 하락
            price = 200.0 - 200 * 0.05 - (i - 200) * 3.0
        candles.append(
            (price, price + 0.5, price - 0.5, price, 1000.0, i * 86400)
        )
    decision = skill.evaluate("SOLUSDT", candles)
    assert decision.action == "SHORT"


def test_empty_candles_returns_flat():
    """빈 입력 → FLAT."""
    skill = DailyTSMOMDonchianSkill()
    decision = skill.evaluate("SOLUSDT", [])
    assert decision.action == "FLAT"
    assert "No candles" in decision.reasoning or "조건 미충족" in decision.reasoning


def test_params_immutable_across_instances():
    """PARAMS dict 클래스 attribute — 인스턴스간 공유."""
    s1 = DailyTSMOMDonchianSkill()
    s2 = DailyTSMOMDonchianSkill()
    assert s1.PARAMS is s2.PARAMS  # 같은 객체


def test_academic_ref_present():
    """학계 근거 명시 (M3 StrategyAgent 검증용)."""
    assert "Han·Kang·Ryu" in DailyTSMOMDonchianSkill.ACADEMIC_REF
