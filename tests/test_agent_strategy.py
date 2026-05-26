"""
tests/test_agent_strategy.py
=====================================================================
StrategyAgent 검증 — Setup Registry 메트릭 + 운영자 7기준.
=====================================================================
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.strategy_agent import StrategyAgent
from audit.signal_decision import SignalDecision
from db.init_db import init_db
from registry.setup_registry import SetupMetrics, SetupRegistry, SetupStatus
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "agent_strategy.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def registry(tmp_db):
    r = SetupRegistry(db_path=tmp_db)
    r.register(DailyTSMOMDonchianSkill)
    return r


@pytest.fixture
def agent(registry):
    return StrategyAgent(registry=registry)


def _make_signal() -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        params_hash=DailyTSMOMDonchianSkill.PARAMS_HASH,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="a" * 64,
        features_snapshot_json="{}",
        symbol="SOLUSDT",
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_pass_all_criteria(agent, registry):
    """7기준 모두 통과 + 학계 근거 명확 → PASS."""
    registry.update_metrics(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        metrics=SetupMetrics(
            n=250, pf=1.35, expectancy_r=0.15, avg_win_loss_ratio=1.8,
            mdd_pct=20.0, single_symbol_max_pct=18.0, top3_excluded_pf=1.15,
        ),
        evaluation_type="backtest", passed=True,
    )
    review = await agent.review(_make_signal())
    assert review.verdict == "PASS"
    assert review.agent == "strategy_quant"


@pytest.mark.asyncio
async def test_fail_top3_excluded_pf(agent, registry):
    """top3_excluded_pf 0.95 + single_symbol 27% → 2 fail → FAIL."""
    registry.update_metrics(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        metrics=SetupMetrics(
            n=250, pf=2.20, expectancy_r=0.30, avg_win_loss_ratio=2.5,
            mdd_pct=18.0, single_symbol_max_pct=27.0,  # >= 25 → fail
            top3_excluded_pf=0.95,                       # < 1.0 → fail
        ),
        evaluation_type="backtest", passed=False,
    )
    review = await agent.review(_make_signal())
    # 2 fail → FAIL (1 fail 이면 CONDITIONAL)
    assert review.verdict == "FAIL"
    full = json.loads(review.full_json)
    assert any("top3_excluded_pf" in c for c in full["concerns"])
    assert any("single_symbol_max" in c for c in full["concerns"])


@pytest.mark.asyncio
async def test_conditional_one_fail(agent, registry):
    """6/7 통과, 1 borderline → CONDITIONAL."""
    registry.update_metrics(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        metrics=SetupMetrics(
            n=250, pf=1.35, expectancy_r=0.15, avg_win_loss_ratio=1.8,
            mdd_pct=20.0, single_symbol_max_pct=18.0,
            top3_excluded_pf=0.99,  # 1.0 borderline
        ),
        evaluation_type="backtest", passed=False,
    )
    review = await agent.review(_make_signal())
    assert review.verdict == "CONDITIONAL"


@pytest.mark.asyncio
async def test_fail_unregistered_setup(agent):
    """미등록 setup_id → FAIL."""
    now = datetime.now(timezone.utc)
    bad = SignalDecision(
        setup_id="nonexistent_v1",
        params_hash="x" * 64,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="a" * 64,
        features_snapshot_json="{}",
        symbol="SOLUSDT",
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )
    review = await agent.review(bad)
    assert review.verdict == "FAIL"
    assert "not registered" in review.full_json


@pytest.mark.asyncio
async def test_invalid_payload_type(agent):
    """SignalDecision 아닌 입력 거부."""
    with pytest.raises(TypeError, match="SignalDecision"):
        await agent.review("not a signal")


@pytest.mark.asyncio
async def test_no_registry_rejected():
    """registry=None 거부."""
    with pytest.raises(ValueError, match="registry"):
        StrategyAgent(registry=None)


@pytest.mark.asyncio
async def test_no_metrics_yet_returns_fail(agent, registry):
    """update_metrics 미호출 → 모든 메트릭 0 → FAIL."""
    review = await agent.review(_make_signal())
    assert review.verdict == "FAIL"  # n=0 < 200, pf=0 < 1.25
