"""
tests/test_skills_base.py
=====================================================================
StrategySkill abstract base 검증.

근거: skills/base.py + SYSTEM_DESIGN_BLUEPRINT.md §3.2.1
=====================================================================
"""

from __future__ import annotations

import hashlib
import json

import pytest

from audit.signal_decision import SignalDecision
from skills.base import StrategySkill


def test_params_hash_auto_computed():
    """자식 클래스의 PARAMS_HASH 자동 계산 (__init_subclass__)."""

    class MySkill(StrategySkill):
        SETUP_ID = "test_v1"
        PARAMS = {"a": 1, "b": 2}
        CATEGORY = "test"
        ACADEMIC_REF = "test"

    expected = hashlib.sha256(
        json.dumps({"a": 1, "b": 2}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert MySkill.PARAMS_HASH == expected


def test_different_params_different_hash():
    """다른 PARAMS → 다른 PARAMS_HASH."""

    class SkillA(StrategySkill):
        SETUP_ID = "a"
        PARAMS = {"k": 1}

    class SkillB(StrategySkill):
        SETUP_ID = "b"
        PARAMS = {"k": 2}

    assert SkillA.PARAMS_HASH != SkillB.PARAMS_HASH


def test_empty_params_empty_hash():
    """PARAMS = {} 면 PARAMS_HASH = '' (자동 X)."""

    class NoParams(StrategySkill):
        SETUP_ID = "x"
        PARAMS = {}

    assert NoParams.PARAMS_HASH == ""


def test_evaluate_raises_not_implemented():
    """abstract evaluate() — 자식이 구현 안 하면 NotImplementedError."""

    class NoImpl(StrategySkill):
        SETUP_ID = "noimpl"
        PARAMS = {"k": 1}

    with pytest.raises(NotImplementedError):
        NoImpl().evaluate("BTC", [], {})


def test_build_flat_decision_returns_signal_decision():
    """_build_flat_decision() — FLAT SignalDecision 생성."""

    class TestSkill(StrategySkill):
        SETUP_ID = "test"
        PARAMS = {"k": 1}

    d = TestSkill()._build_flat_decision(
        symbol="SOLUSDT",
        reasoning="test reason",
        raw_data_hash="a" * 64,
    )
    assert isinstance(d, SignalDecision)
    assert d.action == "FLAT"
    assert d.confidence == 0.0
    assert d.symbol == "SOLUSDT"


def test_compute_raw_data_hash_deterministic():
    """동일 candles → 동일 raw_data_hash."""

    class TestSkill(StrategySkill):
        SETUP_ID = "test"
        PARAMS = {"k": 1}

    candles = [(1.0, 2.0, 0.5, 1.5, 100.0, 1234)] * 5
    h1 = TestSkill()._compute_raw_data_hash(candles)
    h2 = TestSkill()._compute_raw_data_hash(candles)
    assert h1 == h2
    assert len(h1) == 64  # sha256


def test_params_hash_order_independent():
    """dict 키 순서 무관 — 같은 PARAMS 면 같은 hash."""
    # 같은 dict 이라도 Python dict 는 insertion-ordered. 명시적으로 다른 순서:

    class S1(StrategySkill):
        SETUP_ID = "s1"
        PARAMS = {"a": 1, "b": 2, "c": 3}

    class S2(StrategySkill):
        SETUP_ID = "s2"
        PARAMS = {"c": 3, "b": 2, "a": 1}

    # sort_keys=True 적용되므로 동일
    assert S1.PARAMS_HASH == S2.PARAMS_HASH
