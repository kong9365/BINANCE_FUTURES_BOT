-- ============================================================
-- Migration: v3.2.4 → v3.2.5 (Probe B-3 계측 — 체결↔미체결 동일기준 비교 필드)
--
-- 근거:
--   - 운영자 승인 (2026-05-31): B-3(post_only 15초 역선택) 실측. 체결(되돌아온 패자)
--     vs 미체결(달아난 승자)을 *동일 기준가(요청 limit)* 대비 forward-return 으로 비교.
--
-- 추가 컬럼(additive — 기존 데이터 무영향):
--   - trades.entry_limit_price    : 요청한 GTX post-only limit 가(=신호가). 체결가(entry_price)
--                                   와의 차 = fill 슬리피지. 미체결 signal_price 와 동일 기준.
--   - trades.entry_order_send_ts  : 주문 전송 시각(UTC ISO, 근사 — leverage 설정 직전).
--   - unfilled_signals.order_send_ts : 미체결 신호의 주문 전송 시각.
-- (unfilled fwd_return_1/3/6bar·fwd_filled_at 는 v3.2.3 에서 이미 존재 — backfill 이 채움.)
-- ============================================================

ALTER TABLE trades ADD COLUMN entry_limit_price REAL;
ALTER TABLE trades ADD COLUMN entry_order_send_ts TEXT;
ALTER TABLE unfilled_signals ADD COLUMN order_send_ts TEXT;

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.2.5', datetime('now'));
