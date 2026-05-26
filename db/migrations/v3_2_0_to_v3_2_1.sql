-- ============================================================
-- Migration: v3.2.0 → v3.2.1 (청사진 리팩토링 M5)
--
-- 근거:
--   - SYSTEM_DESIGN_BLUEPRINT.md §3.4.2 (Slack MCP outbox)
--   - SYSTEM_DESIGN_BLUEPRINT.md §3.1.2 (Binance MCP shadow)
--   - REFACTOR_PLAN_v2_BLUEPRINT.md M5
--
-- 신규 2개 테이블 (모두 멱등):
--   1. slack_outbox — Slack/Notion 알림 큐 (fail-soft)
--   2. mcp_diff_log — python-binance vs MCP shadow 비교 결과
-- ============================================================

-- ── M5 Slack/Notion Outbox (운영자 결정 #v1-4: Notion 보류, slack ON) ──
CREATE TABLE IF NOT EXISTS slack_outbox (
    entry_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target                TEXT NOT NULL,                   -- "slack" / "notion" (보류)
    payload_json          TEXT NOT NULL,
    created_at            TIMESTAMP NOT NULL,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    last_retry_at         TIMESTAMP,
    status                TEXT NOT NULL DEFAULT 'pending', -- pending / sent / failed_permanent
    last_error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_slack_outbox_status_target
    ON slack_outbox (status, target);

-- ── M5 Binance MCP shadow diff 로그 ──
CREATE TABLE IF NOT EXISTS mcp_diff_log (
    diff_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                       TIMESTAMP NOT NULL,
    method                   TEXT NOT NULL,                -- "futures_account" 등
    matched                  INTEGER NOT NULL,             -- 1=정합, 0=diff > 1%
    diff_pct                 REAL,
    python_binance_response  TEXT,
    mcp_response             TEXT,
    error                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mcp_diff_log_ts ON mcp_diff_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_diff_log_method ON mcp_diff_log (method);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.1', datetime('now'));
