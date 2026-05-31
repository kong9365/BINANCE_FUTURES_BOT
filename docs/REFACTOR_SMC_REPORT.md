# SMC 전략 후보 — 일봉 구조 SMC OOS 정직 판정

> **일자**: 2026-05-31 | **성격**: SMC *후보 탐색* (백테스트/검증 전용, **실거래 아님 — HOLD 유지**)
> **규율**: 재량 0(전부 수식) · 룩어헤드 0(확정지연 단위테스트) · 단일 사전확정 셋업 · 단일 실행 · 스윕/재시도 금지 · B-6 post_only 현실 체결.
> **판정**: **엣지미달(결론)** — n=285≥200(표본충분 → 판정불가 아님). gross_R=−0.151(비용 *차감 전* 음수). PF 0.73, Sharpe −0.99.
> **회귀**: pytest(SMC 단위 14 + 전체) — §7. git: CLAUDE.md 신정책.

---

## 0. 판정 (사전확정 게이트 — post_only 현실 체결 기준, 무수정)

| 게이트 | 기준 | post_only 실측 | |
|---|---|---|---|
| 총 OOS 거래수 | ≥ 200 | **285** | ✅ (표본충분) |
| OOS Sharpe | ≥ 1.0 | **−0.99** | ❌ |
| Net PF | ≥ 1.2 | **0.73** | ❌ |
| 최악 3mo OOS MDD | < 20% | 10.3% | ✅ |
| 거래당 gross > 비용 | gross_R > cost_R | **−0.151 ≤ 0.057** | ❌ |

→ **공식 판정 = 엣지미달(결론).** n=285≥200 이므로 *표본부족(판정불가)이 아니라 결론*이다. 운영자 사전확정 규칙상 "n≥200 & PF<1.2 = 엣지미달(결론)". 파라미터는 손대지 않았다.

## 1. ★ 정직한 핵심 — gross 엣지 음수 (비용/체결 스토리 아님)

- **gross_R = −0.151 (비용 *차감 전* 음수).** cost_R=0.057 은 작다 → **비용이 문제가 아니라 신호 자체가 비용 이전에 음의 기대값**(Phase C 돌파와 같은 종류의 결론).
- **maker_open 비교**: gross_R −0.150(post_only −0.151과 동일), cost_R 만 0.093 vs 0.057 차이. → **1d 에선 체결모델이 판정을 가르지 않는다(B-6 재확인).** B-3(15초 역선택)는 어떤 봉 TF 로도 미해소.
- → 체결을 아무리 잘 줘도(post_only≈maker) SMC 셋업 B 에 양의 gross 엣지가 없다.

## 2. ★ R:R 은 건강 — 문제는 *진입 적중률* (구조 청산은 정상)

운영자 질문("구조 TP 가 R:R<1 로 깔리는지")에 대한 답:

- **계획 R:R 중앙값 2.77, R:R<1 비율 0.0%.** 구조기반 청산(SL=sweep 극값∓0.1ATR, TP=반대측 유동성)은 **R:R 을 깔지 않는다 — 오히려 건강**(avg_win 2.54R / avg_loss −1.05R).
- 그러나 **승률 23.5% < 손익분기 26.5%**(R:R 2.77 의 손익분기 = 1/(1+2.77)). → **SMC 진입(구조+sweep+POI)이 방향을 손익분기만큼도 못 맞춘다.** 청산이 아니라 *진입 엣지*의 부재가 결론.

## 3. ★ 롱·숏 분리 — 둘 다 음수 ("롱만 상승베타"도 아님)

| 방향 | n | gross_R | net_R | 승률 |
|---|---|---|---|---|
| LONG | 163 | **−0.029** | −0.081 | 26.4% |
| SHORT | 122 | **−0.316** | −0.380 | 19.7% |

- LONG 은 gross near-zero(−0.029)지만 *양수 아님* → 상승장 베타조차 못 잡음.
- SHORT 은 강한 음수(−0.316, 승률 19.7%) → 하락 sweep 반전 셋업이 특히 출혈. 대칭 적용이 숏에서 역효과.
- → 한쪽도 +EV 아님. 방향 게이팅으로 구제 불가.

## 4. 코인별·윈도우별 분포 (단일 의존 아님)

- **코인별(상위8)**: UNI 18 / XRP 18 / SOL 16 / DOGE 15 / ADA 15 / BNB 15 / WLD 14 / ONDO 14 — 최다도 285 중 6.3%. **단일 심볼 의존 없음.**
- **연도별 (n, 평균 net_R)**: 2023 (42, **+0.009**) / 2024 (68, **−0.297**) / 2025 (119, **−0.243**) / 2026 (56, **−0.192**). → 2023 손익분기, 이후 일관 음수. **단일 구간 artifact 아님 — 광범위 음수.**

