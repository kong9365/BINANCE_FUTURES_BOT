"""
tests/test_skills_candidate_context.py
=====================================================================
CandidateContextSkill 검증 — Macro/OI 차단 (직접 거래 결정 X).
=====================================================================
"""

from __future__ import annotations

import pytest

from skills.candidate_context_skill import CandidateContextSkill


def _candles_minimal() -> list:
    return [(1.0, 2.0, 0.5, 1.5, 100.0, 1234)] * 10


def test_setup_id_and_category():
    assert CandidateContextSkill.SETUP_ID == "candidate_context_v1"
    assert CandidateContextSkill.CATEGORY == "context"


def test_no_block_on_normal_state():
    """모든 차단 조건 false → continue marker (FLAT + confidence=0.9)."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={
            "macro_blocked": False,
            "oi_grade": "A_STRONG",
            "regime": "TREND_UP",
            "btc_risk_off": False,
        },
    )
    assert decision.action == "FLAT"  # 직접 거래 결정 X (TIER 1 #10)
    assert decision.confidence == 0.9  # CONTINUE marker
    assert "no blocking" in decision.reasoning.lower()


def test_block_on_macro_event():
    """Macro 이벤트 임박 → 차단."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={
            "macro_blocked": True,
            "oi_grade": "A_STRONG",
        },
    )
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0  # 차단 (CONTINUE marker 아님)
    assert "Macro" in decision.reasoning


def test_block_on_oi_danger():
    """OI C_DANGER → 차단."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={"oi_grade": "C_DANGER"},
    )
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0
    assert "C_DANGER" in decision.reasoning


def test_block_on_high_vol_regime():
    """HIGH_VOL 레짐 → 차단."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={"regime": "HIGH_VOL"},
    )
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0
    assert "HIGH_VOL" in decision.reasoning


def test_block_on_btc_risk_off():
    """btc_risk_off 활성 → 차단."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={"btc_risk_off": True},
    )
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0
    assert "BTC Risk-Off" in decision.reasoning


def test_multiple_blocks_concatenated():
    """여러 차단 사유 모두 reasoning 에 표시."""
    skill = CandidateContextSkill()
    decision = skill.evaluate(
        "SOLUSDT",
        _candles_minimal(),
        market_state={
            "macro_blocked": True,
            "oi_grade": "C_DANGER",
            "regime": "HIGH_VOL",
            "btc_risk_off": True,
        },
    )
    assert decision.action == "FLAT"
    assert decision.confidence == 0.0
    assert "Macro" in decision.reasoning
    assert "C_DANGER" in decision.reasoning
    assert "HIGH_VOL" in decision.reasoning
    assert "BTC" in decision.reasoning


def test_no_direct_long_short_decision():
    """TIER 1 #10: 본 Skill 은 *절대* LONG/SHORT 직접 결정 X."""
    skill = CandidateContextSkill()
    # 모든 가능한 market_state 시뮬레이션
    for ms in [
        None,
        {},
        {"macro_blocked": False, "oi_grade": "A_STRONG"},
        {"regime": "TREND_UP"},
        {"btc_risk_off": False, "regime": "RANGING"},
    ]:
        decision = skill.evaluate("SOLUSDT", _candles_minimal(), market_state=ms)
        assert decision.action == "FLAT", f"market_state={ms} action={decision.action}"
