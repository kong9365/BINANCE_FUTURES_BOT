"""
governance/preflight.py
=====================================================================
Preflight — 시스템 시작 시 1회 검증

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.4.3 (Kill Switch 시작 시 작동 시점)
  - SYSTEM_DESIGN_BLUEPRINT.md §10.5 비상 절차

검증 항목 (M1):
  1. KILLSWITCH 파일 디렉토리 mkdir
  2. DB 마이그레이션 v3.2.0 적용 확인
  3. KILLSWITCH 활성 상태면 즉시 종료
  4. (M4 이후) API 키 권한, 외장 SSD mount

설계 메모:
  - 본 모듈은 *동기* (시작 시점에 호출). main_7590.start() 진입에 통합.
  - 실패 시 *예외 전파* — 시작 차단.
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from governance.kill_switch import KillSwitch

logger = logging.getLogger(__name__)


@dataclass
class PreflightResult:
    """Preflight 검증 결과."""

    passed: bool
    checks: dict[str, bool]    # 체크 항목별 결과
    failed_checks: list[str]   # 실패한 체크 이름 목록
    kill_switch_active: bool   # KILLSWITCH 파일 존재 여부
    kill_switch_status: Optional[dict] = None


def run_preflight(db_path: str | Path) -> PreflightResult:
    """시작 시 1회 검증.

    Args:
        db_path: sqlite3 DB 파일 경로.

    Returns:
        PreflightResult — passed=False 면 운영자 개입 필요.
    """
    if not db_path:
        raise ValueError("db_path must not be empty")

    checks: dict[str, bool] = {}
    failed: list[str] = []

    # 1. KILLSWITCH 파일 디렉토리 mkdir (이미 있으면 무시)
    try:
        ks_path = KillSwitch.path()
        ks_path.parent.mkdir(parents=True, exist_ok=True)
        checks["killswitch_dir"] = True
    except Exception as e:  # noqa: BLE001
        logger.error("[Preflight] KILLSWITCH 디렉토리 생성 실패: %s", e)
        checks["killswitch_dir"] = False
        failed.append("killswitch_dir")

    # 2. DB 마이그레이션 v3.2.0 적용 확인
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT version FROM schema_migrations WHERE version = 'v3.2.0'"
            ).fetchone()
            checks["db_migration_v3_2_0"] = row is not None
            if row is None:
                failed.append("db_migration_v3_2_0")
                logger.error(
                    "[Preflight] v3.2.0 마이그레이션 미적용 — `python db/init_db.py` 실행 필요"
                )
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        logger.error("[Preflight] DB 접근 실패: %s", e)
        checks["db_migration_v3_2_0"] = False
        failed.append("db_migration_v3_2_0")

    # 3. KILLSWITCH 활성 상태 확인 (있으면 시작 차단)
    ks_active = KillSwitch.is_active()
    ks_status = KillSwitch.get_status()
    if ks_active:
        logger.critical(
            "[Preflight] ⚠️ KILLSWITCH 활성 — 시스템 시작 차단 (status=%s)",
            ks_status,
        )
        failed.append("kill_switch_active")

    # 4. (M4 이후) API 키 권한 / 외장 SSD mount 추가

    return PreflightResult(
        passed=len(failed) == 0,
        checks=checks,
        failed_checks=failed,
        kill_switch_active=ks_active,
        kill_switch_status=ks_status,
    )
