"""
tests/test_kill_switch.py
=====================================================================
KillSwitch 어댑터 검증 — file OR btc_is_halted.

근거:
  - governance/kill_switch.py + SYSTEM_DESIGN_BLUEPRINT.md §3.4.3
  - 운영자 결정 #v2-4: 청사진 + btc_risk_off 통합 (OR 결합)
=====================================================================
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from governance.kill_switch import KillSwitch


@pytest.fixture
def tmp_killswitch_path(monkeypatch):
    """KILLSWITCH 파일 경로를 tempfile 로 격리."""
    with tempfile.TemporaryDirectory() as tmp:
        ks_path = Path(tmp) / "KILLSWITCH"
        monkeypatch.setenv("KILLSWITCH_FILE", str(ks_path))
        yield ks_path


def test_default_path_via_env(tmp_killswitch_path):
    """KILLSWITCH_FILE env 적용 확인."""
    assert KillSwitch.path() == tmp_killswitch_path


def test_is_active_false_when_no_file(tmp_killswitch_path):
    """파일 없으면 비활성."""
    assert KillSwitch.is_active() is False


def test_activate_creates_file(tmp_killswitch_path):
    """activate() → 파일 생성 + 상태 dict."""
    KillSwitch.activate(reason="test halt", source="manual")
    assert tmp_killswitch_path.exists()
    status = KillSwitch.get_status()
    assert status["reason"] == "test halt"
    assert status["source"] == "manual"
    assert "activated_at" in status


def test_is_active_true_after_activate(tmp_killswitch_path):
    """activate() → is_active() True."""
    KillSwitch.activate(reason="test", source="manual")
    assert KillSwitch.is_active() is True


def test_deactivate_removes_file(tmp_killswitch_path):
    """deactivate(by='operator') → 파일 삭제."""
    KillSwitch.activate(reason="test", source="manual")
    assert tmp_killswitch_path.exists()
    KillSwitch.deactivate(by="operator")
    assert not tmp_killswitch_path.exists()
    assert KillSwitch.is_active() is False


def test_deactivate_non_operator_rejected(tmp_killswitch_path):
    """청사진 §3.4.3: 운영자만 수동 해제."""
    KillSwitch.activate(reason="test", source="manual")
    with pytest.raises(PermissionError, match="operator"):
        KillSwitch.deactivate(by="agent")
    # 파일은 여전히 존재
    assert tmp_killswitch_path.exists()


def test_activate_empty_reason_rejected(tmp_killswitch_path):
    with pytest.raises(ValueError, match="reason"):
        KillSwitch.activate(reason="", source="manual")


def test_activate_empty_source_rejected(tmp_killswitch_path):
    with pytest.raises(ValueError, match="source"):
        KillSwitch.activate(reason="halted", source="")


def test_deactivate_when_already_inactive(tmp_killswitch_path):
    """이미 비활성 상태에서 deactivate — 경고만 (예외 X)."""
    assert not tmp_killswitch_path.exists()
    KillSwitch.deactivate(by="operator")  # No error


def test_btc_risk_off_adapter_true(tmp_killswitch_path):
    """btc_risk_off halted 상태 → is_active() True (어댑터 통합)."""
    from strategy.btc_risk_off import BTCRiskOffState

    now = datetime.now(timezone.utc)
    state = BTCRiskOffState(
        halted_until=now + timedelta(hours=3),
        last_trigger_drop_pct=-0.015,
        last_trigger_at=now - timedelta(hours=1),
    )
    # 파일 없지만 btc_state 활성
    assert not tmp_killswitch_path.exists()
    assert KillSwitch.is_active(btc_state=state, now=now) is True


def test_btc_risk_off_adapter_false(tmp_killswitch_path):
    """btc_risk_off 만료 → is_active() False."""
    from strategy.btc_risk_off import BTCRiskOffState

    now = datetime.now(timezone.utc)
    state = BTCRiskOffState(
        halted_until=now - timedelta(hours=1),  # 만료
        last_trigger_drop_pct=-0.015,
        last_trigger_at=now - timedelta(hours=8),
    )
    assert KillSwitch.is_active(btc_state=state, now=now) is False


def test_file_overrides_btc_risk_off(tmp_killswitch_path):
    """파일 활성 → btc 정상이어도 is_active() True."""
    from strategy.btc_risk_off import BTCRiskOffState

    KillSwitch.activate(reason="manual", source="manual")
    now = datetime.now(timezone.utc)
    state = BTCRiskOffState(halted_until=None)
    assert KillSwitch.is_active(btc_state=state, now=now) is True


def test_get_status_corrupted_file_returns_unknown(tmp_killswitch_path):
    """JSON 파싱 실패 시 unknown 반환 (예외 X)."""
    tmp_killswitch_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_killswitch_path.write_text("not json {", encoding="utf-8")
    status = KillSwitch.get_status()
    assert status["reason"] == "unknown (file corrupted)"
