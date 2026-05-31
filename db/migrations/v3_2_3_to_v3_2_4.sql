-- ============================================================
-- Migration: v3.2.3 → v3.2.4 (Probe — 경계 있는 B-3 계측 프로브 상태)
--
-- 근거:
--   - 운영자 승인 (2026-05-31): 소액 라이브 B-3 계측 프로브. post_only 15초
--     역선택을 실체결로 측정 + 라이브 경험. 검증 엣지 배포 아님(소액=수업료).
--
-- 신규 1개 테이블:
--   - probe_state: 단일행(id=1). 프로브 *시작 시각* + 사전확정 파라미터(예산/데이터
--     목표/기간)를 영속. 재시작 후에도 N주 자동정지·데이터 카운트 스코프(ts≥start)
--     기준을 잃지 않게 한다. 첫 probe-enabled iteration 에서 INSERT OR IGNORE.
-- ============================================================

CREATE TABLE IF NOT EXISTS probe_state (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),  -- 단일행만 허용
    probe_start_at       TIMESTAMP NOT NULL,                  -- 첫 probe-enabled iteration (UTC ISO8601)
    initial_capital_usdt REAL NOT NULL,                       -- 프로브 시작 자본(총손실 기준, 재시작 불변)
    budget_usdt          REAL NOT NULL,                       -- 사전확정 예산 cap
    n_fill_target        INTEGER NOT NULL,                    -- 체결 데이터 목표
    n_unfill_target      INTEGER NOT NULL,                    -- 미체결 데이터 목표
    max_weeks            INTEGER NOT NULL,                    -- 기간 자동정지(주)
    created_at           TIMESTAMP NOT NULL
);

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.4', datetime('now'));
