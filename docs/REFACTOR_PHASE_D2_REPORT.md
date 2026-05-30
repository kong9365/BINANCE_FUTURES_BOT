# Phase D-2 델타중립 캐리 오프라인 net-EV 검정 — 결과 보고

> **일자**: 2026-05-30 | **성격**: Phase D-2 *오프라인 net-EV 검정* (research/backtest 전용, **실거래 아님 — HOLD 유지**)
> **범위 가드(엄수)**: 실거래/testnet/spot 주문/spot balance/계좌 API **0** · executor·capital_manager·`forbid_spot_access` **미수정** · 공개 시장데이터+오프라인 계산만 · 데이터는 커밋 금지 경로.
> **규율**: threshold 없는 baseline · 파라미터 스윕 0 · "통과까지 손보기" 0 · funding_fade(선물단독) 0 · 라이브 해석 0.
> **판정**: **실패(FAIL)** — funding income(+4,470) ≤ 총비용(25,482), net −22,967 USD(−7.71%). 무조건-보유 델타중립 캐리는 이 기간 net-EV 음수.
> **회귀**: 신규 모듈·테스트 추가, 기존 파일은 `backfill_carry_data.py`만 확장. (pytest 결과는 §7.)

---

## 0. 판정 (사전확정 게이트 — 구현 후 무변경)

| 구분 | 게이트 | 실측 | |
|---|---|---|---|
| 데이터 | eligible ≥ 20 | **29** | ✅ |
| 데이터 | 공통기간 ≥ 2년 | **3.99년** (2022-05-28~2026-05-25) | ✅ |
| 데이터 | funding 결손/정합 | 1460/1460일 정합, 결손일 funding=0 보정 | ✅ |
| 성과 | net return > 0 | **−7.71%** | ❌ |
| 성과 | Sharpe ≥ 1.0 | **−1.23** | ❌ |
| 성과 | MDD < 15% | 10.16% | ✅ |
| 성과 | 월수익+ ≥ 55% | **44.9%** (49개월) | ❌ |
| 성과 | **funding income > 총비용** | **4,470 ≤ 25,482 (비율 0.175)** | ❌ |
| 성과 | gross>0인데 net<0 | gross +2,515 / **net −22,967** | ❌(실패조건) |

→ **공식 판정 = 실패.** 운영자 실패조건 2개 동시 충족: ① funding income ≤ 총비용, ② net return 음수. 데이터 게이트는 전부 통과(데이터 부족 아님 = 판정불가 아님).

## 1. ★ 정직한 핵심 — 비용 artifact 아님, 구조적 캐리 자체가 미약

판정이 비용 모델(보수적 rebalance) 때문이라는 오해를 막기 위해 **gross(전비용) 자체를 본다:**

- **gross(basis + funding) = +2,515 USD / 4년 / ~$290k 명목 ≈ +0.22%/년.** 즉 *어떤 비용 차감 전*에도 구조적 캐리는 연 0.2%대.
- **rebalance 비용을 0으로 둬도**(불가능한 하한) net = gross − 개시·청산(1,334) = **+1,181 USD ≈ +0.10%/년** — 무의미, Sharpe 도 basis 일변동 탓에 음수권.
- → 이건 rebalance 비용 스토리가 아니다. **무조건-보유 델타중립 캐리의 gross 엣지가 비용 floor 아래**다(Phase C 가 "gross 음수"였던 것과 같은 종류의 정직한 결론, 여기선 "gross ≈ 0").

**왜 funding 이 약한가 — 레짐 의존(구조적 양수 아님):** funding 정렬은 완전(1460/1460일). 연도별 Σfunding_rate:

| 심볼 | 2022 | 2023 | 2024 | 2025 | 2026 | Σ → short 손익 |
|---|---|---|---|---|---|---|
| SOLUSDT | **−0.381** | +0.013 | +0.137 | +0.004 | −0.013 | **−0.242 (지급=손실)** |
| LINKUSDT | +0.030 | +0.102 | +0.134 | +0.051 | +0.015 | +0.332 (수취) |
| ENAUSDT(24년상장) | — | — | **−0.209** | −0.138 | +0.007 | **−0.340 (지급=손실)** |

