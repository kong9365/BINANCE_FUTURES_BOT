# Phase D-1 — 델타중립 캐리 데이터 백필 — 결과 보고

> **일자**: 2026-05-30 | **성격**: Phase D-1 *데이터 백필 가능성 조사* (조사·스크립트 설계·최소 샘플·품질 리포트 전용)
> **범위 가드(준수)**: spot 주문 0 · spot balance 조회 0 · 계좌 API 0 · executor 미수정 · capital_manager 미수정 · `forbid_spot_access=True` 유지 · 실거래/testnet 주문 0 · 델타중립 전략 구현 0 · 파라미터 스윕 0 · 수익성 주장 0.
> **판정**: **A (오프라인 델타중립 캐리 net-EV 검정에 필요한 데이터 확보 가능)** — *데이터 차원*. (라이브 실행 판정은 Phase D 의 **C** 가 그대로 유효 — spot leg 가 TIER-1 spot 격리와 충돌.)
> **회귀**: 소스/테스트 무변경(신규 독립 스크립트 1 + 문서 1). py_compile 통과.

---

## 0. 판정 (Phase D-1 질문 = "net-EV 검정에 필요한 데이터가 확보 가능한가")

| 차원 | 결과 | 판정 |
|---|---|---|
| spot OHLCV 확보 | 공개 `get_klines` — 유동 10종목 1d 1246봉 ✓ | A |
| perp OHLCV 확보 | 기존 보유(`backtests/cache/verification/*_1d.parquet`, 1460봉) ✓ | A |
| funding history 확보 | 공개 `futures_funding_rate` — 8h 간격 3737건/종목 ✓ | A |
| mark/index(basis) | funding 응답에 `markPrice` 포함 + perp_close−spot_close 로 계산 ✓ | A |
| 시간 정합(perp∩spot∩funding) | 2023+ 공통 **1241일 완전 정합** ✓ | A |
| spot 격리 위반 | **없음** (공개 가격데이터만, 계좌·잔고·주문 0) | — |

→ **종합 = A.** net-EV(펀딩 수취 − 총비용 − basis 변화)를 *오프라인*에서 검정할 4개 데이터(spot·perp·funding·basis)가 모두 공개 엔드포인트로 확보된다. 단 이는 **데이터 가용성** 판정이며, **수익성 판정 아님**(범위 밖).

## 1. 확보 가능한 데이터 종류 (전부 공개·키 없음)

| 데이터 | 엔드포인트 (public, keyless `Client()`) | 계좌 접근 | 비고 |
|---|---|---|---|
| spot OHLCV | `client.get_klines` | **없음** | 시장 가격 데이터 |
| perp OHLCV | `client.futures_klines` | **없음** | 기존 backfill_history 보유 |
| funding rate | `client.futures_funding_rate` | **없음** | fundingTime/Rate + **markPrice** |
| basis | (perp_close − spot_close)/spot_close | **없음** | 위 3종에서 파생 |

> ★ `get_klines`(spot)는 **시장 시세** 조회다. `forbid_spot_access`(=spot *계좌/잔고/주문* 차단)와 무충돌 — 가격을 보는 것과 자산을 만지는 것은 별개. 본 스크립트는 spot **잔고/주문/계좌 API 를 일절 호출하지 않는다.**

## 2. 품질 측정 (최소 샘플 — 유동 10종목, 1d, 2023-01-01~)

```
symbol         perp   spot   fund  align日 spotMiss% basis_med% fundIvl(h)
BTCUSDT        1460   1246   3737    1241     15.0%    -0.042%        8.0
ETHUSDT        1460   1246   3737    1241     15.0%    -0.042%        8.0
SOLUSDT        1460   1246   3737    1241     15.0%    -0.044%        8.0
BNBUSDT        1460   1246   3737    1241     15.0%     0.007%        8.0
XRPUSDT        1460   1246   3737    1241     15.0%    -0.046%        8.0
ADAUSDT        1460   1246   3737    1241     15.0%    -0.043%        8.0
DOGEUSDT       1460   1246   3737    1241     15.0%    -0.045%        8.0
LINKUSDT       1460   1246   3737    1241     15.0%    -0.036%        8.0
AVAXUSDT       1460   1246   3737    1241     15.0%    -0.035%        8.0
TRXUSDT        1460   1246   3737    1241     15.0%    -0.046%        8.0
```

