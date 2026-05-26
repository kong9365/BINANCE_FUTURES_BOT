"""
tests/test_kill_switch_auto.py
=====================================================================
KillSwitch 자동 활성화 6조건 검증 (governance/auto_trigger.py).
=====================================================================
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from governance.auto_trigger import (
    TriggerResult,
    check_daily_loss_hard,
    check_ops_critical,
    check_data_stale,
    check_ssd_disconnect,
    check_mcp_disconnect,
    check_api_key_changed,
)


# 조건 1: daily_loss_hard
def test_daily_loss_hard_triggered():
    """daily loss -1.5% → activate=True."""
    result = check_daily_loss_hard(current_wallet=985, daily_start=1000)
    assert result.activate is True
    assert result.source == "daily_loss_hard"
    assert "Daily loss" in result.reason


def test_daily_loss_hard_not_triggered():
    """daily loss -1.4% (warn only) → activate=False."""
    result = check_daily_loss_hard(current_wallet=986, daily_start=1000)
    assert result.activate is False


# 조건 2: ops_critical
def test_ops_critical_triggered():
    result = check_ops_critical(ops_review_verdict="CRITICAL")
    assert result.activate is True
    assert result.source == "ops_critical"


def test_ops_critical_not_triggered():
    for v in ("HEALTHY", "DEGRADED", None):
        result = check_ops_critical(ops_review_verdict=v)
        assert result.activate is False


# 조건 3: mcp_disconnect
def test_mcp_disconnect_triggered():
    result = check_mcp_disconnect(last_seen_seconds_ago=600)  # 10분
    assert result.activate is True
    assert result.source == "mcp_disconnect"


def test_mcp_disconnect_not_triggered():
    result = check_mcp_disconnect(last_seen_seconds_ago=100)  # 100초
    assert result.activate is False


# 조건 4: data_stale
def test_data_stale_triggered():
    result = check_data_stale(staleness_minutes=5.0, max_minutes=3.0)
    assert result.activate is True
    assert result.source == "data_stale"


def test_data_stale_not_triggered():
    result = check_data_stale(staleness_minutes=2.0, max_minutes=3.0)
    assert result.activate is False


# 조건 5: api_key_changed
def test_api_key_changed_triggered():
    result = check_api_key_changed(
        current_permissions={"trade": True, "withdraw": True},  # 변경됨
        expected={"trade": True, "withdraw": False},
    )
    assert result.activate is True
    assert result.source == "api_key_changed"


def test_api_key_changed_not_triggered():
    result = check_api_key_changed(
        current_permissions={"trade": True, "withdraw": False},
        expected={"trade": True, "withdraw": False},
    )
    assert result.activate is False


def test_api_key_unknown_permissions_skipped():
    """current_permissions=None → 평가 X (activate=False)."""
    result = check_api_key_changed(
        current_permissions=None,
        expected={"trade": True},
    )
    assert result.activate is False


# 조건 6: ssd_disconnect
def test_ssd_disconnect_triggered():
    """존재하지 않는 경로 → activate."""
    result = check_ssd_disconnect(bot_data_dir="/nonexistent_path_xyz_12345")
    assert result.activate is True
    assert result.source == "ssd_disconnect"


def test_ssd_disconnect_not_triggered():
    """존재하는 경로 → activate=False."""
    with tempfile.TemporaryDirectory() as tmp:
        result = check_ssd_disconnect(bot_data_dir=tmp)
        assert result.activate is False
