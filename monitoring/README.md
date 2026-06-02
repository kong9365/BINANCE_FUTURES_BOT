# monitoring — 실시간 관찰 알림 + 페이퍼(가상) 트레이딩 로그

> **읽기전용 · 가상 · 키불필요(시장데이터) · 실거래 HOLD.** 자동매매가 아니라 *의사결정 보조 + 학습/검증 도구*.
> 봇은 "사라"고 하지 않는다 — **관찰된 사실 + 손익구조**만 제시하고, 진입 판단·책임은 운영자.

## ★ 정직성 전제 (반드시 숙지)
- 지금까지 **5회 검증 전부 directional 무엣지**. 이 알림 신호(거래량·OI·돌파·taker)는 *그* 무엣지 신호들이다.
- 알림은 "추천"이 아니라 **"관찰 보고"** — 방향 적중 보장 없음. **방향 확률은 절대 제시하지 않는다.**
- 손익구조(R:R·SL·TP)는 측정 가능·룩어헤드0 → 정직한 정보. 4지표는 "정렬 상태"로만 보여준다.
- **페이퍼 로그도 무엣지로 나올 가능성이 높다**(정직). 미래 데이터·돈 0으로 재확인하는 6번째 검증.
- **n≥200 누적까지 수주~수개월**(급히 보지 말 것). PASS처럼 보여도 **실거래 HOLD**.

## 구성
| 파일 | 역할 |
|---|---|
| `observer.py` | 4지표(거래량·OI·추세·taker) 산출 + STRONG/WEAK 분류 + 순위점수. 룩어헤드0·방향확률 금지. |
| `paper_log.py` | SQLite *분리* 가상로그 — 기록·SL/TP/time_stop 추적·집계. net_R = 백테스트 post_only 일치. |
| `alert.py` | 3블록 알림 포맷(무슨일/손익구조/경고) + "검증된 엣지 없음" 경고 항상. |
| `notify.py` | Telegram *발신 전용* 콜백(미설정 시 로그-only, fail-soft). |
| `runner.py` | 공개 keyless 스캔 루프(보호종목 제외·STRONG 상위 알림·WEAK+STRONG 로그·open 추적). **executor·실주문 0.** |
| `scripts/run_observer.py` | CLI 데몬 진입점. |
| `dashboard/pages/7_paper_log.py` | 페이퍼 로그 대시보드 탭(평결박스·STRONG/WEAK·gross vs cost·누적곡선·n<200 배지). |

## 가동 (운영자) — 비밀 *값*은 운영자가 `.env` 에 직접 (CC 취급 0)
`run_observer.py` 가 프로젝트 `.env` 를 자동 로드한다(`.env` 는 `.gitignore` → 커밋 안 됨).
**운영자가 `.env` 에 아래를 직접 추가**(CC 는 키 값을 보지도 쓰지도 않음):
1. **활성화(필수)**: `MONITORING_ENABLED=true`
2. **(선택) Telegram 발신** — 본인이 직접: `TELEGRAM_BOT_TOKEN=<봇토큰>` · `TELEGRAM_CHAT_ID=<챗ID>` (없으면 알림은 로그로만).
   - ※ **바이낸스 API 키는 불필요** — 시장데이터는 공개 keyless(`Client()`).
3. **(선택) 조정**: `MONITORING_SYMBOLS=BNBUSDT,SOLUSDT,...` · `MONITORING_BUDGET_USDT=200` · `MONITORING_PAPER_DB=data/paper_log.db`

실행: `python scripts/run_observer.py` (테스트는 `--cycles 1`) · 대시보드: `streamlit run dashboard/app.py` → "페이퍼 로그" 탭

## 빈도 / 임계 (0단계 검증·승인 반영)
- **알림(STRONG)**: 거래량≥2.5배 · |OI|≥5% · EMA200+Donchian20 정렬 · taker≥65%(롱)/≤35%(숏).
  → 원시 ~5.7건/일, **일일캡 1~2 + 24h 코인쿨다운 + 최소간격** → 점수 상위 1~2개만 발신.
- **로그(WEAK)**: 거래량≥1.5배 · taker≥55/≤45 · ≥3지표 정렬 → 캡 없음(빠른 누적). 코인당 동시 1건.
- 순위 점수 = `vol/2.5 + taker강도/임계 + |OI|/5 + 돌파폭/ATR` (정렬강도 합산, *성적 아님*).

## 안전 불변식
- 시장데이터 = 공개 keyless 엔드포인트만. **executor·실주문·실키·LIVE_TRADING 0.**
- **보호종목**(BTC·ETH·HOLO·CFX·LYN·INJ) 알림·로그에서 제외(이중 가드).
- 페이퍼 로그 DB(`data/paper_log.db`)는 가상 기록만 — 어떤 실주문도 트리거하지 않음. `.gitignore` 대상.
- 기존 트레이딩/하네스/게이트 수정 0(신규 모듈 격리). 페이퍼 로그가 directional 엣지를 *만들지 않는다* — 측정할 뿐.
