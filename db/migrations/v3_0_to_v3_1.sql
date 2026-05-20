-- ============================================================
-- Migration: v3.0 → v3.1
-- 명세서 §13-3
--
-- 참조용 마이그레이션. 기존 v3.0 DB 를 보유한 사용자가
-- v3.1 로 업그레이드할 때 사용한다.
-- 신규 사용자는 db/schema.sql 이 이미 v3.1 베이스이므로
-- 이 파일을 실행할 필요가 없다 (init_db.py 가 v3.1 을 직접 등록).
-- ============================================================

-- ---- trades 컬럼 추가 (v3.1 신규) ----
ALTER TABLE trades ADD COLUMN regime TEXT;
ALTER TABLE trades ADD COLUMN regime_confidence REAL;
ALTER TABLE trades ADD COLUMN cost_guard_ev REAL;
ALTER TABLE trades ADD COLUMN slippage_actual_pct REAL;
ALTER TABLE trades ADD COLUMN pair_tier INTEGER;
ALTER TABLE trades ADD COLUMN pnl_usd_net REAL;
ALTER TABLE trades ADD COLUMN manual_intervention INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN sizing_kelly_raw REAL;
ALTER TABLE trades ADD COLUMN sizing_pct REAL;

ALTER TABLE shadow_decisions ADD COLUMN blocked_by TEXT;

-- ---- §13-2 신규 테이블 생성 ----
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
    reasons         TEXT,
    source          TEXT    DEFAULT 'auto'
);
CREATE INDEX IF NOT EXISTS idx_regime_history_ts ON regime_history (timestamp DESC);

CREATE TABLE IF NOT EXISTS regime_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    candidate       TEXT    NOT NULL,
    streak          INTEGER NOT NULL,
    promoted        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS weekly_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at    TEXT    NOT NULL,
    period_days     INTEGER,
    report_json     TEXT    NOT NULL,
    gpt_insights    TEXT,
    gpt_cost_usd    REAL    DEFAULT 0,
    operator_reviewed_at TEXT,
    operator_notes  TEXT
);

CREATE TABLE IF NOT EXISTS gpt_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    caller          TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    prompt_hash     TEXT,
    prompt_text     TEXT,
    response_text   TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    cost_usd        REAL,
    latency_ms      REAL,
    success         INTEGER,
    error_msg       TEXT
);
CREATE INDEX IF NOT EXISTS idx_gpt_log_ts ON gpt_call_log (timestamp DESC);

CREATE TABLE IF NOT EXISTS system_health_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    healthy         INTEGER NOT NULL,
    critical        INTEGER DEFAULT 0,
    issues          TEXT,
    ws_kline_age_s  REAL,
    ws_user_age_s   REAL,
    rest_latency_ms REAL,
    rest_error_rate REAL,
    time_diff_s     REAL
);

CREATE TABLE IF NOT EXISTS pair_whitelist_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    tier            INTEGER NOT NULL,
    market_cap_usd  REAL,
    volume_24h_usd  REAL,
    avg_funding_7d  REAL,
    blocked_reasons TEXT,
    listed_at       TEXT
);

CREATE TABLE IF NOT EXISTS macro_events_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    event_time_utc  TEXT    NOT NULL,
    importance      TEXT,
    description     TEXT,
    affected_pairs  TEXT,
    source          TEXT    DEFAULT 'manual',
    added_at        TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    UNIQUE NOT NULL,
    started_at      TEXT    NOT NULL,
    completed_at    TEXT,
    config_json     TEXT,
    summary_json    TEXT,
    verdict         TEXT,
    operator_notes  TEXT
);

-- ---- 마이그레이션 완료 표시 ----
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
INSERT INTO schema_migrations VALUES ('v3.1', datetime('now'));
