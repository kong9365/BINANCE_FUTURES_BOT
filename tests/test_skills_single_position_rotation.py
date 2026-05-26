"""
tests/test_skills_single_position_rotation.py
=====================================================================
SinglePositionRotationSkill 검증 — EV 1위 자동 선정 (청사진 §3.2.3).
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from audit.signal_decision import SignalDecision
from skills.single_position_rotation_skill import SinglePositionRotationSkill


def _make_signal(
    symbol: str,
    ev: float,
    confidence: float = 0.65,
    action: str = "LONG",
) -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id="test",
        params_hash="a" * 64,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="b" * 64,
        features_snapshot_json="{}",
        symbol=symbol,
        action=action,
        confidence=confidence,
        expires_at=now + timedelta(hours=24),
        ev_estimated=ev,
    )


def test_setup_id():
    assert SinglePositionRotationSkill.SETUP_ID == "single_position_rotation_v1"


def test_select_top_picks_highest_ev():
    """EV 1위 선정."""
    skill = SinglePositionRotationSkill()
    candidates = [
        _make_signal("SOLUSDT", ev=0.15),
        _make_signal("AVAXUSDT", ev=0.25),
        _make_signal("LINKUSDT", ev=0.10),
    ]
    top = skill.select_top(candidates, market_state={"current_position_count": 0})
    assert top is not None
    assert top.symbol == "AVAXUSDT"
    assert top.ev_estimated == 0.25
    assert top.rank_in_universe == 1


def test_select_top_returns_none_when_position_full():
    """이미 1개 보유 중이면 None (Single-Position 강제)."""
    skill = SinglePositionRotationSkill()
    candidates = [_make_signal("SOLUSDT", ev=0.20)]
    top = skill.select_top(candidates, market_state={"current_position_count": 1})
    assert top is None


def test_select_top_filters_below_confidence():
    """confidence < 0.55 후보는 필터링."""
    skill = SinglePositionRotationSkill()
    candidates = [
        _make_signal("SOLUSDT", ev=0.30, confidence=0.40),  # 낮은 confidence
        _make_signal("AVAXUSDT", ev=0.10, confidence=0.65),
    ]
    top = skill.select_top(candidates, market_state={"current_position_count": 0})
    assert top is not None
    assert top.symbol == "AVAXUSDT"  # confidence 충족


def test_select_top_filters_negative_ev():
    """EV < 0 후보는 필터링."""
    skill = SinglePositionRotationSkill()
    candidates = [_make_signal("SOLUSDT", ev=-0.05)]
    top = skill.select_top(candidates, market_state={"current_position_count": 0})
    assert top is None


def test_select_top_skips_flat_signals():
    """FLAT action 후보는 진입 대상 아님."""
    skill = SinglePositionRotationSkill()
    candidates = [
        _make_signal("SOLUSDT", ev=0.30, action="FLAT"),
        _make_signal("AVAXUSDT", ev=0.10, action="LONG"),
    ]
    top = skill.select_top(candidates, market_state={"current_position_count": 0})
    assert top is not None
    assert top.symbol == "AVAXUSDT"  # LONG 만 선택


def test_select_top_previous_symbol_cooldown():
    """직전 1위 종목은 cooldown 내 제외 (rotation)."""
    skill = SinglePositionRotationSkill()
    now = datetime.now(timezone.utc)
    candidates = [
        _make_signal("SOLUSDT", ev=0.30),  # 직전 1위 (12h cooldown)
        _make_signal("AVAXUSDT", ev=0.10),
    ]
    top = skill.select_top(
        candidates,
        market_state={
            "current_position_count": 0,
            "previous_symbol": "SOLUSDT",
            "previous_exit_ts": now - timedelta(hours=1),  # 1시간 전 → cooldown 내
        },
    )
    assert top is not None
    assert top.symbol == "AVAXUSDT"


def test_select_top_cooldown_expired():
    """cooldown 만료 후 직전 1위도 후보 가능."""
    skill = SinglePositionRotationSkill()
    now = datetime.now(timezone.utc)
    candidates = [_make_signal("SOLUSDT", ev=0.30)]
    top = skill.select_top(
        candidates,
        market_state={
            "current_position_count": 0,
            "previous_symbol": "SOLUSDT",
            "previous_exit_ts": now - timedelta(hours=13),  # 13시간 전 → cooldown 만료
        },
    )
    assert top is not None
    assert top.symbol == "SOLUSDT"


def test_select_top_empty_candidates():
    """빈 입력 → None."""
    skill = SinglePositionRotationSkill()
    top = skill.select_top([], market_state={"current_position_count": 0})
    assert top is None


def test_evaluate_is_aggregator():
    """단일 symbol evaluate() — aggregator 안내 메시지."""
    skill = SinglePositionRotationSkill()
    decision = skill.evaluate("SOLUSDT", [], {})
    assert decision.action == "FLAT"
    assert "aggregator" in decision.reasoning.lower()
