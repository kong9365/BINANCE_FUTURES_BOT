"""
tests/test_m15_update_registry_metrics.py
=====================================================================
M15-1 — registry 메트릭 갱신 스크립트 검증.

scripts/m15_update_registry_metrics.py 가 setup_registry 를 M13 v2 실측값으로
*진실되게* 갱신하는지 + 멱등인지 확인.
=====================================================================
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db
from registry.setup_registry import SetupRegistry, SetupStatus
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill

import scripts.m15_update_registry_metrics as m15


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "m15_test.db"
        init_db(db_path)
        yield str(db_path)


def _run(db_path: str, monkeypatch) -> int:
    monkeypatch.setattr(
        "sys.argv", ["m15_update_registry_metrics.py", "--db-path", db_path]
    )
    return m15.main()


def test_script_applies_m13_v2_metrics(tmp_db, monkeypatch):
    """스크립트 실행 → registry 가 M13 v2 실측값 + CONDITIONAL."""
    rc = _run(tmp_db, monkeypatch)
    assert rc == 0

    reg = SetupRegistry(db_path=tmp_db)
    s = reg.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert s is not None
    # M13 v2 실측 (docs/SETUP_VERIFICATION_REPORT_v3_2_0.md)
    assert s["last_metric_n"] == 216
    assert s["last_metric_pf"] == pytest.approx(1.260)
    assert s["last_metric_expectancy_r"] == pytest.approx(0.1178)
    assert s["last_metric_avg_win_loss_ratio"] == pytest.approx(1.357)
    assert s["last_metric_mdd_pct"] == pytest.approx(9.92)        # NOT 204.74 (모델 결함)
    assert s["last_metric_single_symbol_max_pct"] == pytest.approx(8.07)
    assert s["last_metric_top3_excluded_pf"] == pytest.approx(1.102)
    assert s["last_metric_win_rate"] == pytest.approx(0.481)
    # 6/7 → CONDITIONAL (passed=False 이지만 status 는 CONDITIONAL)
    assert s["status"] == SetupStatus.CONDITIONAL


def test_script_is_idempotent(tmp_db, monkeypatch):
    """반복 실행 안전 — 두 번 돌려도 동일 최종 상태."""
    assert _run(tmp_db, monkeypatch) == 0
    assert _run(tmp_db, monkeypatch) == 0
    reg = SetupRegistry(db_path=tmp_db)
    s = reg.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert s["status"] == SetupStatus.CONDITIONAL
    assert s["last_metric_pf"] == pytest.approx(1.260)


def test_mdd_is_corrected_value_not_raw(tmp_db, monkeypatch):
    """CLAUDE.md TIER 1 #7 — MDD 는 9.92% (raw 204.74% 모델 결함 아님)."""
    _run(tmp_db, monkeypatch)
    reg = SetupRegistry(db_path=tmp_db)
    s = reg.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert s["last_metric_mdd_pct"] < 25.0          # 7기준 통과 (≤25%)
    assert s["last_metric_mdd_pct"] == pytest.approx(9.92)


def test_evaluation_row_recorded(tmp_db, monkeypatch):
    """setup_evaluations 에 평가 1건 + failed_criteria 기록."""
    import sqlite3

    _run(tmp_db, monkeypatch)
    conn = sqlite3.connect(tmp_db)
    try:
        rows = conn.execute(
            "SELECT passed, failed_criteria FROM setup_evaluations WHERE setup_id = ?",
            (DailyTSMOMDonchianSkill.SETUP_ID,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 0                       # passed=False
        assert "min_avg_win_loss_ratio" in rows[0][1]
    finally:
        conn.close()
