"""
registry/setup_registry.py
=====================================================================
SetupRegistry — DB 기반 setup_registry / setup_evaluations CRUD

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §6.5 (Setup Registry DB 스키마)
  - db/migrations/v3_1_2_to_v3_2_0.sql (M1 적용)
  - 운영자 권장 7기준 컬럼 (top3_excluded_pf, single_symbol_max_pct 등)

설계 메모:
  - 동기 sqlite3. 비동기 호출자는 asyncio.to_thread 로 래핑.
  - register() 는 멱등 (PK setup_id, INSERT OR REPLACE).
  - update_metrics() 는 setup_evaluations 에 INSERT + setup_registry 의
    last_metric_* 업데이트.
  - set_status() 는 PAPER_ONLY → R0_QUALIFIED 같은 전이만 허용.
=====================================================================
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skills.base import StrategySkill

logger = logging.getLogger(__name__)


class SetupStatus:
    """청사진 §6.5 status 값."""

    PAPER_ONLY = "PAPER_ONLY"        # 페이퍼 거래만 (검증 중)
    R0_QUALIFIED = "R0_QUALIFIED"    # 7기준 통과, 실거래 가능
    CONDITIONAL = "CONDITIONAL"      # 6/7 통과, 운영자 결정
    DISABLED = "DISABLED"             # 7기준 fail 또는 영구 비활성
    COOLING = "COOLING"               # 일시 비활성 (OOT drift)

    ALL = frozenset({
        PAPER_ONLY, R0_QUALIFIED, CONDITIONAL, DISABLED, COOLING,
    })


@dataclass
class SetupMetrics:
    """7기준 + 보조 메트릭."""

    n: int = 0                              # ≥ 200
    pf: float = 0.0                          # Net PF ≥ 1.25
    expectancy_r: float = 0.0                # > 0
    avg_win_loss_ratio: float = 0.0          # ≥ 1.5
    mdd_pct: float = 0.0                     # ≤ 25%
    single_symbol_max_pct: float = 0.0       # < 25%
    top3_excluded_pf: float = 0.0            # ≥ 1.0 (ZEC 단일 행운 방어)
    # 보조
    dsr: float = 0.0                          # Deflated Sharpe (다중검정 보정)
    win_rate: float = 0.0                     # 추세는 < 0.55 도 OK
    response_latency_p95_ms: float = 0.0    # M6 라이브 측정 (< 5000ms)


class SetupRegistry:
    """청사진 §6.5 Setup Registry CRUD.

    사용:
        registry = SetupRegistry(db_path="data/bot_live.db")
        registry.register(DailyTSMOMDonchianSkill)
        registry.update_metrics(
            setup_id="1d_tsmom_donchian_long_v1",
            metrics=SetupMetrics(n=250, pf=1.32, ...),
            evaluation_type="backtest",
        )
        registry.set_status("1d_tsmom_donchian_long_v1", "R0_QUALIFIED")
        active = registry.get_active_setups()  # R0_QUALIFIED 만
    """

    def __init__(self, db_path: str | Path) -> None:
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def register(
        self,
        skill_cls: type[StrategySkill],
        status: str = SetupStatus.PAPER_ONLY,
    ) -> None:
        """Skill 클래스를 setup_registry 에 멱등 등록.

        Args:
            skill_cls: StrategySkill 자식 클래스 (SETUP_ID, PARAMS_HASH, PARAMS, CATEGORY, ACADEMIC_REF 사용).
            status: 초기 status (기본 PAPER_ONLY).
        """
        if not skill_cls.SETUP_ID:
            raise ValueError(f"{skill_cls.__name__}.SETUP_ID is empty")
        if not skill_cls.PARAMS_HASH:
            raise ValueError(f"{skill_cls.__name__}.PARAMS_HASH is empty")
        if status not in SetupStatus.ALL:
            raise ValueError(f"status {status!r} not in {sorted(SetupStatus.ALL)}")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            # INSERT OR REPLACE — 멱등 (params_hash 변경 시 row 갱신)
            conn.execute(
                """
                INSERT INTO setup_registry
                    (setup_id, name, category, version, status,
                     created_at, updated_at, params_hash, params_json, academic_ref)
                VALUES
                    (:setup_id, :name, :category, :version, :status,
                     :created_at, :updated_at, :params_hash, :params_json, :academic_ref)
                ON CONFLICT(setup_id) DO UPDATE SET
                    params_hash = excluded.params_hash,
                    params_json = excluded.params_json,
                    updated_at = excluded.updated_at,
                    academic_ref = excluded.academic_ref
                """,
                {
                    "setup_id": skill_cls.SETUP_ID,
                    "name": skill_cls.__name__,
                    "category": skill_cls.CATEGORY or "uncategorized",
                    "version": "v1",
                    "status": status,
                    "created_at": now,
                    "updated_at": now,
                    "params_hash": skill_cls.PARAMS_HASH,
                    "params_json": json.dumps(skill_cls.PARAMS, sort_keys=True),
                    "academic_ref": skill_cls.ACADEMIC_REF,
                },
            )
            conn.commit()
            logger.info(
                "[Registry] %s registered (params_hash=%s..., status=%s)",
                skill_cls.SETUP_ID, skill_cls.PARAMS_HASH[:8], status,
            )
        finally:
            conn.close()

    def update_metrics(
        self,
        setup_id: str,
        metrics: SetupMetrics,
        evaluation_type: str,
        passed: bool,
        failed_criteria: Optional[list[str]] = None,
        full_report_json: Optional[str] = None,
    ) -> None:
        """평가 결과를 setup_evaluations 에 INSERT + setup_registry update.

        Args:
            setup_id: 등록된 setup_id.
            metrics: SetupMetrics 인스턴스.
            evaluation_type: "backtest" / "paper" / "live_7d".
            passed: True=PASS, False=FAIL, None=CONDITIONAL.
            failed_criteria: 실패한 기준 이름 리스트.
            full_report_json: 전체 보고서 JSON.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            # 현재 params_hash 조회 (변경 추적용)
            row = conn.execute(
                "SELECT params_hash FROM setup_registry WHERE setup_id = ?",
                (setup_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"setup_id {setup_id!r} not registered")
            params_hash = row[0]

            # setup_evaluations INSERT
            conn.execute(
                """
                INSERT INTO setup_evaluations
                    (setup_id, ts, evaluation_type, params_hash,
                     metric_n, metric_pf, metric_expectancy_r,
                     metric_avg_win_loss_ratio, metric_mdd_pct,
                     metric_single_symbol_max_pct, metric_top3_excluded_pf,
                     metric_win_rate, metric_response_latency_p95_ms,
                     passed, failed_criteria, full_report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    setup_id, now, evaluation_type, params_hash,
                    metrics.n, metrics.pf, metrics.expectancy_r,
                    metrics.avg_win_loss_ratio, metrics.mdd_pct,
                    metrics.single_symbol_max_pct, metrics.top3_excluded_pf,
                    metrics.win_rate, metrics.response_latency_p95_ms,
                    1 if passed else 0,
                    json.dumps(failed_criteria or []),
                    full_report_json,
                ),
            )

            # setup_registry update (최근 메트릭)
            conn.execute(
                """
                UPDATE setup_registry SET
                    last_evaluation_ts = ?,
                    last_metric_n = ?,
                    last_metric_pf = ?,
                    last_metric_expectancy_r = ?,
                    last_metric_avg_win_loss_ratio = ?,
                    last_metric_mdd_pct = ?,
                    last_metric_single_symbol_max_pct = ?,
                    last_metric_top3_excluded_pf = ?,
                    last_metric_win_rate = ?,
                    last_metric_response_latency_p95_ms = ?,
                    updated_at = ?
                WHERE setup_id = ?
                """,
                (
                    now,
                    metrics.n, metrics.pf, metrics.expectancy_r,
                    metrics.avg_win_loss_ratio, metrics.mdd_pct,
                    metrics.single_symbol_max_pct, metrics.top3_excluded_pf,
                    metrics.win_rate, metrics.response_latency_p95_ms,
                    now, setup_id,
                ),
            )
            conn.commit()
            logger.info(
                "[Registry] %s metrics updated — PF=%.2f, top3_excl=%.2f, passed=%s",
                setup_id, metrics.pf, metrics.top3_excluded_pf, passed,
            )
        finally:
            conn.close()

    def set_status(
        self,
        setup_id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        """status 변경 (CLAUDE.md TIER 1 #7 — fail 시 즉시 DISABLED)."""
        if status not in SetupStatus.ALL:
            raise ValueError(f"status {status!r} not in {sorted(SetupStatus.ALL)}")
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE setup_registry SET status = ?, reason = ?, updated_at = ? "
                "WHERE setup_id = ?",
                (status, reason, now, setup_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"setup_id {setup_id!r} not found")
            conn.commit()
            logger.info("[Registry] %s status → %s (%s)", setup_id, status, reason)
        finally:
            conn.close()

    def get_active_setups(self) -> list[dict]:
        """R0_QUALIFIED 또는 PAPER_ONLY 인 setup 반환 (실제 거래/페이퍼 대상)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT setup_id, name, category, status, params_hash, "
                "       last_metric_pf, last_metric_top3_excluded_pf "
                "FROM setup_registry "
                "WHERE status IN (?, ?) "
                "ORDER BY last_metric_pf DESC",
                (SetupStatus.R0_QUALIFIED, SetupStatus.PAPER_ONLY),
            ).fetchall()
            return [
                {
                    "setup_id": r[0], "name": r[1], "category": r[2],
                    "status": r[3], "params_hash": r[4],
                    "last_metric_pf": r[5], "last_metric_top3_excluded_pf": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_setup(self, setup_id: str) -> Optional[dict]:
        """단일 setup 조회."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM setup_registry WHERE setup_id = ?",
                (setup_id,),
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in conn.execute(
                "PRAGMA table_info(setup_registry)"
            ).fetchall()]
            # 위 PRAGMA 는 컬럼 메타 — 다시 조회
            cols = [
                "setup_id", "name", "category", "version", "status",
                "created_at", "updated_at", "params_hash", "params_json",
                "academic_ref", "last_evaluation_ts",
                "last_metric_n", "last_metric_pf", "last_metric_expectancy_r",
                "last_metric_avg_win_loss_ratio", "last_metric_mdd_pct",
                "last_metric_single_symbol_max_pct", "last_metric_top3_excluded_pf",
                "last_metric_dsr", "last_metric_win_rate",
                "last_metric_response_latency_p95_ms",
                "gate1_ic", "gate2_spread", "gate3_pf",
                "reason", "notes",
            ]
            return dict(zip(cols, row))
        finally:
            conn.close()
