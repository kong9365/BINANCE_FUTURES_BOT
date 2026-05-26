"""
tests/test_audit_chain.py
=====================================================================
chain hash 함수 검증 — ALCOA+ Consistent + Accurate.

근거: audit/chain.py + SYSTEM_DESIGN_BLUEPRINT.md §6.4 + §7.2
=====================================================================
"""

from __future__ import annotations

import pytest

from audit.chain import compute_payload_hash, compute_chain_hash, verify_chain


def test_payload_hash_deterministic_dict():
    """동일 dict → 동일 hash (Consistent)."""
    p = {"k1": "v1", "k2": 42, "k3": [1, 2, 3]}
    h1 = compute_payload_hash(p)
    h2 = compute_payload_hash(p)
    assert h1 == h2
    assert len(h1) == 64  # sha256


def test_payload_hash_key_order_independent():
    """dict 키 순서 무관 (sort_keys 적용)."""
    p1 = {"a": 1, "b": 2}
    p2 = {"b": 2, "a": 1}
    assert compute_payload_hash(p1) == compute_payload_hash(p2)


def test_payload_hash_string_input():
    """미리 정규화된 JSON 문자열도 동작."""
    import json
    p = {"x": 1}
    j = json.dumps(p, sort_keys=True, separators=(",", ":"))
    assert compute_payload_hash(p) == compute_payload_hash(j)


def test_payload_hash_invalid_type():
    """dict / str 외 입력 거부."""
    with pytest.raises(TypeError):
        compute_payload_hash(123)  # type: ignore


def test_different_payloads_different_hash():
    """다른 payload → 다른 hash (Original)."""
    h1 = compute_payload_hash({"a": 1})
    h2 = compute_payload_hash({"a": 2})
    assert h1 != h2


def test_compute_chain_hash_validates_length():
    """payload_hash 길이 검증."""
    with pytest.raises(ValueError, match="64 hex"):
        compute_chain_hash(previous_log_hash=None, payload_hash="too_short")


def test_compute_chain_hash_empty_rejected():
    """empty payload_hash 거부."""
    with pytest.raises(ValueError, match="empty"):
        compute_chain_hash(previous_log_hash=None, payload_hash="")


def test_compute_chain_hash_returns_payload_hash():
    """현재 구현: chain seed = payload_hash 그대로."""
    ph = "a" * 64
    seed = compute_chain_hash(previous_log_hash=None, payload_hash=ph)
    assert seed == ph


def test_verify_chain_empty():
    """빈 리스트는 valid."""
    valid, idx = verify_chain([])
    assert valid is True
    assert idx is None


def test_verify_chain_valid_sequence():
    """3 row chain 정상 — 각 row.previous = prev.payload_hash."""
    rows = [
        {"payload_hash": "h1", "previous_log_hash": None},
        {"payload_hash": "h2", "previous_log_hash": "h1"},
        {"payload_hash": "h3", "previous_log_hash": "h2"},
    ]
    valid, idx = verify_chain(rows)
    assert valid is True
    assert idx is None


def test_verify_chain_broken():
    """중간 row의 previous_log_hash 가 잘못 → broken."""
    rows = [
        {"payload_hash": "h1", "previous_log_hash": None},
        {"payload_hash": "h2", "previous_log_hash": "h1"},
        {"payload_hash": "h3", "previous_log_hash": "WRONG"},  # 깨짐
    ]
    valid, idx = verify_chain(rows)
    assert valid is False
    assert idx == 2
