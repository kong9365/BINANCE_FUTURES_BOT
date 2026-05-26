"""
tests/test_audit_signal_decision.py
=====================================================================
SignalDecision dataclass 검증.

근거: audit/signal_decision.py + SYSTEM_DESIGN_BLUEPRINT.md §6.1
=====================================================================
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from audit.signal_decision import SignalDecision


def _make_default(**overrides) -> SignalDecision:
    """SignalDecision 기본 생성 헬퍼."""
    now = datetime.now(timezone.utc)
    defaults = {
        "setup_id": "1d_tsmom_donchian_long_v1",
        "params_hash": "a" * 64,
        "signal_source": "test",
        "reasoning": "Donchian 20일 돌파 + ADX 28 + EMA200 위",
        "ts_signal_generated": now,
        "raw_data_hash": "b" * 64,
        "features_snapshot_json": '{"donchian_breakout": true}',
        "symbol": "SOLUSDT",
        "action": "LONG",
        "confidence": 0.78,
        "expires_at": now + timedelta(hours=24),
    }
    defaults.update(overrides)
    return SignalDecision(**defaults)


def test_default_creation():
    """기본 필드 + 자동 UUID + ALCOA+ 필드 채워짐."""
    d = _make_default()
    assert d.signal_id  # UUID 자동 생성
    assert len(d.signal_id) == 36  # UUID4 format
    assert d.status == "GENERATED"
    assert d.immutable is True
    assert d.ts_db_recorded.tzinfo is not None  # tz-aware


def test_confidence_range_validation():
    """confidence 0.0~1.0 검증."""
    with pytest.raises(ValueError, match="confidence"):
        _make_default(confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        _make_default(confidence=-0.1)


def test_tz_naive_rejected():
    """tz-naive datetime 거부 (CLAUDE.md TIER 2)."""
    naive = datetime.now()  # tz 없음
    with pytest.raises(ValueError, match="tz-aware"):
        _make_default(ts_signal_generated=naive)


def test_to_db_row_complete():
    """to_db_row() — signal_decisions 테이블 컬럼 매핑 완전."""
    d = _make_default()
    row = d.to_db_row()
    # 모든 필수 컬럼 존재
    required = {
        "signal_id", "setup_id", "params_hash", "claude_session_id",
        "signal_source", "reasoning", "ts_signal_generated", "ts_db_recorded",
        "raw_data_hash", "features_snapshot_json", "symbol", "action",
        "confidence", "cost_estimate_json", "ev_estimated", "rank_in_universe",
        "expires_at", "status",
    }
    assert required <= set(row.keys()), f"누락: {required - set(row.keys())}"


def test_to_audit_payload_legible():
    """to_audit_payload() — ALCOA+ Legible (JSON 직렬화 가능)."""
    d = _make_default()
    payload = d.to_audit_payload()
    json_str = json.dumps(payload, sort_keys=True)
    assert "1d_tsmom_donchian_long_v1" in json_str
    assert "SOLUSDT" in json_str


def test_consistent_params_hash():
    """ALCOA+ Consistent — 동일 params_hash 두 시그널 → 동일 setup_id."""
    d1 = _make_default(params_hash="hash_A")
    d2 = _make_default(params_hash="hash_A")
    assert d1.params_hash == d2.params_hash
    # signal_id 는 *반드시 다름* (UUID4 자동)
    assert d1.signal_id != d2.signal_id


def test_to_audit_json_deterministic():
    """to_audit_json() — 동일 입력 → 동일 출력 (sort_keys)."""
    d = _make_default(signal_id="fixed-id")
    j1 = d.to_audit_json()
    # 동일 인스턴스 다시 호출
    j2 = d.to_audit_json()
    assert j1 == j2
