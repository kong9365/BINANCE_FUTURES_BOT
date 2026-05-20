"""
analytics/shadow_mode.py
=====================================================================
ShadowRecorder — 의사결정 병렬 기록 (shadow 모드)

근거:
  - docs/SPEC_v3.1.md §8-8-3 (shadow.record(strategy_name, decision),
    shadow.record_blocked("cost_guard", candidate, cost_check))
  - docs/SPEC_v3.1.md D-2 (Layer 6: ShadowRecorder.record())
  - docs/SPEC_v3.1.md §모듈표 ("ShadowRecorder | 있음 | 강화 | blocked_by 컬럼")
  - docs/SPEC_v3.1.md §13 (shadow_decisions 테이블 스키마)

책임:
  - 실제 진입한 결정을 shadow_decisions 에 기록 (was_taken_real=1)
  - 게이트에서 차단된 결정도 기록 (was_taken_real=0, blocked_by=<모듈명>)
  - 사후 결과(outcome_R) 업데이트로 "실행했더라면" 분석 데이터 축적

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지(+blocked_by 강화)"로 표기하므로 §8-8-3 /
    §13 의 호출 계약·스키마 기준으로 신규 작성한다.
  - §8-8-3 은 record / record_blocked 를 동기로 호출하므로 동기 메서드다
    (단일 행 INSERT — 메인 루프 부담 미미).
  - DB 오류는 logger.error 후 -1 을 반환한다 (기록 실패가 메인 루프를
    중단시키지 않도록 — shadow 기록은 보조 분석용).
=====================================================================
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _to_jsonable(obj):
    """dataclass / dict / 일반 객체를 JSON 직렬화 가능한 형태로 변환한다.

    - dataclass 인스턴스 → asdict
    - dict → 그대로
    - to_dict() 보유 객체 → to_dict()
    - 그 외 → str(obj)
    """
    if obj is None:
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    return str(obj)


class ShadowRecorder:
    """의사결정 병렬 기록기 (shadow_decisions 테이블).

    사용:
        shadow = ShadowRecorder(db_path="data/bot.db")
        shadow.record("v3_1_main", decision)               # 실제 진입
        shadow.record_blocked("cost_guard", candidate, cost_check)  # 차단됨
    """

    def __init__(self, db_path: str) -> None:
        """기록기 초기화.

        Args:
            db_path: sqlite3 DB 파일 경로 (shadow_decisions 테이블).
        """
        if not db_path:
            logger.warning("[Shadow] db_path 가 비어 있음 — 모든 기록이 실패합니다")
        self.db_path = db_path

    def record(self, strategy_name: str, decision: dict) -> int:
        """실제 진입한 결정을 기록한다 (was_taken_real=1).

        Args:
            strategy_name: 전략 식별자 (예: "v3_1_main").
            decision: 진입 결정 dict (symbol / action / setup_tag / entry_price /
                tp / sl 등). 전체 dict 는 snapshot_json 으로 저장된다.

        Returns:
            INSERT 된 행 id. 실패 시 -1.
        """
        return self._insert(
            strategy_name=strategy_name,
            symbol=decision.get("symbol"),
            candidate_action=decision.get("action"),
            candidate_setup=decision.get("setup_tag"),
            candidate_score=decision.get("score"),
            entry_price=decision.get("entry_price"),
            take_profit=decision.get("tp"),
            stop_loss=decision.get("sl"),
            was_taken_real=1,
            blocked_by=None,
            snapshot=decision,
        )

    def record_blocked(
        self,
        blocked_by: str,
        candidate,
        result=None,
        strategy_name: str = "v3_1_main",
    ) -> int:
        """게이트에서 차단된 결정을 기록한다 (was_taken_real=0).

        Args:
            blocked_by: 차단한 모듈 이름 (예: "cost_guard", "risk_manager").
            candidate: 차단된 후보 (symbol / price 속성 보유 — OIScanner Candidate 등).
            result: 차단 모듈의 결과 객체 (CostGuardResult 등). snapshot 에
                직렬화되어 저장된다. None 허용.
            strategy_name: 전략 식별자. 기본 "v3_1_main".

        Returns:
            INSERT 된 행 id. 실패 시 -1.
        """
        snapshot = {
            "candidate": _to_jsonable(candidate),
            "result": _to_jsonable(result),
            "blocked_by": blocked_by,
        }
        return self._insert(
            strategy_name=strategy_name,
            symbol=getattr(candidate, "symbol", None),
            candidate_action=getattr(candidate, "action", None),
            candidate_setup=getattr(candidate, "setup_tag", None),
            candidate_score=getattr(candidate, "score", None),
            entry_price=getattr(candidate, "price", None),
            take_profit=None,
            stop_loss=None,
            was_taken_real=0,
            blocked_by=blocked_by,
            snapshot=snapshot,
        )

    def update_outcome(self, shadow_id: int, outcome_R: float) -> bool:
        """기록된 shadow 결정에 사후 결과(outcome_R)를 업데이트한다.

        "실행했더라면 / 차단이 옳았는가" 분석을 위해 일정 시간 후 평가한 결과를
        outcome_R(R-multiple)로 기록한다.

        Args:
            shadow_id: record / record_blocked 가 반환한 행 id.
            outcome_R: 사후 평가 R-multiple.

        Returns:
            업데이트 성공 시 True, 실패 시 False.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE shadow_decisions "
                    "SET outcome_R = ?, evaluated_at = ? WHERE id = ?",
                    (outcome_R, now, shadow_id),
                )
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception as e:
            logger.error("[Shadow] outcome 업데이트 실패 (id=%s): %s", shadow_id, e)
            return False

    def _insert(
        self,
        *,
        strategy_name: str,
        symbol,
        candidate_action,
        candidate_setup,
        candidate_score,
        entry_price,
        take_profit,
        stop_loss,
        was_taken_real: int,
        blocked_by,
        snapshot,
    ) -> int:
        """shadow_decisions 에 한 행을 INSERT 하고 행 id 를 반환한다.

        Returns:
            INSERT 된 행 id. DB 오류 시 -1 (메인 루프 비중단).
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            snapshot_json = json.dumps(snapshot, default=str, ensure_ascii=False)
        except Exception as e:
            logger.warning("[Shadow] snapshot 직렬화 실패: %s", e)
            snapshot_json = json.dumps({"_serialization_error": str(e)})

        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO shadow_decisions (
                        timestamp, strategy_name, symbol,
                        candidate_action, candidate_setup, candidate_score,
                        entry_price, take_profit, stop_loss,
                        was_taken_real, blocked_by, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now, strategy_name, symbol,
                        candidate_action, candidate_setup, candidate_score,
                        entry_price, take_profit, stop_loss,
                        was_taken_real, blocked_by, snapshot_json,
                    ),
                )
                conn.commit()
                row_id = int(cur.lastrowid)
            finally:
                conn.close()
            logger.debug(
                "[Shadow] 기록 (id=%d, %s, taken=%d, blocked_by=%s)",
                row_id, symbol, was_taken_real, blocked_by,
            )
            return row_id
        except Exception as e:
            logger.error("[Shadow] shadow_decisions INSERT 실패: %s", e)
            return -1
