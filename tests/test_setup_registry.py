"""
tests/test_setup_registry.py
=====================================================================
SetupRegistry CRUD 검증.
=====================================================================
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db
from registry.setup_registry import SetupMetrics, SetupRegistry, SetupStatus
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "registry_test.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def registry(tmp_db):
    return SetupRegistry(db_path=tmp_db)


def test_register_skill(registry, tmp_db):
    """skill 클래스 등록 — params_hash 자동."""
    registry.register(DailyTSMOMDonchianSkill)
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT setup_id, name, category, status, params_hash "
            "FROM setup_registry WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchone()
        assert row is not None
        assert row[0] == "1d_tsmom_donchian_long_v1"
        assert row[1] == "DailyTSMOMDonchianSkill"
        assert row[2] == "trend"
        assert row[3] == SetupStatus.PAPER_ONLY
        assert row[4] == DailyTSMOMDonchianSkill.PARAMS_HASH
    finally:
        conn.close()


def test_register_idempotent(registry, tmp_db):
    """동일 skill 두 번 register — 멱등 (PK 충돌 X)."""
    registry.register(DailyTSMOMDonchianSkill)
    registry.register(DailyTSMOMDonchianSkill)
    conn = sqlite3.connect(tmp_db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM setup_registry WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_update_metrics(registry, tmp_db):
    """update_metrics — setup_evaluations INSERT + setup_registry update."""
    registry.register(DailyTSMOMDonchianSkill)
    metrics = SetupMetrics(
        n=250, pf=1.35, expectancy_r=0.15, avg_win_loss_ratio=1.8,
        mdd_pct=22.0, single_symbol_max_pct=18.0, top3_excluded_pf=1.15,
        win_rate=0.55, response_latency_p95_ms=3200.0,
    )
    registry.update_metrics(
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        metrics=metrics,
        evaluation_type="backtest",
        passed=True,
    )
    conn = sqlite3.connect(tmp_db)
    try:
        # setup_evaluations INSERT 확인
        eval_count = conn.execute(
            "SELECT COUNT(*) FROM setup_evaluations WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchone()[0]
        assert eval_count == 1

        # setup_registry update 확인
        row = conn.execute(
            "SELECT last_metric_pf, last_metric_top3_excluded_pf "
            "FROM setup_registry WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchone()
        assert row[0] == 1.35
        assert row[1] == 1.15
    finally:
        conn.close()


def test_set_status(registry):
    """status 전환."""
    registry.register(DailyTSMOMDonchianSkill)
    registry.set_status(
        DailyTSMOMDonchianSkill.SETUP_ID,
        SetupStatus.R0_QUALIFIED,
        reason="7-criteria all passed",
    )
    setup = registry.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert setup["status"] == SetupStatus.R0_QUALIFIED
    assert "7-criteria" in setup["reason"]


def test_set_status_invalid_rejected(registry):
    """잘못된 status 거부."""
    registry.register(DailyTSMOMDonchianSkill)
    with pytest.raises(ValueError, match="status"):
        registry.set_status(
            DailyTSMOMDonchianSkill.SETUP_ID, "INVALID_STATUS",
        )


def test_set_status_unknown_setup(registry):
    """등록되지 않은 setup_id → ValueError."""
    with pytest.raises(ValueError, match="not found"):
        registry.set_status("nonexistent", SetupStatus.DISABLED)


def test_update_metrics_unregistered_rejected(registry):
    """등록되지 않은 setup_id 에 metrics update 시 ValueError."""
    metrics = SetupMetrics(n=100, pf=1.0)
    with pytest.raises(ValueError, match="not registered"):
        registry.update_metrics(
            setup_id="nonexistent",
            metrics=metrics,
            evaluation_type="backtest",
            passed=False,
        )


def test_get_active_setups_filters_by_status(registry):
    """get_active_setups() — R0_QUALIFIED + PAPER_ONLY 만."""
    registry.register(DailyTSMOMDonchianSkill)
    # 1개 등록 (PAPER_ONLY)
    active = registry.get_active_setups()
    assert len(active) == 1
    assert active[0]["setup_id"] == DailyTSMOMDonchianSkill.SETUP_ID

    # DISABLED 로 변경 → active 에서 사라짐
    registry.set_status(
        DailyTSMOMDonchianSkill.SETUP_ID, SetupStatus.DISABLED,
    )
    active = registry.get_active_setups()
    assert active == []


def test_consistent_params_hash_across_register(registry, tmp_db):
    """동일 클래스 재등록 — params_hash 동일 (Consistent)."""
    registry.register(DailyTSMOMDonchianSkill)
    first_hash = DailyTSMOMDonchianSkill.PARAMS_HASH

    registry.register(DailyTSMOMDonchianSkill)
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT params_hash FROM setup_registry WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchone()
        assert row[0] == first_hash
    finally:
        conn.close()
