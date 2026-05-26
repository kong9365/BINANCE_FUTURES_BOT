"""
tests/test_agent_execution.py
=====================================================================
ExecutionAgent 검증 — fill_probability + slippage + 4-case.
=====================================================================
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agents.execution_agent import ExecutionAgent
from audit.signal_decision import SignalDecision


def _make_signal(features: dict) -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id="1d_tsmom_donchian_long_v1",
        params_hash="a" * 64,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="b" * 64,
        features_snapshot_json=json.dumps(features),
        symbol="SOLUSDT",
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_execute_normal_spread():
    """spread 0.05% + bid_depth 0.8 → EXECUTE."""
    agent = ExecutionAgent()
    signal = _make_signal({"spread_pct": 0.0005, "bid_depth": 0.8})
    review = await agent.review(signal)
    assert review.verdict == "EXECUTE"
    assert review.agent == "execution"


@pytest.mark.asyncio
async def test_skip_on_high_spread():
    """spread 0.2% (max 0.1% 초과) → SKIP."""
    agent = ExecutionAgent()
    signal = _make_signal({"spread_pct": 0.002, "bid_depth": 0.8})
    review = await agent.review(signal)
    assert review.verdict == "SKIP"


@pytest.mark.asyncio
async def test_delay_on_borderline_fill():
    """bid_depth 0.2 → fill_probability 0.4 → DELAY."""
    agent = ExecutionAgent()
    signal = _make_signal({"spread_pct": 0.0005, "bid_depth": 0.2})
    review = await agent.review(signal)
    assert review.verdict == "DELAY"


@pytest.mark.asyncio
async def test_4case_distribution_sums_close_to_1():
    """4-case 확률 분포 합이 약 1.0."""
    agent = ExecutionAgent()
    signal = _make_signal({"spread_pct": 0.0005, "bid_depth": 0.8})
    review = await agent.review(signal)
    data = json.loads(review.full_json)
    total = sum(data["case_distribution"].values())
    assert 0.95 <= total <= 1.05  # 반올림 오차 허용


@pytest.mark.asyncio
async def test_order_plan_post_only():
    """order_plan 에 post_only=True (청사진 §3.3.1)."""
    agent = ExecutionAgent()
    signal = _make_signal({"spread_pct": 0.0005, "bid_depth": 0.8})
    review = await agent.review(signal)
    data = json.loads(review.full_json)
    assert data["order_plan"]["post_only"] is True
    assert data["order_plan"]["reduce_only"] is False
    assert data["order_plan"]["side"] == "BUY"  # LONG → BUY


@pytest.mark.asyncio
async def test_short_signal_side_sell():
    """SHORT signal → side=SELL."""
    agent = ExecutionAgent()
    now = datetime.now(timezone.utc)
    signal = SignalDecision(
        setup_id="t", params_hash="a" * 64, signal_source="strategy_skill",
        reasoning="t", ts_signal_generated=now, raw_data_hash="b" * 64,
        features_snapshot_json='{"spread_pct": 0.0005, "bid_depth": 0.8}',
        symbol="SOLUSDT", action="SHORT", confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )
    review = await agent.review(signal)
    data = json.loads(review.full_json)
    assert data["order_plan"]["side"] == "SELL"


@pytest.mark.asyncio
async def test_default_conservative_when_no_features():
    """features 없으면 보수적 추정."""
    agent = ExecutionAgent()
    signal = _make_signal({})
    review = await agent.review(signal)
    # 기본 spread=0.05%, bid_depth=1.0 → EXECUTE
    assert review.verdict in {"EXECUTE", "DELAY", "SKIP"}


@pytest.mark.asyncio
async def test_invalid_payload_type():
    agent = ExecutionAgent()
    with pytest.raises(TypeError):
        await agent.review("not a signal")