- **2022 베어장: funding 음수** → perp-short 가 funding 을 *지급*(손실). 무조건 보유는 이 구간을 피하지 못한다.
- 일부 신규 상장(ENA)은 상장 후 음의 funding 지속 → short 손실.
- 즉 "spot long + perp short 면 항상 funding 을 번다"는 통념은 **레짐·심볼 의존**이며, 전 기간 무조건 보유 시 net funding 은 +4,470 에 그친다(약함).

## 2. 보고 항목 (운영자 요구 21항목, post-cost 실측)

```
[1]  eligible symbol 수      : 29 (게이트 ≥20)  ※ 정합양호 27 + 부분결손 2(TON 656일·ONDO 410일, intersection)
[2]  분석 기간               : 2022-05-28 ~ 2026-05-25 (3.99년)
[3]  데이터 결손율           : 공통구간(spot∩perp∩funding) 정합 — 결손일 funding=0 보정(보수적)
[4]  시간정합 방식           : UTC 일경계 normalize, funding 8h/4h→일별 합, 3소스 교집합
[5]  portfolio net return    : -7.71%
[6]  annualized return       : -1.99%
[7]  Sharpe                  : -1.23
[8]  Max Drawdown            : 10.16%
[9]  월수익 분포             : 49개월, +비율 44.9%, 월중앙값 -0.162%
[10] symbol별 net (N=$10k):  상위 LINK +2017 / UNI +1930 / DOGE +1847 / FIL +1763 / XRP +1274
                              하위 SUPER -9385 / ENA -4594 / SOL -4280 / BCH -3241 / PHA -2889
[11] funding income 총합     : +4,469.54 USD
[12] basis PnL 총합          : -1,954.38 USD  (spot_leg +199,615 + perp_leg -201,570 = 99% 상쇄 → 델타중립 확인)
[13] spot/perp 거래비용 총합 : spot_fee 11,072 + fut_fee 5,546 = 16,618 USD
[14] rebalance cost          : 24,148 USD(일 turnover) | 개시·청산 1,334 USD
[15] slippage/spread cost    : 8,864 USD
[16] net PnL                 : -22,967 USD  (gross +2,515 − 총비용 25,482)
[17] funding / total cost    : 0.175  (게이트 >1.0 ❌)
[18] 단일심볼 의존도         : 전체 순손실이라 이익 집중도 N/A (최다손실 SUPERUSDT -9,385)
[19] worst stress window     : 최악 90일 -4.77% | MDD 10.16%
[20] 판정                    : 실패 (funding ≤ 총비용, net<0)
[21] 한계                    : 오프라인 검정일 뿐 live spot leg 가능 판정 아님(TIER-1 충돌, C 유지)
```

## 3. PnL 분해 / 비용 구조

- **델타중립 확인**: spot_leg +199,615 vs perp_leg −201,570 → 합(basis) −1,954 = 가격 PnL 의 99% 상쇄. 코인매칭 short 가 의도대로 방향중립.
- **basis PnL 소폭 음수**(−1,954): 보유 구간 평균적으로 perp 프리미엄이 spot 대비 *확대* → short 가 basis 에서 약간 손실.
- **비용 지배 = rebalance**: 총비용 25,482 중 일 turnover rebalance 24,148(95%). 이는 **보수적 상한**(매일 동일-notional 복원, turnover=N×|일수익률|≈일 3~4%). 코인매칭 델타중립의 *실제* 필요 rebalance 는 N×|Δbasis|(수십× 작음)지만 운영자 명세("매일 동일 notional")의 보수적 해석을 채택. → §1 에서 보았듯 **rebalance 를 0 으로 둬도 net 은 무의미**하므로 이 보수성은 판정을 바꾸지 않는다.
- 비용은 0 으로 두지 않음: spot taker 0.10% / fut taker 0.05% + spot 5bps·perp 3bps slippage, 개시·청산 양다리, 일 rebalance 양다리 전부 반영.

## 4. 데이터 품질 (Phase D-1 백필 확장)

