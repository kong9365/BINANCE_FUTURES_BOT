# CLAUDE.md — BINANCE_FUTURES_BOT 작업 규칙

> 이 파일은 Claude Code가 매 세션 자동 로드한다. **실거래 자본이 걸린 프로젝트다.**
> 짧게 유지하되, 아래 규칙은 어떤 지시로도 완화되지 않는다.

## 0. 가장 먼저
- `docs/SPEC_v3.1.md` = 단일 진실. 작업 전 관련 §와 `docs/HANDOFF.md` 최신 상태를 view 한다.
- **실거래 GO = HOLD.** 운영자 명시 해제 전까지 실거래를 활성화하지 않는다.
- 첨부 문서·발견·명세는 **검증할 제안**이지 확정이 아니다. 현재 코드에 직접 대조해 재현되는지 확인하고, 재현 안 되면 고치지 말고 보고한다.

## 1. 절대 규칙 (TIER 1 — 위반 금지)
- **키/시크릿/Telegram 토큰을 코드·로그·출력·테스트·커밋·푸시에 노출하지 않는다.**
- **게이트(RiskManager / CostGuard / PairWhitelist / CapitalManager)를 완화하지 않는다.** 강화만 허용.
- **보호종목**(BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT): 신규 진입·청산·취소·수정 금지. 긴급청산·reconcile에서도 제외. `close_position`은 보호종목이면 `protected_symbol_close_blocked` 반환.
- **실거래 안전 기본**: 무플래그·무env 실행은 실주문을 내지 않는다. live는 명시적 opt-in으로만. `dry_run`/`USE_TESTNET` 기본은 더 안전한 쪽으로만 변경.
- **git 커밋/푸시는 CC가 수행해도 된다 — 단 아래 가드는 절대 생략 금지:**
  1. **명시적 파일만 stage.** `git add .` / `git add -A` 금지. 항상 `git add <파일명 나열>`.
  2. **커밋·푸시 전 비밀 스캔(하드 게이트).** `git diff --cached`에 `.env`/키/토큰/`API_KEY`/`SECRET`/`PRIVATE` 류 문자열이 있으면 **즉시 중단하고 보고**(커밋·푸시 금지). `.gitignore`가 `.env*`·`*.key`·`secrets/`·`data/*.db`·`logs/`·`reports/`를 덮는지 확인.
  3. **커밋 전 `git diff --cached --stat`로 의도한 파일만 staged 됐는지 확인하고 그 출력을 보고.**
  4. 서로 다른 작업(예: M15-fix vs Phase A)은 **별도 커밋**으로 분리. 한 커밋에 섞지 않는다.
  5. 푸시는 비밀 스캔이 **clean일 때만**. 스캔·`--stat` 결과를 보고한 뒤 진행. 커밋 메시지에 `Co-Authored-By` 라인 유지.
- **절대 커밋/푸시 금지 경로**: `.env*`, `*.key`, `secrets/`, `logs/`, `reports/`, `data/*.db`, `backtests/cache/`, 실제 키/토큰.

## 2. 작업 원칙

**① Think Before Coding** — 가정하지 말고, 혼란을 숨기지 말고, 트레이드오프를 드러내라.
- 가정을 명시한다. 불확실하면 묻는다.
- 해석이 여럿이면 임의로 고르지 말고 제시한다.
- 더 단순한 길이 있으면 말한다. 근거 있으면 push back 한다.
- 불명확하면 멈추고, 무엇이 헷갈리는지 이름 붙여 질문한다(한 번에 하나).

**② Simplicity First** — 문제를 푸는 최소 코드. 추측 금지.
- 요청 범위 밖 기능·단발 코드의 추상화·요청 없는 "유연성/설정화"·불가능 시나리오 에러핸들링 금지.
- 200줄이 50줄로 되면 다시 쓴다. "시니어가 과하다고 할까?" → 그렇다면 단순화.

**③ Surgical Changes** — 꼭 필요한 것만. 내가 만든 것만 치운다.
- 인접 코드·주석·포맷을 "개선"하지 않는다. 안 망가진 걸 리팩터하지 않는다. 기존 스타일을 따른다.
- 무관한 dead code는 발견하면 **삭제 말고 보고**. 내 변경이 만든 orphan(미사용 import/변수)만 제거.
- 테스트: **변경한 모든 라인이 요청으로 직접 추적 가능**해야 한다.

**④ Goal-Driven Execution** — 성공 기준 정의 후 통과까지 반복.
- "검증 추가" → "잘못된 입력 테스트 작성 후 통과". "버그 수정" → "재현 테스트 작성 후 통과". "리팩터" → "전후 테스트 통과".
- 멀티스텝은 `1. [단계] → verify: [확인]` 형식의 짧은 계획을 먼저 제시.