## 5. 4-모드 비교 (B-6 — 1d 체결모델 수렴 재확인)

```
mode          n    win%    PF   grossR  costR    netR   w3moMDD  Sharpe
post_only    285  23.5%  0.73  -0.151  0.057  -0.209   10.3%   -0.99
maker_open   285  23.5%  0.70  -0.150  0.093  -0.243   11.0%   -1.15
```
post_only ≈ maker_open: gross 동일(−0.15), cost 만 차이. 판정은 post_only(라이브 일치). 1d 에선 체결모델이 엣지를 만들지 못함.

## 6. 스펙·구현 (운영자 확정 2026-05-31, 무수정)

| 항목 | 구현 (객관 수식, 룩어헤드 0) |
|---|---|
| swing | fractal N=2(5봉), high[i]>high[i±1..2]. **i+2 이후에만 사용**(확정지연) |
| BOS/CHOCH | 상태기계: close[t] 가 직전 확정 swing 돌파. trend 유지=BOS, 첫 반대=CHOCH |
| FVG | bull low[i]>high[i−2], zone=[high[i−2],low[i]], 최소갭 ≥ 0.25·ATR14, i봉 확정 |
| sweep | bull low[t]<확정 SL AND close[t]>SL (대칭) — 직전 K=3봉 |
| OB | 직전 동방향 BOS 직전 마지막 반대색 캔들 zone=[low,high] |
| 진입(셋업 B) | trend==방향 + 직전3봉 sweep + (FVG OR OB) zone 복귀 (discount 미사용·계산만) |
| 청산(X) | SL=sweep 극값 ∓0.1·ATR, TP=활성범위 반대측 SH/SL. `_simulate_trade` 고정 sl/tp(트레일 off) |
| 사이징/유니버스 | 코인당 0.5% risk·동시≤5 (A1), mid-liq ≥$20M·보호종목 제외 (Phase C) |
| 비용 | post_only(maker, 진입슬립0) + 스톱/구조 청산(taker+tier 슬리피지) + 펀딩 |
| OOS | 전체 1회(워밍업 포함) → IS 12mo(2023-05-27) 이후 partition. 룩어헤드 엔진 차단 |

## 7. 한계 (반드시)

- **1d "일봉 구조 SMC" — 인트라데이 ICT 가 아니다.** 5m/15m SMC 검정은 별도(15m 데이터는 백필 완료, 이번 실행은 운영자 확정대로 1d).
- **B-3(post_only 15초 역선택) 미해소** — 어떤 봉 TF(5m=300초)도 15초 ≪ 라 봉 granularity 로 분해 불가(B-6 결론). 정밀 체결은 B-8(testnet 실체결).
- 결론은 "이 객관 SMC 셋업 B 가 1d 에서 음의 gross 엣지"라는 사실 보고. 수익성·라이브 해석 금지.

## 8. 다음 인계

- 방향성 돌파(Phase C, gross −0.242)·델타중립 캐리(Phase D-2, net 음수)·SMC(gross −0.151) **모두 +EV 미확인** → **실거래 HOLD 유지**가 정직한 검증 인프라의 일관된 결론.
- 원하면 별도 승인 하에 **인트라데이 15m SMC**(데이터 보유) 재판정 — 단 gross 음수 prior 고려, 튜닝 금지.
- B-8(testnet) — B-3 실체결 + unfilled_signals forward-return.

## 9. 변경 파일

**소스(신규)**: `strategy/smc.py` (SMC 프리미티브 순수함수 — swing/BOS·CHOCH/FVG/sweep/OB/premium-discount, 룩어헤드 0)
**소스(수정)**: `backtesting/backtest_engine.py` (BacktestConfig smc 6필드 + smc_cfg + `_evaluate_smc` + dispatch)
**소스(신규)**: `scripts/run_smc_wf.py` (OOS partition 러너 + 롱/숏 분리 + 실현 R:R + maker↔post_only + 사전게이트)
**소스(수정)**: `scripts/backfill_intraday.py` (15m 인트라데이 백필 — 인트라데이 SMC 대비, 공개·계좌0)
**테스트(신규)**: `tests/test_smc.py` (프리미티브 + 확정지연/prefix-안정 룩어헤드 + 롱/숏 + 엔진 스모크 14)
**문서(신규)**: `docs/REFACTOR_SMC_REPORT.md`
**커밋 제외(의도)**: `backtests/cache/intraday/*.parquet` (수집 데이터 — 커밋 금지 경로)
