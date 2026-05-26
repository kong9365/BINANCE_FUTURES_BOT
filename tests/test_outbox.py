"""tests/test_outbox.py — Outbox 패턴 검증."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db
from mcp.outbox import Outbox


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "outbox.db"
        init_db(db_path)
        yield str(db_path)


def test_enqueue_creates_pending_row(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    entry_id = outbox.enqueue("slack", {"text": "test"})
    assert entry_id > 0
    pending = outbox.get_pending("slack")
    assert len(pending) == 1
    assert pending[0].status == "pending"
    assert pending[0].retry_count == 0


def test_invalid_target_rejected(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    with pytest.raises(ValueError, match="target"):
        outbox.enqueue("invalid", {"text": "x"})


def test_non_dict_payload_rejected(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    with pytest.raises(TypeError):
        outbox.enqueue("slack", "not a dict")  # type: ignore


def test_mark_sent_removes_from_pending(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    entry_id = outbox.enqueue("slack", {"text": "test"})
    outbox.mark_sent(entry_id)
    pending = outbox.get_pending("slack")
    assert len(pending) == 0


def test_mark_failed_increments_retry(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    entry_id = outbox.enqueue("slack", {"text": "test"})
    permanent = outbox.mark_failed(entry_id, "network error", max_retries=5)
    assert permanent is False
    pending = outbox.get_pending("slack")
    assert pending[0].retry_count == 1


def test_mark_failed_permanent_after_max_retries(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    entry_id = outbox.enqueue("slack", {"text": "test"})
    # 5 retries
    for _ in range(5):
        outbox.mark_failed(entry_id, "fail", max_retries=5)
    # 5번째에서 permanent
    pending = outbox.get_pending("slack")
    assert len(pending) == 0  # permanent → not pending


def test_count_pending(tmp_db):
    outbox = Outbox(db_path=tmp_db)
    outbox.enqueue("slack", {"text": "1"})
    outbox.enqueue("slack", {"text": "2"})
    outbox.enqueue("slack", {"text": "3"})
    assert outbox.count_pending() == 3
