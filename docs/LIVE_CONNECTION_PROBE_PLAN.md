# 소액 실거래 연결 검증 계획서 (Live Connection Probe Plan)

> ⚠️ **본 문서는 "소액 실거래 연결 검증" 계획서입니다.**
> - 전략 수익성 검증 완료가 **아닙니다**.
> - 큰 금액 실거래 가능 근거가 **아닙니다**.
> - **실거래 GO는 HOLD 유지**. 본 계획의 `USE_TESTNET=false` 실행은 **운영자 최종 승인 전 금지**입니다.
> - 목적은 수익이 아니라 **실거래 환경에서의 연결·주문 생애주기 정상성** 확인입니다.

## 보호종목 (6개 — 봇이 절대 거래하지 않음)
`BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT`

- 출처: `config/settings.py` `_DEFAULT_PROTECTED_SYMBOLS` (환경변수 `PROTECTED_SYMBOLS` 우선, 빈/공백/콤마만이면 위 6개로 폴백).
- **INJUSDT는 보호종목이므로 절대 거래 대상이 아닙니다.** 실거래 연결 검증의 거래 종목으로도 사용 금지.
- 검증 거래 종목은 **비보호 종목만**(예: SOLUSDT / XRPUSDT / TRXUSDT 중 거래 가능 종목).

---

## 1. 목적
실거래(`USE_TESTNET=false`) 환경에서 극소액으로 다음만 확인한다 (수익/전략 검증 아님):
1. live API 키 연결
2. live DB 분리(`data/bot_live.db`)
3. One-way Mode 확인
4. 보호종목 6개 차단
5. GTX 주문 전송
6. FILLED 확인
7. algo STOP/TP 등록
8. Telegram 알림
9. DB OPEN/CLOSED 기록
10. 포지션/보호주문 잔여 0 확인

## 2. 실행 전 조건 (전부 충족 시에만 실행)
- [ ] git `status` clean / `log --oneline -3` 최신 커밋 확인
- [ ] `python -m compileall .` 에러 0
- [ ] `pytest tests/` 전체 통과
- [ ] **`USE_TESTNET=false`** (`.env` 명시)
- [ ] `DB_PATH` 미지정 → `data/bot_live.db` 자동 사용, `data/bot.db`(testnet)와 분리 확인
- [ ] live API 키 권한: **Futures only / 출금 비활성 / Spot 비활성 / Margin 비활성 / Universal Transfer 비활성**
- [ ] Binance 계정 **One-way Mode** (`dualSidePosition=false`)
- [ ] **Futures 지갑 100~300 USDT만** 입금
- [ ] 보호종목 6개 확인 (BTC/ETH/HOLO/CFX/LYN/INJ)
- [ ] Telegram 알림 정상 / httpx·httpcore 토큰 URL 로그 0건 (억제 적용)
- [ ] 게이트 무완화 (RiskManager / CostGuard / PairWhitelist / CapitalManager 원본)

## 3. 실행 제한 (강제)
1. 큰 금액 금지
2. Futures 지갑 100~300 USDT만
3. 첫 실행 30~60분
4. 동시 포지션 1개
5. 보호종목 6개 유지 (BTC/ETH/HOLO/CFX/LYN/INJ)
6. RiskManager 완화 금지
7. CostGuard 완화 금지
8. PairWhitelist 완화 금지
9. 레버리지 상향 금지
10. Hedge Mode 금지, One-way 유지
11. 출금 권한 없는 키만
12. Spot/Margin 권한 없는 키만
13. Universal Transfer 권한 없는 키만
14. 실행 중 Binance 화면 직접 감시

## 4. 실행 명령 (운영자 최종 승인 후에만)
```bash
# 30분 검증
USE_TESTNET=false python main_7590.py --duration 1800
# 60분 검증
USE_TESTNET=false python main_7590.py --duration 3600
```
> 자연 진입은 OIScanner 신호 희소로 발생 가능성 낮음. 1차 목적은 연결·차단·DB 분리·시작 시퀀스·보호종목 알림 검증. 생애주기 메커니즘은 Testnet 통제 프로브에서 이미 입증됨.

## 5. 실시간 감시 항목 (운영자 직접)
- Binance Futures UI: Positions / Open Orders / **Open Algo Orders**
- 콘솔 로그: 시작 시퀀스 / 보호종목 6개 알림 / 스캔·차단 사유 / ERROR·CRITICAL
- Telegram: 가동·진입·청산·critical 수신
- DB: `data/bot_live.db` 생성·기록 (testnet `data/bot.db` 미기록)

## 6. 즉시 중단 조건 (하나라도 발생 → Ctrl+C + 거래소 수동 정리)
1. STOP 미등록
2. TP 미등록
3. DB OPEN인데 거래소 포지션 없음
4. 거래소 포지션인데 DB OPEN 없음
5. Open Algo Orders 이상 잔여
6. **보호종목 6개 중 하나라도 진입 시도**
7. Hedge 관련 critical
8. `position_mode_check_failed`
9. `orphan_position_force_closed`
10. Telegram 알림 미수신
11. `bot_live.db`가 아닌 DB에 기록
12. API 키/토큰 로그 노출
13. ERROR 또는 CRITICAL 발생

## 7. 실행 후 확인 항목
- `bot_live.db`: trades OPEN/CLOSED 수, `sl_order_id`/`tp_order_id`(algoId), `exit_price`/`pnl_usd_net`/`duration_seconds`, `trade_status`
- 거래소: Open Algo Orders 잔여 0, 포지션 잔여 0
- 보호종목 6개 진입 시도 0건
- Telegram 진입/청산/critical 정상
- `data/bot.db`(testnet)와 혼동 없음, `bot_live.db` 정상 생성
- 토큰 URL 로그 0건

## 8. 실거래 후 판정 기준
- **통과**: 연결·DB 분리·보호종목 6개 차단·(진입 시) 생애주기 완결·잔여 0·알림 정상·ERROR 0
- **부분 통과**: 진입 미발생이나 연결/차단/분리/알림 정상 (생애주기는 Testnet 프로브로 보강 인정)
- **실패**: §6 중단 조건 발생 또는 DB↔거래소 불일치
- **즉시 중단**: §6 해당

## 9. 다음 단계 (판정별)
- 통과/부분통과 → 추가 소액 관찰(여러 회) → 그 후에만 금액 단계적 상향 검토
- 24시간 소액/Shadow 운영으로 자연 STOP/TP 트리거 → reconcile 1건 실관측 권장
- 전략 수익성은 별도 백테스트/페이퍼에서 검증 (본 계획 범위 아님)
- 이상 발견 시 코드 수정 후 재검증

---

## 부록 — 검증 이력 (배경)
- Testnet 통제 생애주기 프로브(XRPUSDT, $30, 2x): GTX→FILLED→algo STOP/TP 등록→DB OPEN→ExitPlan→close_position→보호주문 cancel→DB CLOSED(`pnl_usd_net`/`duration_seconds` 기록)→잔여 0 **전 구간 검증 완료**.
- Testnet 1시간 자연 운영: 무오류·무누출, 단 OIScanner 후보 0(데이터 희소)으로 자연 진입 미발생.
- 미관측(잔여): 자연 OIScanner 신호 진입, 거래소 STOP/TP 자연 트리거 → `reconcile_closed_positions` 경로(단위테스트 커버).

**최종: 실거래 GO는 HOLD. 본 계획 실행은 운영자 최종 승인 + 직접 감시 하에서만.**
