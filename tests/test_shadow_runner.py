"""tests/test_shadow_runner.py — ShadowAgentRunner (M7 main_7590 통합 캡슐화)."""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.orchestrator import OrchestratorResult
from audit.audit_logger import AuditLogger
from audit.signal_decision import SignalDecision
from db.init_db import init_db
from governance.shadow_config import (
    ShadowAgentConfig, resolve_shadow_enabled as _resolve_shadow_enabled,
)
from governance.shadow_runner import ShadowAgentRunner


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "shadow.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def mock_orchestrator():
    """리뷰가 APPROVED 인 orchestrator mock."""
    orch = MagicMock()
    orch.review_signal = AsyncMock(return_value=OrchestratorResult(
        approved=True, final_verdict="APPROVED", reviews=[],
    ))
    return orch


@dataclass
class FakeCandidate:
    symbol: str = "SOLUSDT"
    action: str = "LONG"
    confidence: float = 0.7


def test_resolve_shadow_enabled_default_false(monkeypatch):
    """env 미설정 → cfg.enabled 사용."""
    monkeypatch.delenv("ENABLE_SHADOW_AGENTS", raising=False)
    assert _resolve_shadow_enabled(False) is False
    assert _resolve_shadow_enabled(True) is True


def test_resolve_shadow_enabled_env_true(monkeypatch):
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    assert _resolve_shadow_enabled(False) is True


def test_resolve_shadow_enabled_env_false(monkeypatch):
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "false")
    assert _resolve_shadow_enabled(True) is False


@pytest.mark.asyncio
async def test_disabled_noop(mock_orchestrator, monkeypatch):
    """env OFF → run_shadow() 즉시 None (orchestrator 호출 X)."""
    monkeypatch.delenv("ENABLE_SHADOW_AGENTS", raising=False)
    runner = ShadowAgentRunner(
        orchestrator=mock_orchestrator, audit_logger=None,
        cfg=ShadowAgentConfig(enabled=False),
    )
    assert runner.enabled is False
    result = await runner.run_shadow(FakeCandidate())
    assert result is None
    mock_orchestrator.review_signal.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_runs_orchestrator(mock_orchestrator, monkeypatch):
    """env ON → orchestrator review 호출."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    runner = ShadowAgentRunner(
        orchestrator=mock_orchestrator, audit_logger=None,
    )
    result = await runner.run_shadow(FakeCandidate())
    assert result is not None
    assert result.approved is True
    mock_orchestrator.review_signal.assert_called_once()


@pytest.mark.asyncio
async def test_candidate_to_signal_decision():
    """candidate object → SignalDecision 변환."""
    candidate = FakeCandidate(symbol="AVAXUSDT", action="SHORT", confidence=0.85)
    decision = ShadowAgentRunner._candidate_to_signal_decision(
        candidate, setup_id="test_setup", params_hash="abc",
    )
    assert isinstance(decision, SignalDecision)
    assert decision.symbol == "AVAXUSDT"
    assert decision.action == "SHORT"
    assert decision.confidence == 0.85
    assert decision.setup_id == "test_setup"


@pytest.mark.asyncio
async def test_candidate_dict_compatible():
    """candidate 가 dict 인 경우도 호환."""
    candidate = {"symbol": "LINKUSDT", "action": "LONG", "confidence": 0.65}
    decision = ShadowAgentRunner._candidate_to_signal_decision(
        candidate, setup_id="t", params_hash="x",
    )
    assert decision.symbol == "LINKUSDT"
    assert decision.confidence == 0.65


@pytest.mark.asyncio
async def test_orchestrator_failure_safe(mock_orchestrator, monkeypatch):
    """orchestrator review 실패 시 fail-safe (None 반환, 예외 X)."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    mock_orchestrator.review_signal = AsyncMock(side_effect=Exception("boom"))
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator)
    # 예외 전파 안 함
    result = await runner.run_shadow(FakeCandidate())
    assert result is None


