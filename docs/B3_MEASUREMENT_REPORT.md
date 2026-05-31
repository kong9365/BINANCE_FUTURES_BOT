# Track A — B-3 실행 엣지 계측 결과 보고

> **일자**: 2026-05-31 | **성격**: CODEX 검증 — B-3(post_only 역선택) 통계 계측 *인프라 + 배관 검증*.
> **현재 판정**: **INSUFFICIENT_SAMPLE (실 post_only 이벤트 0)** — 배관(분석기·러너·DB 기록) 검증 완료, *실측은 운영자 테스트넷/소액 라이브 누적 후*.
> **회귀**: 분석기 단위테스트 6 + 전체 회귀 — §6.

---

## 0. 질문 / 판정 규칙

질문: **체결된 post_only 주문이 미체결 주문보다 통계적으로 우월한가(역선택)?**
- 체결(되돌아온 패자) vs 미체결(달아난 승자)의 *방향* forward-return(+15s/+30s/+60s/+5m/+15m) 비교.
- **PASS**: 어느 horizon 이든 미체결 > 체결 차이가 유의(Welch t, p<0.05, 차이>0) = B-3 확정.
- **FAIL**: 유의 차이 전무 → B-3 가설 폐기(재시도 금지).
- **INSUFFICIENT_SAMPLE**: 통계 가능한 표본 부족(현 상태 — 이벤트 0).

★ B-3 는 OHLCV·과거봉·백테스트로 측정 불가 — *실제 post_only 주문*이 필수(라이브/테스트넷).

## 1. 현재 결과

| 항목 | 값 |
|---|---|
| 체결 이벤트 n | 0 |
| 미체결 이벤트 n | 0 |
| 판정 | **INSUFFICIENT_SAMPLE** |

(`docs/measurement_report.json` 동일.) 실 post_only 이벤트가 0 — 운영자 테스트넷/소액 라이브로 누적 후 재실행해야 통계 판정 가능. **배관(코드 경로)은 검증 완료**(§3).

## 2. 구현 (Track A 산출물)

- **`analytics/unfilled_signal_analyzer.py`** (순수 통계):
  - `forward_return_at(prices, order_time, ref_price, side, horizon_sec)`: 주문시각+horizon 시점 방향 수익률(LONG+/SHORT−), 기준=요청 limit.
  - `compare(filled, unfilled)`: sample_size·avg·median(양쪽) + difference(미체결−체결) + **Welch t_stat·p_value·95% CI**. 분산 0 / n<2 면 통계량 None(차이만).
  - `measure(events, fetch_prices)`: 이벤트 → forward-return → horizon별 비교 → 판정. 가격소스 주입(네트워크 격리).
  - `b3_verdict`: PASS/FAIL/INSUFFICIENT_SAMPLE.
- **`scripts/run_b3_measurement.py`**: DB(미체결 unfilled_signals + 체결 trades) 로드 → 공개 `futures_agg_trades`(키 없음·read-only) 가격 → measure → 리포트.
- **이벤트 기록**(기존 Probe P1-P3, 별도 커밋): executor 가 post_only 주문 시 `order_send_ts`·요청 limit(`entry_limit_price`)·체결/미체결을 DB 에 fail-soft 기록(unfilled_signals / trades). = 분석기 입력.

## 3. 배관(end-to-end) 검증 — 완료

1. **단위테스트**: forward_return_at(방향·결측), compare(유의/무/표본부족), b3_verdict, measure(체결 vs 미체결 집계) — 합성 데이터로 6/6 통과. 통계 산식·집계 정확.
2. **빈 DB end-to-end**: `run_b3_measurement.py` 가 이벤트 0 → INSUFFICIENT_SAMPLE 리포트 무오류 산출(크래시 0).
3. **기록 경로**(Probe P3): post_only 체결→trades(entry_limit_price/entry_order_send_ts), 미체결→unfilled_signals(order_send_ts) — 테스트 통과(test_executor P3).

→ 신호 → post_only → 체결/미체결 → DB → 분석기 → 리포트 의 **코드 경로 전부 검증**. 남은 것은 *실제 주문 이벤트*(운영자 수집).

## 4. 운영자 데이터 수집 절차 (CC 미실행 — 실키·실주문 0)

### [A2] 테스트넷 배관 (실키 불필요한 testnet 키)
1. `USE_TESTNET=true` + `PROBE_ENABLED=true` + `ACTIVE_STRATEGY=oi_surge` + testnet 키.
2. 가동 → 실제 GTX post_only 주문 생성 → filled/partial/cancel/timeout 이 trades/unfilled_signals 에 적재.
3. 최소 ~100 이벤트 후: `python scripts/run_b3_measurement.py --db <db>` → end-to-end 무오류 확인.

### [A3] 소액 라이브 (A2 통과 후, 운영자 opt-in)
- `LIVE_TRADING_ENABLED=true` + 실키 / 예산 ≤ $30(`PROBE_BUDGET_USDT`) / 위험 0.5%·고정·연승 무증가(코드 강제, Probe P2).
- 안전장치(전부 HALT·수동 재활성): KillSwitch · 총손실/일일손실/연속손실 · 동시보유 cap · preflight(비보호 0·One-way·예산캡) · 보호종목 무거래 · fail-closed · 계측 try-except 격리 (Probe P1-P3).
- 데이터 충분(체결 ~30 + 미체결 ~30) 또는 N일 → 자동 정지(Probe).
- 종료 후: `python scripts/run_b3_measurement.py --db <live db>` → filled vs unfilled forward-return + p-value 판정.

## 5. 한계
- 통계 판정엔 *실 post_only 이벤트* 필수 — 현재 0(테스트넷/라이브 미수집). 본 보고는 *인프라·배관* 검증.
- 초단위 horizon(+15s/30s/60s)은 aggTrades(초단위) 기준 — 거래소 체결 path. CC 는 실주문·실키 미취급.
- B-3 는 Track B(1d 코어)와 독립 — Track B FAIL 여부와 무관하게 *실행 엣지*만 측정.

## 6. 변경 파일
**소스(신규)**: `analytics/unfilled_signal_analyzer.py` (통계 계측기 — 순수) · `scripts/run_b3_measurement.py` (러너, 공개 aggTrades)
**테스트(신규)**: `tests/test_unfilled_signal_analyzer.py` (forward-return/Welch/measure 6)
**산출물**: `docs/B3_MEASUREMENT_REPORT.md` · `docs/measurement_report.json`(현재 INSUFFICIENT)
**기반(기존 커밋)**: Probe P1-P3 (executor 기록 필드 + 안전장치 + probe_b3 봉-backfill)