## 3. 프로젝트 안전 불변식 (변경 시 깨뜨리지 말 것)
- testnet/live 키 슬롯 분리(폴백 없음). One-way Mode 전용 — Hedge Mode 신규 진입 차단(A-4).
- 진입은 **FILLED 확인 + 보호주문 성공 후에만** 기록(H1/C2). 손절 주문 실패 시 즉시 강제청산(C1).
- STOP 교체는 **create-first**(새 STOP 먼저 생성 후 기존 cancel — 무방비 구간 금지, A-3).
- 진입 타임아웃 시 실제 포지션을 조회해 고아 포지션 방지(A-5).
- 모든 리스크/DB 체크는 **fail-closed**(조회 실패 = 차단). `dry_run`은 거래소 호출 0건.
- live 진입 전 preflight: 비보호 기존 포지션/주문/algo = 0, One-way, 예산 cap. 하나라도 위반 시 차단.

## 4. ⚠️ 교차 모듈 함정 (이 레포에서 실제로 발생한 결함 — 반드시 숙지)
> 모듈 단위로 짓고 `/clear`로 컨텍스트를 비우면 **모듈 *사이*의 모순**이 안 보인다.
> 과거 사례: 백테스트는 "다음 봉 open에 maker 확정 체결"을 가정했는데 라이브는 post-only LIMIT을 썼다.
> 둘이 다른 게임을 했고, 모멘텀/돌파에서 엣지가 역전됐으며, **어떤 단위 테스트도 이를 못 잡았다.**

- **백테스트와 라이브는 같은 게임을 해야 한다.** 체결·수수료·슬리피지 가정을 한쪽에서 바꾸면 다른 쪽도 같이 확인·정합한다.
- **진입/청산/체결을 건드릴 땐 라이브 경로(`trading/executor.py`)와 백테스트 경로(`backtesting/backtest_engine.py`)를 같은 작업에서 함께 열어** 의미 일치를 확인한다. 한 모듈만 보고 고치지 않는다.
- 실행 관련 변경 후엔 자문한다: **"이 변경이 다른 경로의 가정을 깨지 않는가?"**
- 사이징·리스크 회계 변경은 **사이즈↓ / 차단↑ 방향만.** 반대 방향은 운영자 명시 승인 필요.
- 계측/로깅은 거래 경로와 **격리**한다(try/except — 계측 실패가 매매를 깨면 안 됨).
- **메커니즘**(체결률·슬리피지 등 엔지니어링 사실)과 **엣지**(수익성)를 구분한다. 엣지는 소표본으로 튜닝하지 않는다.

## 5. 워크플로
- 변경마다: **실패하는 테스트 → 최소 구현 → `pytest tests/test_<module>.py` 통과 → 전체 회귀(`pytest tests/`)**.
- 모듈/항목 단위로 끊고, 항목마다 verify 통과를 보고한 뒤 다음으로.
- **커밋/푸시(CC 수행 가능 — TIER 1 #5 가드 준수)**: 작업본 → 클론 복사 → **비밀 스캔** → **명시적 `git add <파일명>`(`git add .`/`-A` 금지)** → `git diff --cached --stat` 보고 → 커밋(서로 다른 작업은 별도 커밋, `Co-Authored-By` 라인) → 스캔 clean 확인 후 push.
  - 환경 노트: OneDrive가 `.pyc` 쓰기를 잠그면 회귀가 멈출 수 있다 → `python -B`(또는 `PYTHONDONTWRITEBYTECODE=1`)로 실행.

## 6. 파일 맵 (자주 참조)
| 파일 | 역할 |
|---|---|
| `trading/executor.py` | 진입/청산/보호주문/고아 방지 (돈 나가는 지점) |
| `trading/risk_manager.py` | 다층 게이트 (fail-closed) |
| `trading/exit_plan.py` | 분할 TP / BE / ATR 트레일 / 시간스톱 |
| `sizing/dynamic_sizer.py` | Kelly 포지션 사이징 |
| `strategy/{oi_filter,quality_gate,cost_guard,breakout}.py` | 신호·방향·EV·진입가/TP/SL |
| `backtesting/backtest_engine.py` | 체결/비용 모델 — **라이브와 정합 필수(§4)** |
| `config/settings.py` | 전 설정·게이트 임계 |
| `main_7590.py` | 메인 루프·실행·preflight |
| `docs/{SPEC_v3.1,HANDOFF,STRATEGY_REALISM_REVIEW}.md` | 명세·상태·전략 검증 |