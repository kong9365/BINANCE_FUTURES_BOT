"""
tests/test_agent_ops.py
=====================================================================
OpsAgent 검증 — SystemHealthMonitor wrap + KillSwitch 응답성 + outbox.
=====================================================================
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.ops_agent import OpsAgent
from audit.signal_decision import SignalDecision
from db.init_db import init_db
from governance.kill_switch import KillSwitch


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ops_test.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def tmp_ks_path(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        ks_path = Path(tmp) / "KILLSWITCH"
        monkeypatch.setenv("KILLSWITCH_FILE", str(ks_path))
        yield ks_path


@pytest.fixture
def healthy_monitor():
    """모든 헬스 OK."""
    m = MagicMock()
    result = MagicMock()
    result.healthy = True
    result.critical = False
    result.issues = []
    m.check = MagicMock(return_value=result)
    return m


@pytest.fixture
def unhealthy_monitor():
    """일부 이슈, critical X."""
    m = MagicMock()
    result = MagicMock()
    result.healthy = False
    result.critical = False
    result.issues = ["WS kline stale 1분"]
    m.check = MagicMock(return_value=result)
    return m


@pytest.fixture
def critical_monitor():
    """CRITICAL."""
    m = MagicMock()
    result = MagicMock()
    result.healthy = False
    result.critical = True
    result.issues = ["DB connection lost"]
    m.check = MagicMock(return_value=result)
    return m


def _make_signal() -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id="1d_tsmom_donchian_long_v1",
        params_hash="a" * 64,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="b" * 64,
        features_snapshot_json="{}",
        symbol="SOLUSDT",
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_healthy_normal_state(healthy_monitor, tmp_db, tmp_ks_path):
    """모든 OK → HEALTHY."""
    agent = OpsAgent(system_health_monitor=healthy_monitor, db_path=tmp_db)
    review = await agent.review(_make_signal())
    assert review.verdict == "HEALTHY"
    assert review.agent == "ops_observability"


@pytest.mark.asyncio
async def test_degraded_on_issues(unhealthy_monitor, tmp_db, tmp_ks_path):
    """health.healthy=False, critical=False → DEGRADED."""
    agent = OpsAgent(system_health_monitor=unhealthy_monitor, db_path=tmp_db)
    review = await agent.review(_make_signal())
    assert review.verdict == "DEGRADED"


@pytest.mark.asyncio
async def test_critical_on_critical_health(critical_monitor, tmp_db, tmp_ks_path):
    """health.critical=True → CRITICAL."""
    agent = OpsAgent(system_health_monitor=critical_monitor, db_path=tmp_db)
    review = await agent.review(_make_signal())
    assert review.verdict == "CRITICAL"


@pytest.mark.asyncio
async def test_critical_on_kill_switch_active(healthy_monitor, tmp_db, tmp_ks_path):
    """KillSwitch 활성 → CRITICAL."""
    KillSwitch.activate(reason="test", source="manual")
    try:
        agent = OpsAgent(system_health_monitor=healthy_monitor, db_path=tmp_db)
        review = await agent.review(_make_signal())
        assert review.verdict == "CRITICAL"
        data = json.loads(review.full_json)
        assert data["alerting"]["kill_switch_active"] is True
    finally:
        KillSwitch.deactivate(by="operator")


@pytest.mark.asyncio
async def test_signal_id_propagated(healthy_monitor, tmp_db, tmp_ks_path):
    """payload 의 signal_id 가 review.signal_id 에 그대로."""
    agent = OpsAgent(system_health_monitor=healthy_monitor, db_path=tmp_db)
    signal = _make_signal()
    review = await agent.review(signal)
    assert review.signal_id == signal.signal_id


@pytest.mark.asyncio
async def test_no_monitor_still_works(tmp_db, tmp_ks_path):
    """system_health_monitor=None 이어도 KillSwitch + DB 만 평가."""
    agent = OpsAgent(system_health_monitor=None, db_path=tmp_db)
    review = await agent.review(_make_signal())
    assert review.verdict == "HEALTHY"


@pytest.mark.asyncio
async def test_no_payload_uses_system_health(healthy_monitor, tmp_db, tmp_ks_path):
    """payload=None 도 OK (system 전체 헬스 평가)."""
    agent = OpsAgent(system_health_monitor=healthy_monitor, db_path=tmp_db)
    review = await agent.review(None)
    assert review.verdict == "HEALTHY"
    assert review.signal_id == "system-health"
