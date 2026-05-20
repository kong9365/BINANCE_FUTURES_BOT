-- ============================================================
-- Migration: v3.1 → v3.1.1
-- 부록 E-7-4
--
-- v3.1.1 델타 전부:
--   - trades 진입 시점 자본 상태 컬럼 3개
--   - capital_initial (초기 자본 영속화)
--   - capital_daily_snapshot (일일 자본 스냅샷)
--
-- ALTER TABLE 은 멱등하지 않으므로, init_db.py 가
-- schema_migrations 에 'v3.1.1' 미등록일 때만 실행한다.
-- ============================================================

-- ---- E-7-1. trades 테이블에 컬럼 추가 (진입 시점 자본 상태 기록) ----
ALTER TABLE trades ADD COLUMN wallet_balance_at_entry REAL;
ALTER TABLE trades ADD COLUMN available_at_entry REAL;
ALTER TABLE trades ADD COLUMN locked_margin_at_entry REAL;

-- ---- E-7-2. 신규 테이블 — 초기 자본 영속화 (재시작 시 복원) ----
CREATE TABLE IF NOT EXISTS capital_initial (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at            TEXT    NOT NULL,
    initial_wallet_balance REAL    NOT NULL,
    note                   TEXT,                         -- 운영자 메모 (예: "20260514 가동 시작")
    is_active              INTEGER DEFAULT 1              -- 현재 활성 1, 과거 기록 0
);
CREATE INDEX IF NOT EXISTS idx_capital_initial_active ON capital_initial (is_active);

-- ---- E-7-3. 일일 자본 스냅샷 테이블 (운영 추적) ----
CREATE TABLE IF NOT EXISTS capital_daily_snapshot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date     TEXT    UNIQUE NOT NULL,           -- YYYY-MM-DD
    wallet_balance    REAL    NOT NULL,
    available_balance REAL,
    locked_margin     REAL,
    unrealized_pnl    REAL,
    recorded_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capital_daily_date ON capital_daily_snapshot (snapshot_date);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.1.1', datetime('now'));
