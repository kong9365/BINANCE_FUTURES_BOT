-- ============================================================
-- Migration: v3.1.2 → v3.2.0 (청사진 리팩토링 M1)
--
-- 근거:
--   - SYSTEM_DESIGN_BLUEPRINT.md §6 (데이터 모델: SignalDecision, AgentReview, AuditLog, Setup Registry)
--   - SYSTEM_DESIGN_BLUEPRINT.md §7.2 (ALCOA+: append-only, chain hash, params_hash 재현성)
--   - SYSTEM_DESIGN_BLUEPRINT.md §3.2.3 (Single-Position Rotation EV 1위)
--   - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M1 (운영자 권장 7기준 컬럼 포함)
--
-- 신규 6개 테이블 + SQLite trigger (audit_log UPDATE/DELETE 차단).
-- INSERT 는 본 파일 끝에서 1회.
-- 기존 11개 테이블은 0줄 수정 (회귀 0 보장).
--
-- 멱등: 미등록일 때만 실행 (init_db.py 가 schema_migrations 검사).
-- ============================================================

-- ── §6.5 Setup Registry (PARAMS_HASH 기반 재현성, 7기준 컬럼 포함) ──
CREATE TABLE IF NOT EXISTS setup_registry (
    setup_id              TEXT PRIMARY KEY,            -- "1d_tsmom_donchian_long_v1"
    name                  TEXT NOT NULL,
    category              TEXT NOT NULL,                -- "trend" / "mean_rev" / "carry"
    version               TEXT NOT NULL,                -- "v1"
    status                TEXT NOT NULL,                -- "PAPER_ONLY" / "R0_QUALIFIED" / "DISABLED" / "COOLING"
    created_at            TIMESTAMP NOT NULL,
    updated_at            TIMESTAMP NOT NULL,
    params_hash           TEXT NOT NULL,                -- sha256 of params (재현성)
    params_json           TEXT NOT NULL,                -- 직렬화된 params
    academic_ref          TEXT,                          -- "Han·Kang·Ryu 2023 SSRN 4675565"

    -- 운영자 권장 7기준 (M2 백테스트 게이트, REFACTOR_PLAN_v2_BLUEPRINT §추가 운영자 결정)
    last_evaluation_ts    TIMESTAMP,
    last_metric_n                       INTEGER,         -- ≥ 200
    last_metric_pf                      REAL,            -- Net PF ≥ 1.25
    last_metric_expectancy_r            REAL,            -- > 0
    last_metric_avg_win_loss_ratio      REAL,            -- ≥ 1.5
    last_metric_mdd_pct                 REAL,            -- ≤ 25%
    last_metric_single_symbol_max_pct   REAL,            -- < 25%
    last_metric_top3_excluded_pf        REAL,            -- ≥ 1.0 (ZEC 단일종목 행운 방어, 2026-05-22 A2-② 발견)

    -- 보조 메트릭
    last_metric_dsr                     REAL,            -- Deflated Sharpe (DSR)
    last_metric_win_rate                REAL,            -- 추세 전략은 < 0.55 도 OK
    last_metric_response_latency_p95_ms REAL,            -- M6 라이브 측정 (< 5000ms)

    -- 3-Gate 임계 (Phase 1 v3 §5)
    gate1_ic              REAL,                           -- IC > 0.05
    gate2_spread          REAL,                           -- Decile spread
    gate3_pf              REAL,                           -- backtest PF

    reason                TEXT,                           -- 상태 변경 사유
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_setup_registry_status ON setup_registry (status);
CREATE INDEX IF NOT EXISTS idx_setup_registry_category ON setup_registry (category);

-- ── §6.5 Setup Evaluations (평가 이력 — 다중검정 보정용) ──
CREATE TABLE IF NOT EXISTS setup_evaluations (
    eval_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id              TEXT NOT NULL,
    ts                    TIMESTAMP NOT NULL,
    evaluation_type       TEXT NOT NULL,                  -- "backtest" / "paper" / "live_7d"
    params_hash           TEXT NOT NULL,                  -- 평가 시점 params_hash (변경 추적)
    -- 7기준 메트릭 스냅샷
    metric_n              INTEGER,
    metric_pf             REAL,
    metric_expectancy_r   REAL,
    metric_avg_win_loss_ratio REAL,
    metric_mdd_pct        REAL,
    metric_single_symbol_max_pct REAL,
    metric_top3_excluded_pf REAL,
    metric_win_rate       REAL,
    metric_response_latency_p95_ms REAL,
    -- 결과
    passed                INTEGER NOT NULL,               -- 1=PASS, 0=FAIL, -1=CONDITIONAL
    failed_criteria       TEXT,                            -- JSON array of failed criterion names
    full_report_json      TEXT,
    FOREIGN KEY (setup_id) REFERENCES setup_registry(setup_id)
);

CREATE INDEX IF NOT EXISTS idx_setup_evaluations_setup_id ON setup_evaluations (setup_id);
CREATE INDEX IF NOT EXISTS idx_setup_evaluations_ts ON setup_evaluations (ts DESC);

-- ── §6.1 SignalDecision (ALCOA+ 완전 호환) ──
CREATE TABLE IF NOT EXISTS signal_decisions (
    -- Attributable
    signal_id              TEXT PRIMARY KEY,              -- UUID
    setup_id               TEXT NOT NULL,
    params_hash            TEXT NOT NULL,                  -- 재현성 (Consistent)
    claude_session_id      TEXT,                            -- 추적
    signal_source          TEXT NOT NULL,                   -- "strategy_skill" / "manual" / "test"

    -- Legible
    reasoning              TEXT NOT NULL,                   -- 자연어

    -- Contemporaneous
    ts_signal_generated    TIMESTAMP NOT NULL,
    ts_db_recorded         TIMESTAMP NOT NULL DEFAULT (datetime('now')),

    -- Original
    raw_data_hash          TEXT NOT NULL,                   -- sha256 of input candles
    features_snapshot_json TEXT NOT NULL,                   -- 의사결정 시점 features

    -- Decision
    symbol                 TEXT NOT NULL,
    action                 TEXT NOT NULL,                   -- LONG/SHORT/FLAT/EXIT
    confidence             REAL NOT NULL,                   -- 0.0~1.0

    -- Cost Estimate (Phase 1 v3 §7 4-case)
    cost_estimate_json     TEXT,                            -- {case_a, case_b, case_c, case_d}

    -- Single-Position Rotation (§3.2.3)
    ev_estimated           REAL,                            -- 기대값
    rank_in_universe       INTEGER,                         -- 1 = top

    -- Expires
    expires_at             TIMESTAMP NOT NULL,

    -- Status
    status                 TEXT NOT NULL DEFAULT 'GENERATED', -- GENERATED/REVIEWED/APPROVED/REJECTED/EXECUTED/FAILED

    FOREIGN KEY (setup_id) REFERENCES setup_registry(setup_id)
);

CREATE INDEX IF NOT EXISTS idx_signal_decisions_setup_id ON signal_decisions (setup_id);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_ts ON signal_decisions (ts_signal_generated DESC);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_status ON signal_decisions (status);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_symbol ON signal_decisions (symbol);

-- ── §6.3 AgentReview (5-Agent JSON output) ──
CREATE TABLE IF NOT EXISTS agent_reviews (
    review_id              TEXT PRIMARY KEY,                -- UUID
    signal_id              TEXT NOT NULL,
    agent                  TEXT NOT NULL,                   -- strategy_quant/risk/execution/data_backtest/ops_observability
    ts                     TIMESTAMP NOT NULL,
    verdict                TEXT NOT NULL,                   -- Agent별 다름
    full_json              TEXT NOT NULL,                   -- Agent JSON output 전체
    concerns_json          TEXT,                            -- list[str]
    recommendations_json   TEXT,                            -- list[str]
    confidence             TEXT,                            -- LOW/MEDIUM/HIGH
    FOREIGN KEY (signal_id) REFERENCES signal_decisions(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_reviews_signal_id ON agent_reviews (signal_id);
CREATE INDEX IF NOT EXISTS idx_agent_reviews_agent ON agent_reviews (agent);
CREATE INDEX IF NOT EXISTS idx_agent_reviews_ts ON agent_reviews (ts DESC);

-- ── §6.4 AuditLog (chain hash, ALCOA+ Accurate) ──
CREATE TABLE IF NOT EXISTS audit_log (
    log_id                 TEXT PRIMARY KEY,                -- UUID
    ts                     TIMESTAMP NOT NULL,

    event_type             TEXT NOT NULL,                   -- SIGNAL_GENERATED/AGENT_REVIEW/ORDER_PLACED/...

    -- Reference
    related_signal_id      TEXT,
    related_position_id    TEXT,
    related_setup_id       TEXT,

    -- ALCOA+
    actor                  TEXT NOT NULL,                   -- system/agent_<name>/operator/claude_session
    payload_json           TEXT NOT NULL,
    payload_hash           TEXT NOT NULL,                   -- sha256

    -- Linkage (blockchain-like chain)
    previous_log_hash      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_signal_id ON audit_log (related_signal_id);

-- SQLite trigger: UPDATE/DELETE 차단 (ALCOA+ Accurate, append-only)
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (ALCOA+ Accurate, blueprint §7.2)');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only (ALCOA+ Accurate, blueprint §7.2)');
END;

-- ── KillSwitch Events (청사진 §3.4.3) ──
CREATE TABLE IF NOT EXISTS kill_switch_events (
    event_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    activated_at           TIMESTAMP NOT NULL,
    deactivated_at         TIMESTAMP,
    reason                 TEXT NOT NULL,
    source                 TEXT NOT NULL,                   -- manual/ops_agent/system/btc_risk_off/stage3/daily_loss_hard/...
    deactivated_by         TEXT,                            -- operator (수동 해제만)
    payload_json           TEXT
);

CREATE INDEX IF NOT EXISTS idx_kill_switch_activated ON kill_switch_events (activated_at DESC);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.0', datetime('now'));
