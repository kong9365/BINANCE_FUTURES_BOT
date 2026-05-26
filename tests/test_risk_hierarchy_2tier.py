"""
tests/test_risk_hierarchy_2tier.py
=====================================================================
2-tier risk hierarchy 검증 (운영자 결정 #v2-2).
=====================================================================
"""

from __future__ import annotations

import pytest

from governance.risk_hierarchy import (
    RISK_HIERARCHY_CONFIG, RiskHierarchyConfig,
    check_daily_loss, check_mdd,
)


def test_default_thresholds():
    """기본 임계값 확인 — warn -1.0%, hard -1.5% (운영자 결정)."""
    cfg = RISK_HIERARCHY_CONFIG
    assert cfg.daily_warn_pct == 0.010
    assert cfg.daily_hard_pct == 0.015
    assert cfg.mdd_warn_pct == 0.10
    assert cfg.mdd_hard_pct == 0.15


def test_daily_loss_ok():
    """-0.5% → OK."""
    assert check_daily_loss(current_wallet=995, daily_start=1000) == "OK"


def test_daily_loss_warn():
    """-1.0% 도달 → WARN (Slack 경고)."""
    assert check_daily_loss(current_wallet=990, daily_start=1000) == "WARN"


def test_daily_loss_warn_borderline():
    """-1.4% → WARN (hard -1.5% 미도달)."""
    assert check_daily_loss(current_wallet=986, daily_start=1000) == "WARN"


def test_daily_loss_hard():
    """-1.5% 도달 → HARD (KillSwitch)."""
    assert check_daily_loss(current_wallet=985, daily_start=1000) == "HARD"


def test_daily_loss_beyond_hard():
    """-2.0% (이미 차단된 상태, 도달 X 정상 시나리오) → HARD."""
    # 실제 운영에서는 -1.5%에서 이미 KillSwitch 활성화되어 도달 X.
    # 하지만 함수는 평가 가능.
    assert check_daily_loss(current_wallet=980, daily_start=1000) == "HARD"


def test_daily_loss_zero_baseline():
    """daily_start=0 → UNKNOWN."""
    assert check_daily_loss(current_wallet=100, daily_start=0) == "UNKNOWN"


def test_mdd_ok():
    """-5% → OK."""
    assert check_mdd(current_wallet=950, initial=1000) == "OK"


def test_mdd_warn():
    """-10% → WARN."""
    assert check_mdd(current_wallet=900, initial=1000) == "WARN"


def test_mdd_hard():
    """-15% → HARD."""
    assert check_mdd(current_wallet=850, initial=1000) == "HARD"


def test_mdd_zero_baseline():
    """initial=0 → UNKNOWN."""
    assert check_mdd(current_wallet=100, initial=0) == "UNKNOWN"


def test_custom_config():
    """custom RiskHierarchyConfig 적용 가능."""
    cfg = RiskHierarchyConfig(daily_warn_pct=0.005, daily_hard_pct=0.01)
    # -0.5% 도달 → WARN
    assert check_daily_loss(995, 1000, cfg) == "WARN"
    # -1.0% 도달 → HARD
    assert check_daily_loss(990, 1000, cfg) == "HARD"


def test_2tier_reachable_simulation():
    """운영자 결정 #문제2 (3-tier 문제) 검증: warn 도달 가능 + hard 별도 도달."""
    # 동일 baseline 1000, 다양한 손실 시나리오:
    scenarios = [
        (1000, "OK"),     # 변동 없음
        (999, "OK"),       # -0.1%
        (995, "OK"),       # -0.5%
        (990, "WARN"),     # -1.0% (warn 도달)
        (986, "WARN"),     # -1.4% (warn 유지)
        (985, "HARD"),     # -1.5% (hard 도달)
        (980, "HARD"),     # -2.0% (hard 초과 — 정상에서는 도달 X)
    ]
    for current, expected in scenarios:
        actual = check_daily_loss(current, 1000)
        assert actual == expected, (
            f"current={current} expected={expected} actual={actual}"
        )


def test_config_exported_from_settings():
    """config/settings.py 에서 re-export 확인."""
    from config.settings import RISK_HIERARCHY_CONFIG as exported
    assert exported is RISK_HIERARCHY_CONFIG  # 같은 인스턴스
