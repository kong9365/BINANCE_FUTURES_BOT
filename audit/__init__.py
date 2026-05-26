"""ALCOA+ 데이터 인티그리티 트레이서 (v3.2.0, 청사진 §6 + §7.2).

본 패키지는 청사진 §3.2.1 의 ALCOA+ 9원칙을 자동 적용:
  - Attributable: SignalDecision 의 setup_id / claude_session_id / signal_source
  - Legible: reasoning 필드 (자연어) + features_snapshot_json (구조화)
  - Contemporaneous: ts_signal_generated + ts_db_recorded (Outbox 패턴)
  - Original: raw_data_hash (sha256 of candles)
  - Accurate: append-only (audit_log UPDATE/DELETE SQLite trigger 차단)
  - Complete: SignalDecision + AgentReview + AuditLog 3중 추적
  - Consistent: params_hash (동일 입력 → 동일 결정 재현)
  - Enduring: Parquet + SQLite + GitHub 미러
  - Available: DuckDB 쿼리, Notion 대시보드 (M5+)

모듈:
  - signal_decision.SignalDecision  — 청사진 §6.1
  - agent_review.AgentReview         — 청사진 §6.3
  - audit_log.AuditLog               — 청사진 §6.4
  - chain                            — payload_hash / previous_log_hash (blockchain-like)
  - audit_logger.AuditLogger         — append-only INSERT + chain 자동 계산
"""

from audit.signal_decision import SignalDecision
from audit.agent_review import AgentReview
from audit.audit_log import AuditLog, EventType
from audit.chain import compute_payload_hash, compute_chain_hash
from audit.audit_logger import AuditLogger

__all__ = [
    "SignalDecision",
    "AgentReview",
    "AuditLog",
    "EventType",
    "compute_payload_hash",
    "compute_chain_hash",
    "AuditLogger",
]