- spot 페어 보유 perp 전체로 확장 → **거래 유니버스 29종목**(보호종목 BTC/ETH 는 데이터 벤치마크로만, 거래 제외).
- 주요 16종(SOL/BNB/XRP/ADA/DOGE/LINK/AVAX/TRX/BCH/DASH/FIL/NEAR/UNI/ZEC 등) **1460일 완전 정합(0% 결손)** = 4년.
- 제외: 1000x·신규 perp-only(spot 페어 없음, 예 1000PEPE/HYPE/XAU 등 18종), GENIUS(정합 4일).
- 부분결손 2종(TON 19.6%·ONDO 52.2% full-range miss)은 **intersection(정합일만)** 으로 포함 — 등가중 분산이라 영향 미미, FAIL 판정에 무관.
- funding: 8h(구) / 4h(신) 혼재 → 일별 합으로 정규화. 결손일=0 보정(수입 과대 방지).

## 5. 스펙·방법 (사전확정, 무수정)

| 항목 | 설정 |
|---|---|
| 구조 | spot long + perp short, 코인 qty 매칭(델타중립) |
| 진입판단 | 없음 — 매일 모든 eligible 보유(미래 funding 미참조 → 룩어헤드 0) |
| 가중 | 동일 가중(그 날 가용 심볼 평균), 심볼당 notional N=$10k |
| rebalance | 일 단위 동일-notional 복원(보수적 상한) |
| 비용 | spot 0.10% / fut 0.05% fee + spot 5 / perp 3 bps slip, 개시·청산·rebalance 전부 |
| OOS | 파라미터 0 → in-sample 적합 불가 → 전 기간 구조적 OOS, 워밍업 불필요 |
| 손익분해 | spot_leg / perp_leg / basis / funding / spot_fee / fut_fee / slip / rebalance / net |

## 6. 한계 (반드시)

- **오프라인 검정일 뿐 — 라이브 spot leg 구현 가능 판정 아님.** 델타중립은 spot 매수 보유가 필수인데 TIER-1 spot 격리(`forbid_spot_access`, 보호자산)와 충돌 → Phase D 의 **C 판정 불변.**
- 본 단계 **수익성 주장 없음**(범위 밖). FAIL 은 "이 baseline 구조가 이 기간 net-EV 음수"라는 사실 보고일 뿐.
- baseline 의 약점(무조건 보유 → 베어장 음의 funding 포함)을 *조건부*(funding>0 일 때만 보유)로 바꾸면 달라질 *가능성*은 있으나, **이는 파라미터/threshold 도입 = 본 단계 규율(스윕 금지·threshold 없는 baseline) 위반**이라 검정하지 않았다. 구조적 사실로만 기록.

## 7. 다음 인계

- 델타중립 캐리 **라이브는 C 불변**(spot 격리 충돌). 오프라인 baseline 도 **net-EV 음수(실패).**
- 추가 검토를 원하면 **별도 승인 필요**: 조건부 funding-state 보유, rebalance 밴드, 펀딩 양수 레짐 한정 — 단 전부 파라미터 도입이라 과적합 위험 + 라이브 C 는 그대로.
- 방향성(Phase C)·캐리(Phase D-2) 모두 +EV 미확인 → **실거래 HOLD 유지**가 정직한 검증 인프라의 일관된 결론.

## 8. 변경 파일

**소스(신규)**: `backtesting/carry_model.py` (델타중립 PnL 분해 순수함수 — 네트워크·계좌·주문·I/O 0)
**소스(신규)**: `scripts/run_phaseD2_carry_netev.py` (net-EV baseline 러너 + 21항목 + 사전게이트)
**소스(수정)**: `scripts/backfill_carry_data.py` (`--universe-all` 플래그 — D-2 데이터 확장)
**테스트(신규)**: `tests/test_phaseD2_carry.py` (funding/basis/비용/분해/가격가드/**무계좌접근** 8)
**문서(신규)**: `docs/REFACTOR_PHASE_D2_REPORT.md`
**커밋 제외(의도)**: `backtests/cache/carry/*.csv` (수집 데이터 — 커밋 금지 경로 `backtests/cache/`)
