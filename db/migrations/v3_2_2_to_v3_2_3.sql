-- ============================================================
-- Migration: v3.2.2 → v3.2.3 (FINAL_REVIEW B7 [4-4])
--
-- 근거:
--   - FINAL_REVIEW_BINANCE_FUTURES_BOT.md §4-4 (미체결 신호 forward-return 계측)
--   - 운영자 승인 (2026-05-30): Phase B-7 — 가장 비싼 데이터(post-only 미체결)
--     를 폐기하지 말고 영속화. B-3(백테스트-라이브 체결 정합) 결정 증거용.
--
-- 신규 1개 테이블:
--   - unfilled_signals: 진입 미체결/스킵 신호 메타 + forward-return 채움 컬럼.
--     (분석 리포트는 범위 외 — 스키마/적재 훅만 제공.)
-- ============================================================

CREATE TABLE IF NOT EXISTS unfilled_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TIMESTAMP NOT NULL,        -- 미체결/스킵 발생 시각 (UTC ISO8601)
    symbol             TEXT NOT NULL,
    action             TEXT NOT NULL,             -- LONG / SHORT
    setup_tag          TEXT,                      -- 신호 출처 셋업
    signal_price       REAL,                      -- 신호가(진입 시도가)
    reason             TEXT,                      -- fill_timeout / partial_fill_timeout /
                                                  -- fill_timeout_no_position 등 (스킵 사유)
    -- ── forward-return 분석용 (나중에 분석 훅이 채움 — 적재 시엔 NULL) ──
    fwd_return_1bar    REAL,                      -- 신호 +1 bar 수익률
    fwd_return_3bar    REAL,                      -- 신호 +3 bar 수익률
    fwd_return_6bar    REAL,                      -- 신호 +6 bar 수익률 (역선택 신호)
    fwd_filled_at      TIMESTAMP                  -- forward-return 계산 완료 시각
);

CREATE INDEX IF NOT EXISTS idx_unfilled_signals_ts ON unfilled_signals (ts DESC);
CREATE INDEX IF NOT EXISTS idx_unfilled_signals_symbol ON unfilled_signals (symbol, ts DESC);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.3', datetime('now'));
