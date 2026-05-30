# REFACTOR B-6 — 백테스트 진입 체결 모델 (B-3 정합 시도)

> **일자**: 2026-05-30 | **근거**: FINAL_REVIEW §8 (B-3 / D-1), §5 로드맵 Phase B
> **목표**: 백테스트가 라이브와 "같은 게임"을 하도록 — post-only 체결을 모델링해 B-3(체결 역선택)를 해소/측정.
> **결과**: 인프라 구축 완료 + **A/B 실측 — B-3는 봉 granularity 로는 측정 불가(상·하한 모두 78~100% 체결, 엣지 거의 불변). 정밀 측정은 B-8(testnet 실체결) 필요.**
> **회귀**: pytest 1065 passed (회귀 0). git: CLAUDE.md 신정책(가드 준수)대로.

---

## 0. 핵심 결론 (정직하게)

1. **infra 완성**: `BacktestConfig.entry_fill_model` (config-driven) — `post_only`(기본·라이브 일치) / `post_only_strict`(보수 하한) / `taker` / `maker_open`(legacy 비교). 모든 모드 단위테스트 + 회귀 통과.
2. **B-3 미확정 (봉 granularity 한계)**: 동일 신호셋 A/B 에서 post_only 체결률이 **상한 100% / 하한 78%** 로, 라이브 15초 타임아웃이 함의하는 "모멘텀 미체결 급감"이 **나타나지 않음**. 엣지(PF/expR)도 모델 간 거의 불변. → 백테스트 봉(1d/1h)으로는 15초 post-only 역선택을 *분해할 수 없다*(아래 §3).
3. **부수(더 중요)**: 모든 체결 모델에서 PF < 1.0 — 즉 **체결 모델과 무관하게 이 데이터에서 양의 엣지 없음.** B-3 여부 이전에 엣지 자체가 부재 → HOLD 결론 강화.
4. **다음**: B-3 의 *정밀* 측정은 B-8(testnet 실체결)에서만 가능. B-7 `unfilled_signals`(+forward-return)가 그 계측 scaffold.

---

## 1. 검증된 불일치 (대조 — executor ↔ backtest_engine)

| 측면 | 라이브 (`executor.py`) | 백테스트(기존, `backtest_engine.py`) |
|---|---|---|
| 진입 | GTX **post-only LIMIT** at 신호가, 15초 미체결 시 스킵 | `entry_price=df.at[ts,"open"]` **무조건 확정 체결** |
| 체결률 | <100% | **100%** |
| 수수료 | maker | maker(진입)+taker(청산)+2×slip ✓ (수수료는 이미 정합) |

→ 불일치 핵심 = **체결 *여부* 모델 부재**(수수료는 이미 maker). ts 인덱싱 확정: `ts`=진입봉, 신호=`<ts` 마감봉, **`close[ts-1]`=라이브 주문가**(테스트로 핀).

## 2. 구현 (이번 범위)

- `_resolve_entry_fill(symbol, ts, action) → (filled, entry_price)`:
  - `maker_open`/`taker`: `open[ts]` 확정 체결.
  - `post_only`(기본): limit=`close[ts-1]`, 진입봉이 닿으면(롱 `low≤limit`/숏 `high≥limit`) limit 체결(maker), 안 닿으면 스킵. **체결률 상한**.
  - `post_only_strict`: 시초가 갭 회피 — 롱 `open>limit`/숏 `open<limit` 면 미체결. **체결률 하한**.
- 수수료 분기(`_simulate_trade`): post_only/strict = maker+taker+**1slip**(resting=무진입슬리피지), maker_open=maker+taker+2slip, taker=taker+taker+2slip.
- 3 전략(oi_surge/breakout/trend_follow) 진입부 공통 배선. 신호·전략 파라미터 **무변경**.
- 기본값 = `post_only`(라이브 일치). legacy 는 `maker_open` 으로 비교 보존.

## 3. A/B 리포트 (실측, `scripts/run_b6_fill_model_ab.py`)