@pytest.mark.asyncio
async def test_audit_log_integration(mock_orchestrator, monkeypatch, tmp_db):
    """audit_logger 주입 시 SIGNAL_GENERATED + SIGNAL_REVIEWED 이벤트 기록."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    audit_logger = AuditLogger(db_path=tmp_db)
    runner = ShadowAgentRunner(
        orchestrator=mock_orchestrator, audit_logger=audit_logger,
    )
    await runner.run_shadow(FakeCandidate())

    # audit_log 에 2개 이벤트 확인
    conn = sqlite3.connect(tmp_db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE event_type IN ('SIGNAL_GENERATED', 'SIGNAL_REVIEWED')"
        ).fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_shadow_agent_config_default():
    """ShadowAgentConfig 기본값 — enabled=False (안전)."""
    cfg = ShadowAgentConfig()
    assert cfg.enabled is False
    assert cfg.log_to_audit is True
    assert cfg.timeout_seconds == 5.0


def test_config_exported_from_settings():
    """config/settings.py 에서 re-export."""
    from config.settings import SHADOW_AGENT_CONFIG, ShadowAgentConfig
    assert isinstance(SHADOW_AGENT_CONFIG, ShadowAgentConfig)


# ─────────────────────────────────────────────────────
# M15 — run_shadow_for_decision + setup_id 라벨 정합
# ─────────────────────────────────────────────────────
def _make_decision(setup_id="1d_tsmom_donchian_long_v1", symbol="ETHUSDT", action="LONG"):
    """skill 이 생산하는 형태의 SignalDecision."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return SignalDecision(
        signal_id="sig-m15-test",
        setup_id=setup_id,
        params_hash="ffbacd920f61",
        signal_source="strategy_skill",
        reasoning="1d LONG breakout test",
        ts_signal_generated=now,
        raw_data_hash="rawhash123",
        features_snapshot_json='{"action": "LONG", "adx": 30.0}',
        symbol=symbol,
        action=action,
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_run_shadow_default_setup_id_not_hardcoded_1d(mock_orchestrator, monkeypatch):
    """M15: run_shadow 기본 setup_id 가 더 이상 1d_tsmom 하드코딩이 아님."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    captured = {}

    async def _capture(decision):
        captured["setup_id"] = decision.setup_id
        return OrchestratorResult(approved=True, final_verdict="APPROVED", reviews=[])

    mock_orchestrator.review_signal = AsyncMock(side_effect=_capture)
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator)
    # setup_id 미지정 → 기본 "shadow_unknown" (1d_tsmom 아님)
    await runner.run_shadow(FakeCandidate())
    assert captured["setup_id"] == "shadow_unknown"
    assert captured["setup_id"] != "1d_tsmom_donchian_long_v1"


@pytest.mark.asyncio
async def test_run_shadow_uses_passed_setup_tag(mock_orchestrator, monkeypatch):
    """M15: run_shadow 가 전달된 setup_tag 로 라벨 (oi_surge/breakout 정합)."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    captured = {}

    async def _capture(decision):
        captured["setup_id"] = decision.setup_id
        return OrchestratorResult(approved=True, final_verdict="APPROVED", reviews=[])

    mock_orchestrator.review_signal = AsyncMock(side_effect=_capture)
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator)
    await runner.run_shadow(FakeCandidate(), setup_id="oi_surge_long")
    assert captured["setup_id"] == "oi_surge_long"


@pytest.mark.asyncio
async def test_run_shadow_for_decision_preserves_setup_id(mock_orchestrator, monkeypatch):
    """M15: run_shadow_for_decision 은 decision 의 실제 setup_id 보존 (변환 X)."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator)
    decision = _make_decision(setup_id="1d_tsmom_donchian_long_v1")
    result = await runner.run_shadow_for_decision(decision)
    assert result is not None
    # orchestrator 가 *바로 그 decision* 으로 호출됨 (재생성 X)
    mock_orchestrator.review_signal.assert_called_once_with(decision)


@pytest.mark.asyncio
async def test_run_shadow_for_decision_disabled_noop(mock_orchestrator, monkeypatch):
    """env OFF → run_shadow_for_decision 즉시 None."""
    monkeypatch.delenv("ENABLE_SHADOW_AGENTS", raising=False)
    runner = ShadowAgentRunner(
        orchestrator=mock_orchestrator, cfg=ShadowAgentConfig(enabled=False),
    )
    result = await runner.run_shadow_for_decision(_make_decision())
    assert result is None
    mock_orchestrator.review_signal.assert_not_called()


@pytest.mark.asyncio
async def test_run_shadow_for_decision_none_safe(mock_orchestrator, monkeypatch):
    """decision=None → fail-safe None (예외 X)."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator)
    result = await runner.run_shadow_for_decision(None)
    assert result is None
    mock_orchestrator.review_signal.assert_not_called()


@pytest.mark.asyncio
async def test_run_shadow_for_decision_audit_real_setup_id(
    mock_orchestrator, monkeypatch, tmp_db
):
    """M15: audit_log SIGNAL_GENERATED 가 실제 setup_id 기록 (Disconnect 3 해소)."""
    monkeypatch.setenv("ENABLE_SHADOW_AGENTS", "true")
    audit_logger = AuditLogger(db_path=tmp_db)
    runner = ShadowAgentRunner(orchestrator=mock_orchestrator, audit_logger=audit_logger)
    await runner.run_shadow_for_decision(
        _make_decision(setup_id="1d_tsmom_donchian_long_v1")
    )
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT related_setup_id FROM audit_log "
            "WHERE event_type = 'SIGNAL_GENERATED'"
        ).fetchone()
        assert row is not None
        assert row[0] == "1d_tsmom_donchian_long_v1"
    finally:
        conn.close()