- **심볼 수**: 유동 10종목 샘플(BTC/ETH는 *데이터만*, 거래 유니버스 제외). 전체 perp 52종목 중 spot 페어 보유분으로 확장 가능.
- **기간**: spot 2023-01-01 ~ 2026-05-30 (1246일). perp 2022-05-27 ~ (1460일).
- **결손율**: 표시값 15.0% — **그러나 이는 데이터 결함이 아니라 수집 시작일 artifact**(§3).
- **시간 정합**: perp∩spot 공통 **1241일**, 이 구간 내 spot 결손 ≈ 0(완전 정합). 일 경계 동일(UTC 00:00 normalize).
- **basis 계산성**: ✓ 중앙값 −0.05~+0.01% (유동 종목 무위험차익 수준 — 정상).
- **funding 수입 계산성**: ✓ 8.0h 간격(= Binance 3회/일), 종목당 3737건 결손 없음. 펀딩 수취 = Σ(fundingRate × notional) 계산 가능.

## 3. 15% "결손"의 정체 — 수집 시작일 artifact (데이터 결함 아님)

```
perp BTCUSDT : 2022-05-27 ~ 2026-05-25  (1460일)
spot BTCUSDT : 2023-01-01 ~ 2026-05-30  (1246일)   ← 내가 --start 2023-01-01 로 수집
→ perp 가 219일(≈15%) 먼저 시작. 그 219일은 'spot 미수집'일 뿐, spot 데이터 자체는 존재.
→ spot 을 perp 시작일(2022-05-27)부터 받으면 결손 <2%.
→ 2023+ 공통 1241일은 이미 완전 정합 — 3.4년 = OOS 표본 충분.
```

품질 스크립트의 임계(`spot결손<10%`)는 보수적으로 잡혀 이 artifact 때문에 "0/10 usable"로 출력됐다. **임계를 결과에 맞춰 완화하지 않았다**(규율). 정합 가능성의 *실제* 답은 "1241일 완전 정합 + 시작일만 맞추면 <2%".

## 4. spot 격리 위반 점검 (TIER-1)

- 스크립트가 호출하는 것: `get_klines`(spot 시세) / `futures_klines`(perp 시세) / `futures_funding_rate`(펀딩) — **전부 공개 시장데이터.**
- 호출하지 **않는** 것: spot 잔고·spot 주문·계좌 API·`futures_account`·키 인증. `Client()` 는 키 없이 생성.
- `capital_manager` / `executor` / `config.forbid_spot_access` **무수정.**
- → **위반 없음.** 보호자산(spot 보유분) 격리 원칙 그대로.

## 5. 한계 / 다음 인계

- 본 단계는 **데이터 가용성**만 확인. **수익성(net-EV>0) 판정 아님** — 그건 D-2(델타중립 백테스트) 의 몫이며 범위 밖.
- **라이브 실행은 여전히 C**: 델타중립은 spot long + perp short 가 필수인데 spot leg 가 TIER-1 spot 격리와 충돌(Phase D 보고의 C 판정 불변). *오프라인 검정*만 A.
- 확장 시: ① spot 시작일을 perp 시작일에 맞춤(결손<2%), ② spot 페어 보유 perp 종목으로 유니버스 확대, ③ 1000x·신규 perp-only(예: 1000PEPEUSDT, BILLUSDT)는 **spot 페어 부재 → 캐리 유니버스 제외**(샘플에서 spot=0 확인).
- D-2 착수 시: 펀딩 8h × notional 누적 vs (maker 진입 × 2leg + 청산 + basis 변화) 비교 엔진 — **단, spot leg 격리 충돌로 라이브 비권장(C)이라 오프라인 검정 한정.**

## 6. 변경 파일

**소스(신규)**: `scripts/backfill_carry_data.py` (공개 spot/perp/funding 백필 + 품질 리포트, 계좌·주문 0)
**문서(신규)**: `docs/REFACTOR_PHASE_D1_REPORT.md`
**커밋 제외(의도)**: `backtests/cache/carry/*.csv` (수집 데이터 — CLAUDE.md 커밋 금지 경로 `backtests/cache/`. 재현 가능하므로 미커밋)