데이터: `backtests/cache/verification/*_1d.parquet`(mainnet 1d) + `*_1h.csv`(1h).

```
=== daily_tsmom (1d, 16종목) ===
model              trades  fill%   win%     PF    expR    ret%
maker_open(legacy)     80   100%  41.2%   0.95   0.014   -0.7%
post_only(상한)        80   100%  41.2%   0.96   0.023   -0.6%
post_only_strict(하한) 62    78%  43.5%   0.93   0.074   -0.7%
taker                  80   100%  41.2%   0.94   0.012   -0.9%

=== breakout (1h, 14종목) ===
maker_open             13   100%  30.8%   0.38  -0.409   -0.2%
post_only              13   100%  38.5%   0.43  -0.358   -0.2%
post_only_strict       13   100%  38.5%   0.33  -0.361   -0.3%
taker                  13   100%  30.8%   0.37  -0.424   -0.2%

=== oi_surge (1h, 14종목) ===  → 0 trades (1h 504봉 = regime 워밍업 부족, 데이터 한계)
```

**해석**:
- **체결률이 안 떨어진다**: 24/7 연속 크립토는 `open[ts]≈close[ts-1]`(인터바 갭 거의 0) + 봉 내 변동폭 ≫ 15초 → limit(=prev close)이 봉 안에서 거의 항상 터치됨. 상한 100%, 하한도 78%(1d)/100%(1h). 라이브 15초가 함의하는 급감은 봉에서 안 보임.
- **엣지가 안 바뀐다**: PF 가 모든 모델에서 daily 0.93~0.96, breakout 0.33~0.43. post_only 가 maker_open 을 *역전*시키지 않음(오히려 strict 는 expR 소폭↑ — 갭 진입 일부 배제 효과).
- **모든 모델 PF<1**: 체결 모델 무관하게 net 손실 — 양의 엣지 부재.

## 4. 왜 B-3 가 봉으로 측정 안 되나 (granularity)

라이브 타임아웃 **15초** ≪ 백테스트 봉 **1d/1h**. post-only 역선택의 본질("신호 직후 15초 안에 가격이 limit 로 돌아오는가")은 **분/초 단위 path** 정보인데, OHLCV 봉은 그 path 를 갖지 않는다(봉 내 high/low 만). 연속 크립토에선 봉 내 저점이 거의 항상 prev close 아래로 dip → 상한 모델 ~100% 체결. 하한(시초가 갭) 모델도 연속성 때문에 큰 갭이 드물어 78%+ 체결. **즉 봉 백테스트는 B-3 를 위/아래 어느 bound 로도 분해하지 못한다 — 무효과가 아니라 *측정 불가*.**

## 5. 다음 인계 (B-8 / Phase C)

- **B-8 (testnet e2e)**: 실제 GTX post-only 체결률·미체결을 측정. B-7 `unfilled_signals` 에 +1/+3/+6bar forward-return 채워 *미체결=달린 승자 vs 체결=반전 패자* 직접 비교 → B-3 의 *유일한* 검정력 있는 측정.
- **Phase C**: 엣지 재판정 — 단, A/B 가 이미 시사하듯 *체결 모델과 무관하게 PF<1* 이면 엣지 부재가 1차 결론. CostGuard default_win_rate 손익분기 아래로(2.1/D-3).
- **실거래 HOLD 유지** — 검증 인프라가 내린 정직한 결론.

## 6. 변경 파일

**소스(수정)**: `backtesting/backtest_engine.py` (BacktestConfig.entry_fill_model + _resolve_entry_fill + 수수료 분기 + 3 eval 배선)
**소스(신규)**: `scripts/run_b6_fill_model_ab.py` (A/B 리포트)
**테스트(수정)**: `tests/test_backtest_engine.py` (B6 단위테스트 9 — ts핀/체결/미체결/경계/숏/legacy/taker/엣지/strict)
**문서(신규)**: `docs/REFACTOR_B6_REPORT.md`
