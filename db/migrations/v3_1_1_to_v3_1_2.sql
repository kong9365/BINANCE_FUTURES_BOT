-- ============================================================
-- Migration: v3.1.1 → v3.1.2
-- 감사 후속 (실거래 중단급 수정)
--
-- v3.1.2 델타 전부:
--   - trades 에 거래소 주문 추적 컬럼 4개 추가
--       entry_order_id  : 진입 주문의 clientOrderId (멱등/조회)
--       sl_order_id     : 거래소 reduceOnly STOP_MARKET 주문 id
--       tp_order_id     : 거래소 reduceOnly TAKE_PROFIT_MARKET 주문 id
--       trade_status    : 'OPEN' / 'CLOSED' (행 생명주기 명시)
--
-- ALTER TABLE 은 멱등하지 않으므로, init_db.py 가
-- schema_migrations 에 'v3.1.2' 미등록일 때만 실행한다.
-- ============================================================

ALTER TABLE trades ADD COLUMN entry_order_id TEXT;
ALTER TABLE trades ADD COLUMN sl_order_id TEXT;
ALTER TABLE trades ADD COLUMN tp_order_id TEXT;
ALTER TABLE trades ADD COLUMN trade_status TEXT DEFAULT 'OPEN';

-- ---- 마이그레이션 완료 표시 ----
INSERT INTO schema_migrations VALUES ('v3.1.2', datetime('now'));
