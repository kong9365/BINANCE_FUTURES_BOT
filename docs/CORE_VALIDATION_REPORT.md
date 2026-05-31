# Track B — 1d TSMOM/Donchian 코어 검증 결과 보고

> **일자**: 2026-05-31 | **성격**: CODEX 검증(설계 아님) — 단일·고정 전략, 최적화 0, 오프라인 WF.
> **판정**: **INSUFFICIENT_SAMPLE (n=193 < 200)** — 단 방향 증거는 *gross 음수*(FAIL 방향).
> **규율**: 파라미터/임계/필터 무수정. FAIL 방향이어도 재시도·재실행·튜닝 0. 결과 저장 후 종료.
> **회귀**: pytest 전체(엔진 매크로 게이트 대칭 확장 — Phase C long_only 무영향) — §7.

---

## 0. 판정 (사전확정 게이트, post_only 현실 체결 기준)

| 게이트 | 기준 | 실측 | |
|---|---|---|---|
| 총 OOS 거래 n | ≥ 200 | **193** | ❌ → INSUFFICIENT_SAMPLE |
| 거래당 gross > cost | gross_R > cost_R | **−0.084 ≤ 0.015** | ❌ |
| Net PF | ≥ 1.2 | **0.78** | ❌ |
| OOS Sharpe | ≥ 1.0 | **−0.66** | ❌ |
| 최악 3mo 윈도우 MDD | < 20% | 5.4% | ✅ |
| cross-pair positive | > 55% | **38%** | ❌ |

→ **형식 판정 = INSUFFICIENT_SAMPLE** (n=193<200). 운영자 규칙상 n<200 은 *판정불가*이며 **답은 데이터 확장(코인↑·기간↑·인트라데이 TF)이지 튜닝이 아니다.** 파라미터·임계·필터 손대지 않았다.

## 1. ★ 방향 증거 — gross 엣지 음수 (결론 아님, 보고만)

n<200 이라 *공식* 결론은 불가하나, 193건의 방향은 Phase C 와 동일하게 명확하다:
- **gross_R = −0.084 (비용 *차감 전* 음수).** cost_R=0.015(작음) → **비용이 문제가 아니라 신호 자체가 비용 이전부터 음의 기대값.**
- → B-3(체결 역선택)/비용 스토리가 아니다. 체결을 아무리 잘 줘도(post_only≈maker) gross 엣지가 양수로 돌아서지 않는다.
- 함의: 데이터를 확장해 n≥200 을 채워도 gross 음수면 결론은 "엣지 없음"일 가능성이 높다. 단 규칙대로 *지금* FAIL 로 단정하지 않는다(형식상 INSUFFICIENT_SAMPLE).

## 2. 메트릭 (post_only 판정 / maker_open 비교)

```
mode          n   win%    PF  grossR  costR    netR  w3moMDD  Sharpe  xpair+
post_only   193  39.4%  0.78  -0.084  0.015  -0.098    5.4%   -0.66     38%
maker_open  193  38.9%  0.76  -0.084  0.024  -0.107    5.5%   -0.72     38%
```
- **post_only ≈ maker_open**: gross_R 동일(−0.084), cost_R 만 미세 차이(0.015 vs 0.024). → **1d 에선 체결모델이 판정을 가르지 않는다(B-6 결론 재확인).** maker_open 은 비교 전용(최종 판정 아님).
- avg_trade = **−0.098R** (거래당 평균 음수).

## 3. 방향 분리 (롱+숏 대칭 — 둘 다 음수)

| 방향 | n | gross_R | net_R | 승률 |
|---|---|---|---|---|
| LONG | 151 | **−0.073** | −0.088 | 39.7% |
| SHORT | 42 | **−0.124** | −0.136 | 38.1% |
| LONG+SHORT | 193 | −0.084 | −0.098 | 39.4% |

- **롱·숏 모두 gross 음수.** SHORT(−0.124)이 LONG(−0.073)보다 더 나쁨 — 대칭 BTC_RISK_OFF 게이트(숏=BTC<200EMA)에도 숏측 엣지 부재. "양방향 표본 2배"가 엣지를 만들지 못함.

## 4. 코인별·윈도우별 분포

- **코인별(상위8)**: BCH 17 / TRX 16 / UNI 13 / FIL 13 / SOL 12 / ADA 12 / BNB 11 / AVAX 10 — 분산(단일 종목 의존 없음). **cross-pair positive 38%**(>55% 미달 — 다수 코인이 음수).
- **연도별 (n, 평균 net_R)**: 2023 (48, −0.199) / 2024 (65, −0.199) / 2025 (62, −0.020) / 2026 (18, **+0.265**). → 2023-2024 일관 음수, 2026 양수지만 n=18 소표본(과대해석 금지).

## 5. 전략·스펙 (고정, 무수정)

| 항목 | 값 |
|---|---|
| 진입 | Donchian20 돌파 + EMA200 편향 + 대칭 BTC_RISK_OFF 게이트 (LONG=BTC>200EMA / SHORT=BTC≤200EMA). ADX 게이트 없음(스펙) |
| 청산 | Chandelier 3.0×ATR 트레일 (고정 TP·부분익절 없음 — 우측꼬리 보존). 초기 stop 2.0×ATR |
| 유니버스 | mid-liq 직전30봉 거래대금 ≥$20M + 보호종목 제외 |
| 사이징 | 코인당 risk 0.5% (notional≠risk), 동시보유 ≤5 |
| 비용 | post_only(maker 진입·무진입슬립) + 시장가 청산(taker+슬리피지) + 펀딩 |
| OOS | 단일 실행(워밍업 12mo 포함) → 2023-05-27 이후 partition. 룩어헤드 0(엔진 _get_candles_until <ts) |

## 6. 한계

- **1d 테스트 — B-3(post_only 15초 역선택) 해소 아님**(인트라데이 현상, §2 체결모델 수렴이 그 증거). B-3 실측은 Track A(라이브).
- Chandelier 트레일은 **봉 t 고/저(≤t)만** 사용(미래 봉 미참조). 단 *봉 내* 고→저 순서를 가정(고가로 트레일 ratchet 후 저가 체크)하는 기존 엔진 컨벤션 — 미세 낙관 편향 가능(FAIL 방향 결론엔 무영향).
- n=193<200 = 형식상 판정불가. 데이터 확장(코인↑·기간↑·인트라데이)이 답이며 *튜닝 아님.*

## 7. 사전확률 vs 결과

- **사전 기대 = FAIL** (Phase C 돌파 gross −0.242 + daily_tsmom PF<1). 
- **결과**: gross_R −0.084 (음수) — FAIL 방향 확인. 형식상 n=193<200 → INSUFFICIENT_SAMPLE. 목적은 성공이 아니라 정직한 판정 — 달성.

## 8. 변경 파일

**소스(수정)**: `backtesting/backtest_engine.py` (`_evaluate_breakout` 매크로 게이트 대칭 확장 — LONG=risk-on/SHORT=risk-off. long_only=True 인 Phase C/기존 테스트엔 무영향)
**소스(신규)**: `analytics/core_strategy_validation.py` (OOS 메트릭 + 사전확정 게이트, 순수) · `scripts/run_core_validation.py` (러너 + JSON 산출)
**테스트(신규)**: `tests/test_core_validation.py` (게이트/메트릭/방향분리/OOS partition)
**산출물**: `docs/CORE_VALIDATION_REPORT.md` · `docs/core_validation_report.json`
