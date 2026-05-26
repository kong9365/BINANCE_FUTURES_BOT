"""
tests/test_agent_orchestrator.py
=====================================================================
AgentOrchestrator 검증 — 5-Agent 병렬+순차 + audit_log chain.
=====================================================================
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.data_agent import DataAgent
from agents.execution_agent import ExecutionAgent
from agents.ops_agent import OpsAgent
from agents.orchestrator import AgentOrchestrator, OrchestratorResult
from agents.risk_agent import RiskAgent
from agents.strategy_agent import StrategyAgent
from audit.agent_review import AgentReview
from audit.audit_log import EventType
from audit.audit_logger import AuditLogger
from audit.signal_decision import SignalDecision
from data.capital_manager import CapitalSnapshot
from db.init_db import init_db
from governance.kill_switch import KillSwitch
from registry.setup_registry import SetupMetrics, SetupRegistry
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "orchestrator.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def tmp_ks_path(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        ks_path = Path(tmp) / "KILLSWITCH"
        monkeypatch.setenv("KILLSWITCH_FILE", str(ks_path))
        yield ks_path


def _make_signal() -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        params_hash=DailyTSMOMDonchianSkill.PARAMS_HASH,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="b" * 64,
        features_snapshot_json='{"staleness_minutes": 1, "missing_ratio_pct": 0, '
                               '"listing_age_days": 365, "spread_pct": 0.0005, '
                               '"bid_depth": 0.8}',
        symbol="SOLUSDT",
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


def _make_passing_setup(tmp_db) -> SetupRegistry:
    """7기준 모두 통과하는 setup."""
    r = SetupRegistry(db_path=tmp_db)
    r.register(DailyTSMOMDonchianSkill)
    r.update_metrics(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        metrics=SetupMetrics(
            n=250, pf=1.35, expectancy_r=0.15, avg_win_loss_ratio=1.8,
            mdd_pct=20.0, single_symbol_max_pct=18.0, top3_excluded_pf=1.15,
        ),
        evaluation_type="backtest", passed=True,
    )
    return r


def _make_mock_risk_manager_approve():
    import time
    rm = MagicMock()
    rm.check_all = AsyncMock(return_value=True)
    snapshot = CapitalSnapshot(
        wallet_balance=995.0, margin_balance=995.0, available_balance=950.0,
        locked_margin=45.0, unrealized_pnl=0.0, timestamp=time.time(),
    )
    cm = MagicMock()
    cm.get_snapshot = AsyncMock(return_value=snapshot)
    cm.get_initial_capital = MagicMock(return_value=1000.0)
    cm.get_daily_start_capital = MagicMock(return_value=1000.0)
    rm.capital_manager = cm
    return rm


def _make_healthy_monitor():
    m = MagicMock()
    result = MagicMock()
    result.healthy = True
    result.critical = False
    result.issues = []
    m.check = MagicMock(return_value=result)
    return m


@pytest.mark.asyncio
async def test_all_pass_approved(tmp_db, tmp_ks_path):
    """5-Agent 모두 PASS → approved=True."""
    registry = _make_passing_setup(tmp_db)
    orchestrator = AgentOrchestrator(
        strategy_agent=StrategyAgent(registry),
        risk_agent=RiskAgent(_make_mock_risk_manager_approve()),
        execution_agent=ExecutionAgent(),
        data_agent=DataAgent(protected_symbols=[]),
        ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
        audit_logger=AuditLogger(tmp_db),
    )
    result = await orchestrator.review_signal(_make_signal())
    assert result.approved is True
    assert result.final_verdict == "APPROVED"
    assert len(result.reviews) == 5  # Strategy + Data + Ops + Risk + Execution


@pytest.mark.asyncio
async def test_data_invalid_blocks(tmp_db, tmp_ks_path):
    """DataAgent INVALID → 차단 (Risk/Execution 호출 안 함)."""
    registry = _make_passing_setup(tmp_db)
    orchestrator = AgentOrchestrator(
        strategy_agent=StrategyAgent(registry),
        risk_agent=RiskAgent(_make_mock_risk_manager_approve()),
        execution_agent=ExecutionAgent(),
        data_agent=DataAgent(protected_symbols=["SOLUSDT"]),  # 의도적 차단
        ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
        audit_logger=AuditLogger(tmp_db),
    )
    result = await orchestrator.review_signal(_make_signal())
    assert result.approved is False
    assert result.blocked_at_agent == "data_backtest"
    # Risk/Execution 호출 안 함 → reviews 에 3개만 (Strategy/Data/Ops)
    assert len(result.reviews) == 3


@pytest.mark.asyncio
async def test_ops_critical_blocks(tmp_db, tmp_ks_path):
    """OpsAgent CRITICAL (KillSwitch 활성) → 차단."""
    KillSwitch.activate(reason="test", source="manual")
    try:
        registry = _make_passing_setup(tmp_db)
        orchestrator = AgentOrchestrator(
            strategy_agent=StrategyAgent(registry),
            risk_agent=RiskAgent(_make_mock_risk_manager_approve()),
            execution_agent=ExecutionAgent(),
            data_agent=DataAgent(protected_symbols=[]),
            ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
            audit_logger=AuditLogger(tmp_db),
        )
        result = await orchestrator.review_signal(_make_signal())
        assert result.approved is False
        assert result.blocked_at_agent == "ops_observability"
    finally:
        KillSwitch.deactivate(by="operator")


@pytest.mark.asyncio
async def test_risk_reject_blocks(tmp_db, tmp_ks_path):
    """RiskAgent REJECT → execution 호출 안 함."""
    registry = _make_passing_setup(tmp_db)
    rm = MagicMock()
    rm.check_all = AsyncMock(return_value=False)  # REJECT
    cm = MagicMock()
    import time
    snapshot = CapitalSnapshot(
        wallet_balance=995.0, margin_balance=995.0, available_balance=950.0,
        locked_margin=45.0, unrealized_pnl=0.0, timestamp=time.time(),
    )
    cm.get_snapshot = AsyncMock(return_value=snapshot)
    cm.get_initial_capital = MagicMock(return_value=1000.0)
    cm.get_daily_start_capital = MagicMock(return_value=1000.0)
    rm.capital_manager = cm

    orchestrator = AgentOrchestrator(
        strategy_agent=StrategyAgent(registry),
        risk_agent=RiskAgent(rm),
        execution_agent=ExecutionAgent(),
        data_agent=DataAgent(protected_symbols=[]),
        ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
        audit_logger=AuditLogger(tmp_db),
    )
    result = await orchestrator.review_signal(_make_signal())
    assert result.approved is False
    assert result.blocked_at_agent == "risk"


@pytest.mark.asyncio
async def test_audit_log_chain_complete(tmp_db, tmp_ks_path):
    """5-Agent 통과 시 audit_log 에 5개 row + chain 무결성."""
    registry = _make_passing_setup(tmp_db)
    orchestrator = AgentOrchestrator(
        strategy_agent=StrategyAgent(registry),
        risk_agent=RiskAgent(_make_mock_risk_manager_approve()),
        execution_agent=ExecutionAgent(),
        data_agent=DataAgent(protected_symbols=[]),
        ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
        audit_logger=AuditLogger(tmp_db),
    )
    await orchestrator.review_signal(_make_signal())

    # audit_log 에 AGENT_REVIEW 이벤트 5개 확인
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT event_type, payload_hash, previous_log_hash FROM audit_log "
            "WHERE event_type='AGENT_REVIEW' ORDER BY ts ASC, log_id ASC"
        ).fetchall()
        assert len(rows) == 5
        # chain 무결성 (직전 row 의 payload_hash = 현재 previous_log_hash)
        for i in range(1, len(rows)):
            assert rows[i][2] == rows[i - 1][1]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_invalid_payload_type(tmp_db, tmp_ks_path):
    """SignalDecision 아닌 입력 거부."""
    registry = _make_passing_setup(tmp_db)
    orchestrator = AgentOrchestrator(
        strategy_agent=StrategyAgent(registry),
        risk_agent=RiskAgent(_make_mock_risk_manager_approve()),
        execution_agent=ExecutionAgent(),
        data_agent=DataAgent(protected_symbols=[]),
        ops_agent=OpsAgent(_make_healthy_monitor(), db_path=tmp_db),
    )
    with pytest.raises(TypeError):
        await orchestrator.review_signal("not a signal")
