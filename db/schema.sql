-- ============================================================
-- Binance Futures Bot — DB Schema (v3.1 base)
-- 명세서 §13-1, §13-2
--
-- 본 파일은 v3.1 현행 베이스 스키마이다.
-- v3.1.1 이후의 변경(trades 신규 컬럼, capital_* 테이블)은
-- db/migrations/v3_1_to_v3_1_1.sql 델타로만 적용한다.
-- 모든 문장은 IF NOT EXISTS 로 멱등(재실행 안전).
-- ============================================================

-- ---- §13-1. 기존 테이블 (v3.0 유지 + v3.1 신규 컬럼) ----

-- 거래 기록
CREATE TABLE IF NOT EXISTS trades (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                 TEXT    NOT NULL,
    symbol                    TEXT    NOT NULL,
    action                    TEXT    NOT NULL,            -- LONG/SHORT
    entry_price               REAL    NOT NULL,
    exit_price                REAL,
    quantity                  REAL    NOT NULL,
    leverage                  INTEGER,
    take_profit               REAL,
    stop_loss                 REAL,
    setup_tag                 TEXT,
    pnl_usd                   REAL,
    pnl_pct                   REAL,
    fees_usd                  REAL,
    duration_seconds          INTEGER,
    exit_reason               TEXT,
    -- v3.1 신규 컬럼
    regime                    TEXT,                        -- TREND_UP/TREND_DOWN/RANGING/UNCERTAIN
    regime_confidence         REAL,
    cost_guard_ev             REAL,                        -- CostGuard 기대값
    slippage_actual_pct       REAL,                        -- 실제 슬리피지 %
    pair_tier                 INTEGER,
    pnl_usd_net               REAL,                        -- 비용 차감 후 순손익
    manual_intervention       INTEGER DEFAULT 0,           -- 운영자 수동 개입 여부
    sizing_kelly_raw          REAL,                        -- DynamicSizer kelly 값
    sizing_pct                REAL                         -- 자본 대비 %
);

CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades (timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_regime ON trades (regime);
CREATE INDEX IF NOT EXISTS idx_trades_setup_tag ON trades (setup_tag);

-- Shadow 모드
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT    NOT NULL,
    strategy_name         TEXT    NOT NULL,
    symbol                TEXT,
    candidate_action      TEXT,
    candidate_setup       TEXT,
    candidate_score       INTEGER,
    entry_price           REAL,
    take_profit           REAL,
    stop_loss             REAL,
    was_taken_real        INTEGER DEFAULT 0,
    blocked_by            TEXT,                          -- v3.1: 차단 모듈 이름
    snapshot_json         TEXT,
    outcome_R             REAL,
    evaluated_at          TEXT
);

-- ---- §13-2. v3.1 신규 테이블 ----

-- 레짐 변경 이력
CREATE TABLE IF NOT EXISTS regime_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    symbol          TEXT    NOT NULL DEFAULT 'BTCUSDT',
    regime          TEXT    NOT NULL,
    prev_regime     TEXT,
    confidence      REAL,
    adx_4h          REAL,
    atr_ratio_1h    REAL,
    atr_ratio_4h    REAL,
    bb_width_pct    REAL,
    ema_slope       REAL,
    funding_rate    REAL,
    reasons         TEXT,                                -- JSON array
    source          TEXT    DEFAULT 'auto'               -- auto/manual/macro
);

CREATE INDEX IF NOT EXISTS idx_regime_history_ts ON regime_history (timestamp DESC);

-- 레짐 후보 (안정성 룰 추적)
CREATE TABLE IF NOT EXISTS regime_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    candidate       TEXT    NOT NULL,
    streak          INTEGER NOT NULL,
    promoted        INTEGER DEFAULT 0                    -- 1이면 confirmed로 승격됨
);

-- 주간 보고서
CREATE TABLE IF NOT EXISTS weekly_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at    TEXT    NOT NULL,
    period_days     INTEGER,
    report_json     TEXT    NOT NULL,
    gpt_insights    TEXT,
    gpt_cost_usd    REAL    DEFAULT 0,
    operator_reviewed_at TEXT,                           -- 운영자 검토 완료 시각
    operator_notes  TEXT                                 -- 운영자 메모
);

-- GPT 호출 로그 (감사 추적)
CREATE TABLE IF NOT EXISTS gpt_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    caller          TEXT    NOT NULL,                    -- weekly/macro/manual 등
    model           TEXT    NOT NULL,
    prompt_hash     TEXT,                                -- 프롬프트 SHA-256
    prompt_text     TEXT,                                -- 전체 프롬프트
    response_text   TEXT,                                -- 전체 응답
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    cost_usd        REAL,
    latency_ms      REAL,
    success         INTEGER,
    error_msg       TEXT
);

CREATE INDEX IF NOT EXISTS idx_gpt_log_ts ON gpt_call_log (timestamp DESC);

-- 시스템 건강 로그
CREATE TABLE IF NOT EXISTS system_health_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    healthy         INTEGER NOT NULL,
    critical        INTEGER DEFAULT 0,
    issues          TEXT,                                -- JSON array
    ws_kline_age_s  REAL,
    ws_user_age_s   REAL,
    rest_latency_ms REAL,
    rest_error_rate REAL,
    time_diff_s     REAL
);

-- 페어 화이트리스트 이력
CREATE TABLE IF NOT EXISTS pair_whitelist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    tier            INTEGER NOT NULL,
    market_cap_usd  REAL,
    volume_24h_usd  REAL,
    avg_funding_7d  REAL,
    blocked_reasons TEXT,                                -- JSON array
    listed_at       TEXT
);

-- 거시 이벤트 캘린더 (캐시용)
CREATE TABLE IF NOT EXISTS macro_events_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    event_time_utc  TEXT    NOT NULL,
    importance      TEXT,                                -- HIGH/MEDIUM/LOW
    description     TEXT,
    affected_pairs  TEXT,                                -- JSON array
    source          TEXT    DEFAULT 'manual',
    added_at        TEXT    DEFAULT CURRENT_TIMESTAMP
);

-- 백테스트 결과 저장
CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    UNIQUE NOT NULL,             -- 예: '20260513_walkforward_v3.1'
    started_at      TEXT    NOT NULL,
    completed_at    TEXT,
    config_json     TEXT,
    summary_json    TEXT,
    verdict         TEXT,                                -- pass/fail/needs_review
    operator_notes  TEXT
);

-- ---- 마이그레이션 추적 (§13-3) ----
-- 테이블 정의만. INSERT 는 init_db.py / 마이그레이션 SQL 이 담당.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
