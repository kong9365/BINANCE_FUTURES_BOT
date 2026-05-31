"""DB 초기화 — schema.sql 적용 + 마이그레이션 등록.

명세서 §13 + 부록 E-7 + 청사진 §6 (M1 v3.2.0 신규).

실행 순서:
    1. data/ 디렉토리 생성, data/bot.db 연결
    2. db/schema.sql 적용 (v3.1 베이스, IF NOT EXISTS 로 멱등)
    3. schema_migrations 에 'v3.1' 미등록 시 등록
    4. schema_migrations 에 'v3.1.1' 미등록 시 v3_1_to_v3_1_1.sql 적용
       (ALTER TABLE 포함 — 미등록일 때만 실행하므로 재실행 안전)
    5. schema_migrations 에 'v3.1.2' 미등록 시 v3_1_1_to_v3_1_2.sql 적용
       (trades 거래소 주문 추적 컬럼 — 미등록일 때만 실행)
    6. schema_migrations 에 'v3.2.0' 미등록 시 v3_1_2_to_v3_2_0.sql 적용
       (청사진 §6 데이터 모델 — setup_registry, signal_decisions, agent_reviews,
        audit_log, kill_switch_events + audit_log append-only trigger)

`python db/init_db.py` 로 직접 실행하거나, init_db()/get_applied_versions()
를 import 해서 사용한다 (테스트 등).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# 경로 (db/ 의 부모 = 프로젝트 루트 기준)
DB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DB_DIR.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bot.db"

SCHEMA_PATH = DB_DIR / "schema.sql"
MIGRATIONS_DIR = DB_DIR / "migrations"
MIGRATION_V3_1_1 = MIGRATIONS_DIR / "v3_1_to_v3_1_1.sql"
MIGRATION_V3_1_2 = MIGRATIONS_DIR / "v3_1_1_to_v3_1_2.sql"
MIGRATION_V3_2_0 = MIGRATIONS_DIR / "v3_1_2_to_v3_2_0.sql"
MIGRATION_V3_2_1 = MIGRATIONS_DIR / "v3_2_0_to_v3_2_1.sql"
MIGRATION_V3_2_2 = MIGRATIONS_DIR / "v3_2_1_to_v3_2_2.sql"
MIGRATION_V3_2_3 = MIGRATIONS_DIR / "v3_2_2_to_v3_2_3.sql"
MIGRATION_V3_2_4 = MIGRATIONS_DIR / "v3_2_3_to_v3_2_4.sql"


def get_applied_versions(conn: sqlite3.Connection) -> set[str]:
    """schema_migrations 에 등록된 버전 집합 반환."""
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    except sqlite3.OperationalError:
        # schema_migrations 테이블이 아직 없음
        return set()
    return {row[0] for row in rows}


def _apply_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    """SQL 파일을 executescript 로 적용."""
    sql = path.read_text(encoding="utf-8")
    conn.executescript(sql)


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    """DB 를 초기화하고 마이그레이션을 적용한다. DB 파일 경로를 반환.

    재실행 안전: 이미 적용된 버전은 건너뛴다.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # 2. v3.1 베이스 스키마 (멱등)
        _apply_sql_file(conn, SCHEMA_PATH)
        conn.commit()

        applied = get_applied_versions(conn)

        # 3. v3.1 등록 (schema.sql 이 이미 v3.1 구조를 만들었으므로 표시만)
        if "v3.1" not in applied:
            conn.execute(
                "INSERT INTO schema_migrations VALUES ('v3.1', datetime('now'))"
            )
            conn.commit()
            logger.info("[init_db] v3.1 등록")
        else:
            logger.info("[init_db] v3.1 이미 등록됨 — 건너뜀")

        # 4. v3.1.1 델타 (ALTER TABLE 포함 — 미등록일 때만)
        if "v3.1.1" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_1_1)
            conn.commit()
            logger.info("[init_db] v3.1.1 마이그레이션 적용")
        else:
            logger.info("[init_db] v3.1.1 이미 적용됨 — 건너뜀")

        # 5. v3.1.2 델타 (trades 거래소 주문 추적 컬럼 — 미등록일 때만)
        if "v3.1.2" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_1_2)
            conn.commit()
            logger.info("[init_db] v3.1.2 마이그레이션 적용")
        else:
            logger.info("[init_db] v3.1.2 이미 적용됨 — 건너뜀")

        # 6. v3.2.0 델타 (청사진 §6: setup_registry, signal_decisions, agent_reviews,
        #    audit_log + append-only trigger, kill_switch_events — 미등록일 때만)
        if "v3.2.0" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_2_0)
            conn.commit()
            logger.info("[init_db] v3.2.0 마이그레이션 적용 (청사진 §6 데이터 모델)")
        else:
            logger.info("[init_db] v3.2.0 이미 적용됨 — 건너뜀")

        # 7. v3.2.1 델타 (청사진 §3.4.2 + §3.1.2: slack_outbox + mcp_diff_log)
        if "v3.2.1" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_2_1)
            conn.commit()
            logger.info("[init_db] v3.2.1 마이그레이션 적용 (Slack outbox + MCP shadow)")
        else:
            logger.info("[init_db] v3.2.1 이미 적용됨 — 건너뜀")

        # 8. v3.2.2 델타 (M11: Supabase 폐기 후 로컬 ohlcv_local 테이블)
        if "v3.2.2" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_2_2)
            conn.commit()
            logger.info("[init_db] v3.2.2 마이그레이션 적용 (로컬 ohlcv_local)")
        else:
            logger.info("[init_db] v3.2.2 이미 적용됨 — 건너뜀")

        # 9. v3.2.3 델타 (B7 [4-4]: 미체결 신호 forward-return 계측 테이블)
        if "v3.2.3" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_2_3)
            conn.commit()
            logger.info("[init_db] v3.2.3 마이그레이션 적용 (unfilled_signals)")
        else:
            logger.info("[init_db] v3.2.3 이미 적용됨 — 건너뜀")

        # 10. v3.2.4 델타 (Probe: 경계 있는 B-3 계측 프로브 상태 테이블)
        if "v3.2.4" not in applied:
            _apply_sql_file(conn, MIGRATION_V3_2_4)
            conn.commit()
            logger.info("[init_db] v3.2.4 마이그레이션 적용 (probe_state)")
        else:
            logger.info("[init_db] v3.2.4 이미 적용됨 — 건너뜀")

        final = sorted(get_applied_versions(conn))
        logger.info("[init_db] 완료 — 적용 버전: %s", final)
    finally:
        conn.close()

    return db_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = init_db()
    print(f"DB 초기화 완료: {path}")
