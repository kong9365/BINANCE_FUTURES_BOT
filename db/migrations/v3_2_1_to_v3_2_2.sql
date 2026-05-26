-- ============================================================
-- Migration: v3.2.1 → v3.2.2 (청사진 리팩토링 M11)
--
-- 근거:
--   - 운영자 결정 (2026-05-27): Supabase 폐기 → 로컬 ohlcv 저장
--   - REFACTOR_PLAN_v2_BLUEPRINT.md M11
--
-- 신규 1개 테이블:
--   - ohlcv_local: 로컬 sqlite 캔들 저장소 (백테스트 / 백필 용도)
-- ============================================================

CREATE TABLE IF NOT EXISTS ohlcv_local (
    symbol             TEXT NOT NULL,
    interval           TEXT NOT NULL,                 -- "1m" / "5m" / "1h" / "1d"
    ts                 TIMESTAMP NOT NULL,            -- UTC ISO8601 (캔들 open time)
    open               REAL NOT NULL,
    high               REAL NOT NULL,
    low                REAL NOT NULL,
    close              REAL NOT NULL,
    volume             REAL NOT NULL,
    quote_volume       REAL,
    trades             INTEGER,
    taker_buy_base     REAL,                          -- CVD proxy
    taker_buy_quote    REAL,
    closed             INTEGER DEFAULT 1,             -- 1 = 마감 (룩어헤드 차단)
    PRIMARY KEY (symbol, interval, ts)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_local_symbol_interval ON ohlcv_local (symbol, interval, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_local_ts ON ohlcv_local (ts DESC);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.2', datetime('now'));
