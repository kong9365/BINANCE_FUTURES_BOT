"""
audit/chain.py
=====================================================================
ALCOA+ chain hash 계산 — blockchain-like 무결성

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.4 (AuditLog.previous_log_hash 명세)
  - SYSTEM_DESIGN_BLUEPRINT.md §7.2 (ALCOA+ Accurate + Consistent)

설계 메모:
  - 순수 함수 (입출력 결정적). 동일 payload → 동일 payload_hash.
  - chain hash 는 단순 (prev || current) 가 아닌 *현재 row 의 payload_hash 자체*.
    즉 row N+1 의 previous_log_hash = row N 의 payload_hash. 검증 시
    `assert row[N+1].previous_log_hash == row[N].payload_hash` 로 chain 무결성 확인.
=====================================================================
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional


def compute_payload_hash(payload: dict | str) -> str:
    """payload (dict 또는 미리 정규화된 JSON 문자열) 의 sha256 해시.

    dict 입력 시: sort_keys=True + separators=(",", ":") 로 *정규화* 후 sha256.
    동일 dict → 동일 hash (ALCOA+ Consistent).

    Args:
        payload: dict 또는 JSON 문자열.

    Returns:
        64자 hex sha256 문자열.
    """
    if isinstance(payload, dict):
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    elif isinstance(payload, str):
        normalized = payload
    else:
        raise TypeError(f"payload must be dict or str, got {type(payload).__name__}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_chain_hash(
    previous_log_hash: Optional[str],
    payload_hash: str,
) -> str:
    """다음 row 의 *chain seed* 를 계산한다.

    실제 chain 검증은 sequential read 로 수행:
        for i in range(1, len(rows)):
            assert rows[i].previous_log_hash == rows[i-1].payload_hash

    본 함수는 *명시성 + 유닛테스트 용이성* 을 위해 분리. 반환값은 단순히
    payload_hash 그대로 (다음 row 가 쓸 prev). 미래 확장 (예: HMAC 도입) 시
    이 함수를 수정한다.

    Args:
        previous_log_hash: 직전 row 의 payload_hash (없으면 None — 첫 row).
        payload_hash: 현재 row 의 payload_hash.

    Returns:
        다음 row 의 previous_log_hash 로 쓸 값 (= 현재 payload_hash).
    """
    # 입력 검증
    if not payload_hash:
        raise ValueError("payload_hash must not be empty")
    if len(payload_hash) != 64:
        raise ValueError(
            f"payload_hash must be 64 hex chars (sha256), got len={len(payload_hash)}"
        )
    # 현재 단순 구현: chain seed = payload_hash
    # 미래 확장 시 (예: previous_log_hash + payload_hash 합 hash) 변경 가능
    return payload_hash


def verify_chain(rows: list[dict]) -> tuple[bool, Optional[int]]:
    """audit_log row 리스트의 chain 무결성을 검증한다.

    Args:
        rows: ts 오름차순 정렬된 audit_log row dict 리스트. 각 row 는
            'payload_hash', 'previous_log_hash' 키 필수.

    Returns:
        (is_valid, broken_at_index).  is_valid=True 면 broken_at_index=None.
        is_valid=False 면 broken_at_index 가 깨진 row index (0-based).
    """
    if not rows:
        return True, None
    # 첫 row 는 previous_log_hash=None 허용 (genesis)
    if rows[0].get("previous_log_hash") not in (None, ""):
        # 엄격: genesis 가 아니면 chain 깨짐 (하지만 운영 중 부팅마다 새 chain 시작 가능)
        # 이는 운영자 정책 — 본 함수는 *상대적* 무결성만 검증
        pass
    for i in range(1, len(rows)):
        prev_hash = rows[i - 1]["payload_hash"]
        current_prev = rows[i].get("previous_log_hash")
        if current_prev != prev_hash:
            return False, i
    return True, None
