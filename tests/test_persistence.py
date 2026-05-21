"""
tests/test_persistence.py
=====================================================================
data/persistence.py — Supabase 주저장 + 로컬 sqlite 폴백 어댑터 단위 테스트.

  - Supabase 클라이언트는 MagicMock 주입(실제 네트워크 없음 — CLAUDE.md 규칙)
  - outbox 는 tmp_path
검증:
  1. insert 성공 → 클라이언트 호출, outbox 비어 있음
  2. insert 실패 → outbox 1건 적재, False 반환
  3. flush_outbox → 재전송 후 outbox 비움
  4. flush 중 재실패 → 순서 보존하며 중단(잔여 유지)
  5. degraded 모드(client=None) → 항상 outbox, flush 0
  6. upsert 누적 테이블 → on_conflict 키 전달
  7. 편의 래퍼(save_oi/log_event) 동작
  8. datetime → ISO 정규화
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from data.persistence import SupabasePersistence


def _ok_client():
    """insert/upsert.execute 가 정상 동작하는 mock."""
    return MagicMock()


def _failing_client():
    c = MagicMock()
    c.table.return_value.insert.return_value.execute.side_effect = RuntimeError("net down")
    c.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("net down")
    return c


def _p(client, tmp_path):
    return SupabasePersistence(
        client=client, outbox_path=str(tmp_path / "outbox.db"), auto_flush=False
    )


def test_insert_success_calls_client_no_outbox(tmp_path):
    client = _ok_client()
    p = _p(client, tmp_path)
    assert p.insert("trades", {"symbol": "SOLUSDT", "action": "LONG"}) is True
    client.table.assert_called_with("trades")
    client.table.return_value.insert.assert_called_once()
    assert p.pending_count() == 0


def test_insert_failure_enqueues_outbox(tmp_path):
    p = _p(_failing_client(), tmp_path)
    assert p.insert("trades", {"symbol": "SOLUSDT"}) is False
    assert p.pending_count() == 1


def test_flush_replays_and_clears(tmp_path):
    # 먼저 실패로 적재
    p = _p(_failing_client(), tmp_path)
    p.insert("trades", {"symbol": "SOLUSDT"})
    p.insert("signals", {"symbol": "XRPUSDT", "strategy": "breakout"})
    assert p.pending_count() == 2
    # 클라이언트가 복구됨 → flush
    p.client = _ok_client()
    sent = p.flush_outbox()
    assert sent == 2
    assert p.pending_count() == 0
    assert p.client.table.return_value.insert.call_count == 2


def test_flush_stops_on_repeated_failure(tmp_path):
    p = _p(_failing_client(), tmp_path)
    p.insert("trades", {"a": 1})
    p.insert("trades", {"a": 2})
    # 여전히 실패하는 클라이언트로 flush → 0건, 순서 보존(잔여 유지)
    sent = p.flush_outbox()
    assert sent == 0
    assert p.pending_count() == 2


def test_degraded_mode_no_client(tmp_path):
    p = SupabasePersistence(
        client=None, url=None, key=None,
        outbox_path=str(tmp_path / "outbox.db"), auto_flush=False,
    )
    assert p.client is None
    assert p.insert("trades", {"x": 1}) is False
    assert p.pending_count() == 1
    assert p.flush_outbox() == 0          # 클라이언트 없으면 flush 불가


def test_upsert_passes_on_conflict(tmp_path):
    client = _ok_client()
    p = _p(client, tmp_path)
    p.save_oi("SOLUSDT", "2026-05-21T00:00:00+00:00", 12345.6)
    client.table.assert_called_with("oi_history")
    _, kwargs = client.table.return_value.upsert.call_args
    assert kwargs.get("on_conflict") == "symbol,ts,period"


def test_convenience_log_event(tmp_path):
    client = _ok_client()
    p = _p(client, tmp_path)
    assert p.log_event("INFO", "main", "봇 시작", {"k": "v"}) is True
    client.table.assert_called_with("app_events")
    payload = client.table.return_value.insert.call_args[0][0]
    assert payload["level"] == "INFO" and payload["component"] == "main"
    assert "ts" in payload


def test_datetime_normalized_to_iso(tmp_path):
    client = _ok_client()
    p = _p(client, tmp_path)
    dt = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    p.insert("equity_snapshots", {"ts": dt, "wallet_balance": 100.0})
    payload = client.table.return_value.insert.call_args[0][0]
    assert isinstance(payload["ts"], str) and payload["ts"].startswith("2026-05-21T12:00")
