"""
tests/test_shadow_mode.py
=====================================================================
ShadowRecorder 단위 테스트.

근거:
  - docs/SPEC_v3.1.md §8-8-3 (record / record_blocked)
  - docs/SPEC_v3.1.md §13 (shadow_decisions 스키마)

구성:
  - DB 는 tmp_path 임시 SQLite (db.init_db.init_db 로 schema 적용)

검증 시나리오:
  1. record → was_taken_real=1, blocked_by NULL, snapshot_json 보존
  2. record_blocked → was_taken_real=0, blocked_by 기록
  3. record_blocked + dataclass result → snapshot 직렬화
  4. update_outcome → outcome_R / evaluated_at 갱신
  5. DB 경로 오류 → -1 반환 (메인 루프 비중단)
  6. _to_jsonable — dataclass / dict / to_dict / 기타
=====================================================================
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pytest

from analytics.shadow_mode import ShadowRecorder, _to_jsonable
from db.init_db import init_db


# ── fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> str:
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _fetch(db_path: str, row_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM shadow_decisions WHERE id = ?", (row_id,)
        ).fetchone()
    finally:
        conn.close()


@dataclass
class _FakeCandidate:
    symbol: str
    price: float


@dataclass
class _FakeResult:
    passed: bool
    expected_value: float
    reason: str


_DECISION = {
    "symbol": "SOLUSDT",
    "action": "LONG",
    "setup_tag": "oi_surge_long",
    "entry_price": 100.0,
    "tp": 102.0,
    "sl": 99.0,
    "score": 78,
}


# ── 1. record ───────────────────────────────────────────────────────

def test_record_taken_real(db_path):
    """record → was_taken_real=1, blocked_by NULL, 필드/snapshot 보존."""
    rec = ShadowRecorder(db_path)
    row_id = rec.record("v3_1_main", _DECISION)
    assert row_id > 0

    row = _fetch(db_path, row_id)
    assert row["strategy_name"] == "v3_1_main"
    assert row["symbol"] == "SOLUSDT"
    assert row["candidate_action"] == "LONG"
    assert row["candidate_setup"] == "oi_surge_long"
    assert row["candidate_score"] == 78
    assert row["entry_price"] == 100.0
    assert row["take_profit"] == 102.0
    assert row["stop_loss"] == 99.0
    assert row["was_taken_real"] == 1
    assert row["blocked_by"] is None
    assert json.loads(row["snapshot_json"])["setup_tag"] == "oi_surge_long"


# ── 2~3. record_blocked ─────────────────────────────────────────────

def test_record_blocked(db_path):
    """record_blocked → was_taken_real=0, blocked_by 기록."""
    rec = ShadowRecorder(db_path)
    candidate = _FakeCandidate(symbol="SOLUSDT", price=150.0)
    row_id = rec.record_blocked("cost_guard", candidate)
    assert row_id > 0

    row = _fetch(db_path, row_id)
    assert row["was_taken_real"] == 0
    assert row["blocked_by"] == "cost_guard"
    assert row["symbol"] == "SOLUSDT"
    assert row["entry_price"] == 150.0


def test_record_blocked_serializes_dataclass_result(db_path):
    """record_blocked + dataclass result → snapshot 에 직렬화 저장."""
    rec = ShadowRecorder(db_path)
    candidate = _FakeCandidate(symbol="SOLUSDT", price=150.0)
    result = _FakeResult(passed=False, expected_value=-1.2, reason="EV 음수")
    row_id = rec.record_blocked("cost_guard", candidate, result)

    snapshot = json.loads(_fetch(db_path, row_id)["snapshot_json"])
    assert snapshot["blocked_by"] == "cost_guard"
    assert snapshot["candidate"]["symbol"] == "SOLUSDT"
    assert snapshot["result"]["expected_value"] == -1.2
    assert snapshot["result"]["passed"] is False


# ── 4. update_outcome ───────────────────────────────────────────────

def test_update_outcome(db_path):
    """update_outcome → outcome_R / evaluated_at 갱신."""
    rec = ShadowRecorder(db_path)
    row_id = rec.record("v3_1_main", _DECISION)
    assert rec.update_outcome(row_id, outcome_R=2.5) is True

    row = _fetch(db_path, row_id)
    assert row["outcome_R"] == 2.5
    assert row["evaluated_at"] is not None


# ── 5. DB 오류 → -1 ─────────────────────────────────────────────────

def test_record_db_error_returns_minus_one(tmp_path):
    """존재하지 않는 DB 경로 → INSERT 실패, -1 반환 (메인 루프 비중단)."""
    bad_path = str(tmp_path / "nonexistent_dir" / "bot.db")
    rec = ShadowRecorder(bad_path)
    assert rec.record("v3_1_main", _DECISION) == -1


def test_update_outcome_db_error_returns_false(tmp_path):
    """존재하지 않는 DB 경로 → update_outcome False."""
    bad_path = str(tmp_path / "nonexistent_dir" / "bot.db")
    rec = ShadowRecorder(bad_path)
    assert rec.update_outcome(1, 1.0) is False


# ── 6. _to_jsonable ─────────────────────────────────────────────────

def test_to_jsonable_variants():
    """_to_jsonable — dataclass / dict / to_dict 보유 객체 / 기타."""
    assert _to_jsonable(None) is None
    assert _to_jsonable({"a": 1}) == {"a": 1}
    assert _to_jsonable(_FakeResult(True, 1.0, "ok")) == {
        "passed": True, "expected_value": 1.0, "reason": "ok"
    }

    class _HasToDict:
        def to_dict(self):
            return {"x": 42}

    assert _to_jsonable(_HasToDict()) == {"x": 42}
    assert _to_jsonable(12345) == "12345"
