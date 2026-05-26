# Binance Futures Bot — 세션 연속성 핸드오프 (컨텍스트 초기화 후 재개용)

> 최종 갱신: 2026-05-26 (**v3.2.0 청사진 리팩토링 M0 시작 — 알파 재검토 시나리오**)
> 본 문서는 [docs/REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) (마일스톤 M0~M6) 의 컨텍스트 진행 기록.
> 멀티 PC 연속성: 모든 세션 시작 시 `git pull` + 본 문서 + `REFACTOR_M<N>_REPORT.md` 확인 필수.
> (세션 0~12 구현 완료 시점의 옛 핸드오프는 git 히스토리에 보존됨.)
>
> 🆕 **v3.2.0 청사진 리팩토링 시작(2026-05-26) — 알파 재검토 시나리오 운영자 명시 GO.**
>
> **배경**: [SYSTEM_DESIGN_BLUEPRINT.md](../SYSTEM_DESIGN_BLUEPRINT.md) (2,601줄) 의 4-Layer + 5-Agent + ALCOA+ + GMP 적용. 운영자 결정 8건 ([REFACTOR_M0_BASELINE.md](REFACTOR_M0_BASELINE.md) §운영자 결정).
>
> **HANDOFF "알파 영구 중단" 재검토 (운영자 결정 #v2-1)**:
> 9개 실패 strategy 누적 결론 ("알파 추구 액티브 매매 안 함", 2026-05-23) 은 *현재 데이터 + 현재 임계* 기준이었음. 본 리팩토링은 다음을 추가:
>
> 1. **운영자 권장 7기준 게이트** (M2): n≥200 / Net PF≥1.25 / Expectancy_R>0 / avg_win/avg_loss≥1.5 / MDD≤25% / single_symbol<25% / **상위 3종목 제거 PF≥1.0** (← ZEC 단일종목 97% 행운 방어)
> 2. **ALCOA+ 트레이서** (M1) — 모든 SignalDecision/AgentReview/AuditLog 영속 + chain hash
> 3. **5-Agent 검토단** (M3) — Strategy/Risk/Execution/Data/Ops 다관점
> 4. **KillSwitch 어댑터** (M1) — 기존 btc_risk_off + 파일 기반 통합
> 5. **Stage 3 백스톱** (M4) — 90d -10% 또는 MDD -25% → 자동 archetype 재고
>
> **CLAUDE.md TIER 1 #9 조건부 완화** (운영자 명시 GO 2026-05-26): 1d 돌파/Donchian *완전 재검증* 만 허용. 같은 데이터+같은 임계 단순 재시도는 여전히 금지.
>
> **실거래 GO 여전히 HOLD**. M2 7기준 통과 시에만 R0_QUALIFIED → Phase 1.5 micro-live 별도 진행.
>
> **M0 작업 시작 (2026-05-26)**: CLAUDE.md v3.2.0 업데이트 + SPEC E-10 (2-tier hierarchy) + 본 HANDOFF 재검토 명시. pytest 기준선 = **718 passed** (이전 HANDOFF 2026-05-25 706 + 미반영 12).
>
> 🟥 **현재 상태(2026-05-23): 검증 종료(재확정) — 5개 전략군 모두 불합격.**
> 광범위 유니버스(≥$10M ~183종목, 2년 1.98M ohlcv) + 새 메커니즘 2개(ORB 인트라데이,
> CSM 스윙)까지 엄격 사전확정으로 재시도했지만 **둘 다 FAIL**(ORB: MDD 76%·Sharpe −3.88;
> CSM: LAB 한 종목이 수익 46% 집중 + MDD 57% — 본질은 "저시총 로또코인 잡기").
> 이전 OI-급증·돌파(1h/1d)·펀딩 페이드까지 합쳐 **5 distinct 전략군 모두 base-rate 그대로
> 실패**. 이 봇 설정(소액·USDT-M 무기한·테이커비용)에서 단순 TA·랭킹 기반 알파는 발견 안 됨.
> **결론(강화·재확정): 알파 추구 액티브 매매 안 함 → 미거래/보수 또는 단순 보유.**
> 영구 자산 = 검증 인프라(portfolio_backtest·cross_sectional·evaluate·universe·backfill_history).
> 미래 *근본적으로 다른* 데이터/메커니즘이 동일 사전확정 기준 통과 시에만 배포 재검토.
> 상세 = `docs/STRATEGY_REALISM_REVIEW.md §7-8` + Supabase `backtest_runs`. 실거래 GO = **HOLD**.
>
> 📊 **+1 추가 검증(2026-05-23): LCR(이벤트 반응형) Phase 0 무료 sanity → borderline FAIL.**
> 셋업 A 단독(BTC 평온, n=8,851) +4h 평균 +0.387%로 비용 임계 0.5%에 0.11%pt 못미침 →
> 풀-오토 LCR Phase 1~6 진행 중단. **그러나 셋업 C(BTC 동반 급락 시 진입 금지)는
> 데이터로 명확히 검증됨** (단독 +0.39% vs 동반 −0.65% = 1.03%pt 스프레드). 거래 엣지
> 아닌 *안전 필터*라 standalone 채택 가능. `docs/STRATEGY_REALISM_REVIEW.md §9` 참조.
>
> ✅ **셋업 C 구현 완료(2026-05-23, HEAD `2845934`)**: `strategy/btc_risk_off.py` +
> `BTCRiskOffConfig`(기본 enabled, 1.2%/6h) + `main_7590._iter` 통합. BTC 1h 종가 변화
> ≤ −1.2% 시 자동 6h 신규 진입 차단(쿨다운 만료 시 자동 해제, 중복 트리거 방지, Telegram
> CRITICAL 알림). **거래를 *막기만* 하는 룰** — 자본 보존 우선. 단위테스트 9건, 전체 429 passed.
> 실거래 GO는 여전히 **HOLD** (이 필터는 거래를 열지 않고 *닫기만* 함).
>
> ✅ **Microstructure Phase 2 Universe 확장 완료(2026-05-24) — Top-10 고정 폐기, Dynamic Tiered Universe 채택.**
> 운영자 critique 반영: Top-10 고정은 *수집기 검증용* 으로는 적절했으나 *최종 알파 검증용* 으로는 너무 좁음. Phase 2 데이터 누적을 위해 동적 Tier 분류 추가:
>
> **Tier 구조** (수집 *전용*, 실거래 universe 와 분리 — PAIR_WHITELIST.protected_symbols 정책 무변경):
> - **Tier 0 (core)**: BTC/ETH/SOL/XRP/DOGE/BNB — 항상 포함, depth+trade+force
> - **Tier 1 (full)**: 24h quoteVolume top N (default 20, CLI 15 보수) — depth+trade+force
> - **Tier 2 (light)**: 다음 M (default 50, CLI 30 보수) — trade+force (depth 미수집, 부하 감소)
> - **Tier 3 (monitor only)**: 기존 OHLCV/OI/Funding 경로 (`collect_oi` — 본 모듈 무관)
>
> **commodity-like 강화 (운영자 critique #3)**: exact symbol set `{XAUUSDT, XAGUSDT, CLUSDT, BZUSDT}` 기본 제외 (prefix 단독 사용 금지). exchangeInfo `contractType=PERPETUAL AND quoteAsset=USDT AND status=TRADING` 필수 통과. `--include-commodity-like` 옵션으로 실험적 포함 가능.
>
> **메타 lineage 테이블 (운영자 critique #1)**: `collection_universe_snapshots` (ts, tier, symbol, quote_volume, category, stream_types, source, resolver_version v1) — Phase 3 IC 분석 시 point-in-time universe 복원 보장. MCP migration `collection_universe_snapshots`.
>
> **multiplex 우선 + 보수 운영 (운영자 critique #2)**: 단일 multiplex socket (raw `websockets`) 으로 모든 stream 묶음 → DNS/TLS handshake 1회. python-binance 1.0.36 의 `futures_multiplex_socket` 이 depth 누락 버그 + `@aggTrade` 0건 (Binance 측 throttle) 발견 → URL 폴백 `<symbol>@trade` 채택 (raw trade, 더 granular, 동일 의미 - agg_trade_id 컬럼 그대로 사용). max_queue_size=1000, ping_interval=20s, 지수 backoff 재연결.
>
> **검증** (5min smoke, tier1=15/tier2=30, 51 symbols, 73 streams):
> - **l2=5,682 / agg=295,280 / liq=0 / reconnects=0 / avg latency 149ms / dedup 0**
> - Tier 0 core ✓ / Tier 1 (HYPE, ZEC, NEAR, BSB, BEAT, SUI, GRASS, IN, ONDO, BILL, GMT, 1000PEPE, TAO, WLD, GENIUS) ✓
> - depth 21 streams (core 6 + tier1 15) — Tier 2 30 심볼은 depth 미수집 (의도)
> - trade 51 streams (전 tier) / forceOrder 1 multiplex
> - commodity-like 0 (XAU/XAG/CL/BZ 제외 확인)
> - `collection_universe_snapshots` 51 rows 적재 ✓
>
> **신규**: `data/microstructure_universe.py` (resolver + helpers), `tests/test_microstructure_universe.py` (21 tests), `config/settings.py` (`MicrostructureUniverseConfig`). **수정**: `data/ws_collector.py` (symbol_streams override + raw multiplex), `scripts/run_ws_collector.py` (--tiered-universe + 5 CLI options). 단위테스트 27건 추가 (전체 **684 passed**, 회귀 0).
>
> **알파/ML/R0/진입/주문 경로 일체 미수정**. RiskManager/KillSwitch/보호종목 정책 그대로. 실거래 GO **HOLD** 무기한 유지.
>
> ✅ **운영 hardening (Phase 2-D, 2026-05-24)** — 운영자 가이드 반영:
> 1. **`agg_trades.source_stream` 컬럼 추가** (MCP migration `agg_trades_source_stream`) — `'aggTrade'` vs `'trade'` lineage 명시. Phase 3 CVD 분석 시 혼동 방지. `parse_agg_trade_message` 가 자동 채움 (msg 의 `a` 필드면 aggTrade, `t` 면 trade).
> 2. **23h 선제 재연결** — Binance WS 24h disconnect 정책을 *예측된 cycle* 로 처리. `_run_raw_multiplex_loop` 가 23시간 도달 시 정상 break → 즉시 재연결. `CollectorStats.preemptive_reconnects` 별도 카운터.
> 3. **`scripts/ws_health_report.py`** — 운영자 9-지표 일일 heartbeat: stream_count / l2-agg-liq row counts 24h / source_stream 별 / last_msg_at / latency / snapshot_count. `--telegram` 발송 옵션. `--window-hours 168` 로 7일 점검.
> 4. **`CollectorStats` 강화**: `p95_latency_ms` property, `queue_overflow_count` counter, `record_latency()` 메모리 cap (10k samples).
>
> 단위테스트 4건 추가 (전체 **688 passed**, 회귀 0). 30s smoke 결과: source_stream='trade' 233 rows 적재 확인, latency_samples 정상 누적, preemptive_reconnects=0 (아직 23h 미도달).
>
> **운영자 9-지표 (헬스 리포트 명시)**:
> 1. stream_count 2. l2_book_snapshots_24h 3. agg_trades_24h (+source_stream 분리) 4. liquidations_24h 5. avg/p95 latency_ms 6. reconnect_count 7. queue_overflow_count 8. last_message_age_sec 9. collection_universe_snapshot_count.
>
> **다음 단계 (개발 X, 운영 안정화)**:
> - BinanceWSCollect Windows Task 등록 (운영자 머신, `run_ws_collector.bat.template` 참조)
> - 24h 후 `ws_health_report.py --window-hours 24` 1차 점검
> - 7일 후 `--window-hours 168` 으로 row 폭증 점검 (agg ≈ 85M/24h 추정 → 7일 ≈ 595M, Supabase free tier 8GB → 모니터링 필수)
> - 60-90일 후 Phase 3 (IC 분석 → Rule-based) 재승인 요청
>
> 🆕 **Phase 2-E 완료(2026-05-25) — Disk Saturation Mitigation + 1m Aggregate + 새 Supabase 프로젝트.**
>
> **사건 요약**: Phase 2 데이터 누적 30분만에 무료 Supabase 디스크(2GB) 96.5% 도달 → Postgres crash → WAL replay 완료 후 *첫 WAL write 가 디스크 부족* → 무한 recovery loop → 자력 복구 불가. 운영자 결정: A. 새 Supabase 프로젝트 생성.
>
> **신규 Supabase 프로젝트**: `Binance-MS` (id `fsahlcmidhqdaxhqyhyk`, ap-southeast-1, FREE). 6 테이블 schema 재적용(MCP migration `all_microstructure_schema`): l2_book_snapshots, agg_trades(+source_stream), liquidations, collection_universe_snapshots, agg_trades_1m, backtest_runs. RLS off (무료 단순화).
>
> **신규 코드 (Phase 2-E)**:
> - `data/aggregator_1m.py` — Trade-tick → 1분 OHLC+volume+CVD 집계기. 메모리 상태 + Supabase upsert. WSCollector 의 write_sem 공유. 단위테스트 18.
> - `scripts/cleanup_old_data.py` — raw TTL DELETE (agg 1h / l2 6h). dry-run 지원. PostgREST 기반.
> - `scripts/run_cleanup.bat` + Windows Task `BinanceWSCleanup` (매시간 trigger).
> - 수정: `ws_collector.py` (handle_agg_trade 가 _agg_1m.record_trade 동시 호출 + _periodic_flush_loop 가 30s 마다 _agg_1m.flush_completed_buckets), `ws_health_report.py` (`agg_trades_1m_24h` 지표 추가).
> - 단위테스트 18건 추가 (전체 **706 passed**, 회귀 0).
>
> **보존 정책**: raw `agg_trades` 1h TTL / `l2_book_snapshots` 6h TTL / `agg_trades_1m` **영구 보존**(Phase 3 IC 입력) / liquidations·snapshots 영구. 60일 추정 디스크 ~700MB (Supabase free 2GB 안전).
>
> **검증**: 새 프로젝트에 데몬 재가동 후 3분만에 l2 5,920 / agg 111,000 / snap 51 정상 적재. WinError 10035 / PGRST002 / 57P03 *모두 사라짐*. agg_trades_1m 은 분 boundary 에서 첫 flush 대기.
>
> **3개 Windows Tasks 운영 중**:
> - `BinanceWSCollect` (AtLogOn, tier1=15 tier2=30, multiplex, 23h preemptive reconnect)
> - `BinanceWSCleanup` (매시간, raw TTL DELETE)
> - `BinanceWSHealthDaily` (매일 09:00, 9-지표 + Telegram)
>
> 실거래 GO **HOLD** 무기한 유지. Phase 3 (60-90일 후 IC 분석) 재승인 필요.
>
> 🚀 **Phase 2 Deploy 완료(2026-05-24)** — C:\bots\BINANCE_FUTURES_BOT 클론으로 코드 동기화 + Windows Tasks 등록:
> - `BinanceWSCollect` (사용자 logon trigger, 무한 실행, 5회 재시작, 배터리 허용)
> - `BinanceWSHealthDaily` (매일 09:00, --window-hours 24 --telegram)
> - **WinError 10035 fix**: 73 streams × 병렬 buffer flush → Supabase connection pool 포화 발견. `asyncio.Semaphore(1)` 공유로 모든 buffer 의 Supabase 쓰기 직렬화 → 즉시 해결. 단위테스트 39 passed 유지.
> - 클론 동기화 파일: data/{ws_collector.py, microstructure_universe.py, persistence.py}, scripts/{run_ws_collector.py, ws_health_report.py, run_ws_collector.bat.template}, config/settings.py, tests/{test_microstructure_universe.py, test_ws_collector.py}.
> - 클론 신규 .bat 파일: run_ws_collector.bat (tier1=15 tier2=30), run_ws_health.bat (--telegram).
>
> **자동 진행 중**: BinanceWSCollect 데몬이 매 logon 시 자동 시작, 23h 선제 재연결, 매일 09:00 헬스 리포트 → Telegram.
>
> ✅ **Microstructure Phase 1 완료(2026-05-24) — 데이터 레일 준비 끝, 알파 빌드 *없음* (의도된 보류).**
> 9 strategy fail (OHLCV+OI+funding+ML) 누적 결론: 우리 데이터에 추출 가능한 알파 없음. 운영자 critique:
> *"지금은 신호 찾기보다 실시간 마이크로구조 데이터 수집 체계 확보 및 검증"*. Phase 1 = 데이터 인프라만.
>
> **Phase 1-A (코드 + 스키마)**: Supabase MCP `apply_migration microstructure_tables` 으로 3 신규 테이블 생성 — `l2_book_snapshots`(1초 bucket, UNIQUE symbol+ts), `agg_trades`(UNIQUE symbol+agg_trade_id), `liquidations`(UNIQUE symbol+exchange_ts+side+price+qty). 모든 테이블 `received_ts` + `latency_ms` GENERATED column + (symbol,ts)/(ts) 인덱스. `data/ws_collector.py` async 3-stream (depth@100ms→1초 downsample / aggTrade tick-full / forceOrder@arr multiplex), batch upsert(1000 rows/5s), 자동 재연결(지수 backoff), max_queue_size=1000(기본 100 은 HYPE 등 overflow). 단위테스트 29건(파싱/dedup/batch/latency/lifecycle). 전체 657 passed.
>
> **Phase 1-B (smoke test 60s, BTC/ETH/SOL)**: l2=169 rows(3 syms × 57s), agg=1251 rows(BTC629/ETH486/SOL136), liq=0, reconnects=0, **avg latency 50.7ms** (good). UNIQUE 작동 (0 dup), latency_ms generated column 정상.
>
> **Phase 1-C (5min top-10 scale test)**: SOL/XAU/ZEC/XAG/CL/DOGE/LAB/HYPE/XRP/BZ universe → l2=2760, agg=12,765, **liq=10 (실 liquidation 캐치)**, reconnects=1(HYPE queue overflow 자동 회복 → max_queue_size 1000 으로 패치). HYPE liq notional avg $3558 BUY / $641 SELL — 실 트레이딩 사이즈 검증.
>
> **자동 진행 보류 (운영자 critique)**: 알파/ML/R0/진입 로직 빌드 *금지*. Phase 2 = 60-90일 데이터 누적 (운영자 시간 0, Windows Task 로 BinanceWSCollect 등록). Phase 3 = IC 분석 → Rule-based Layer 2 → R0 평가 (LightGBM 은 IC 입증 후만).
>
> 신규 모듈: `data/ws_collector.py`, `scripts/run_ws_collector.py`, `scripts/run_ws_collector.bat.template`, `tests/test_ws_collector.py`. aiohttp 기본 AsyncResolver 가 Win/Py3.14 에서 DNS 실패 → ThreadedResolver 패치 (script 시작 시 monkey-patch).
>
> Windows Task 등록: 운영자가 `scripts/run_ws_collector.bat.template` 을 `C:\bots\BINANCE_FUTURES_BOT\run_ws_collector.bat` 으로 복사 + 경로 수정 후 `Register-ScheduledTask -AtStartup -AllowStartIfOnBatteries ...` (BinanceOICollect 패턴 동일, .bat 안 주석에 정확한 명령 포함).
>
> 실거래 GO **HOLD** 무기한 유지. R5 도달 + 운영자 명시 승인 필수.
>
> 🟥 **ML EV Engine v2 R0 결과(2026-05-24, run_id `ml_ev_r0_20260524T101148Z`): FAIL — 9번째 시도 정직 종료.**
> 운영자 통찰 ("진짜 퀀트는 조건부 확률 추정 + EV 진입") 반영하여 binary classification (v1) 폐기 후 forward-return regression v2 채택. Ridge + LightGBM 듀얼 동시 학습, 41 피처(EMA·VWMA·BB·RSI·OI·funding·funding_remaining·BTC trend/corr·6-state regime·시간 cyclic·횡단면 percentile), 14mo train / 4mo val / 6mo OOS 엄격 분리, EV 진입(r_hat > cost 0.16% + 0.20% margin). 29 심볼 × 163k train obs / 106k OOS obs.
>
> **결과**: LGBM Spearman = **+0.0004** (~ random), Ridge Spearman = −0.0100. 즉 *모델이 forward return 에 대한 의미 있는 예측력 없음*. EV-진입 352 trades 가 발생했으나 cross-sectional 69% positive 임에도 **MDD 49.3%** + **단일 종목 39% 지배** → "trading PF 1.98 / Sharpe 4.14" 는 small subset 우연. 사전확정 10기준 중 6 PASS / 4 FAIL. Spearman 0.05 미달 (regression sanity FAIL) 이 핵심.
>
> 진단: 1.98M ohlcv 위 41 피처로도 1h alt 4h forward return 의 *조건부 평균*에 거의 정보 없음. 시장이 (a) 효율적이거나 (b) 우리 피처에 잡히지 않는 정보(L2 orderbook, 뉴스, 거래소 간 흐름)에 의해 움직이거나 (c) signal-to-noise 가 비용 0.16% RT 보다 낮음. 8 rule-based + 1 ML 모두 동일 결론으로 수렴 → 우리 봇 설정(소액·USDT-M·30s 폴링·OHLCV+OI+funding 만)의 *진입 결정* 알파는 데이터에 존재하지 않거나 추출 불가.
>
> **정직 종료 처리(9번째 시도, 계획서 §FAIL 시 규칙)**: target 변경·feature 추가·모델 변형 재시도 금지. 영구 자산 = 9 전략 검증 인프라 + ML 파이프라인(R0/R1 재사용 가능) + 안전 게이트(BTC risk-off). 실거래 GO **HOLD** 무기한.
> 신규 모듈: `strategy/indicators.py`(VWMA/BB/RSI), `analytics/regime_detector_v2.py`(6-state), `analytics/feature_engineering.py`(41 피처, 룩어헤드 차단 단위테스트), `analytics/ev_model.py`(Ridge+LightGBM wrapper + save/load), `analytics/ml_backtest.py`(R0/R1 러너), `scripts/run_ml_r0.py`(CLI). 단위테스트 55건 추가(전체 628 passed).
>
> 🟥 **Pair Stat-Arb v1 R0 결과(2026-05-24, run_id `pair_arb_r0_20260524T084130Z`): FAIL — 8번째 시도 정직 종료.**
> direction-neutral cointegrated pair mean-reversion 을 7 단일자산 방향성 실패와 *카테고리 자체* 다른 시도로 채택. Supabase MCP 로 top-50 거래대금 유니버스 산출(SOL, XAU, ZEC, XAG, CL, DOGE, LAB, HYPE, XRP, BZ + 40개) → 1,225 후보 페어 중 *7 페어만* cointegrated(p<0.01 + Hurst<0.4 + half_life<14d). 157 trades, PF 0.81, Sharpe −0.73, MDD 80.7%, 횡단면 양(+) 42.9%, **단일 페어 70.4% 지배**(XAU-XAG 가능). 사전확정 7기준 중 *6개 FAIL*(MDD sanity 1개만 vacuous PASS).
>
> 진단: 두 번째 실험은 (a) 알파벳 정렬 universe(저유동성 메메·신규상장 다수) → spurious cointegration 30 페어 → 99% MDD 단일 트레이드 폭락. 1차 fix 로 *최근 30일 거래대금 ranking* 으로 universe 교체 → 진짜 메이저는 7 페어만 cointegrated → 그것마저 net 손실. 본질: **크립토 메이저 perp 은 BTC 주도 상관 + 변동성으로 cointegration 깨짐, 2-leg 비용(0.33% round-trip)이 spread 변동을 압도**.
>
> 8 전략 모두 사전확정 FAIL → **알파 추구 액티브 매매 영구 중단** (실거래 GO HOLD 무기한). 운영자 통찰("실거래 가능 전략 있을 것")은 *우리 봇 설정(소액·USDT-M·테이커·30s 폴링)* 에서는 실현 불가. 영구 자산 = 8 전략군 검증 인프라 + BTC risk-off 안전 게이트.
> 모듈: `strategy/pair_selector.py`(공적분 스캐너), `strategy/pair_stat_arb.py`(시그널 상태머신), `analytics/pair_arb_backtest.py`(R0 러너), `scripts/run_pair_arb_r0.py`(CLI), 단위테스트 61건 추가(전체 573 passed). 이전 CCS-Lite v1.1 모듈도 영구 보존.
>
> 🟥 **CCS-Lite v1.1 R0 결과(2026-05-24, run_id `ccs_lite_r0_20260523T163501Z`): FAIL.**
> 작업본에서 5 모듈(sector_map, loss_cooldown, leverage_flush, ccs_lite, ccs_lite_backtest)
> 구현 + 단위테스트 83건 추가(전체 512 passed, 회귀 0). R0 자동 러너로 Supabase 데이터
> 평가 → `backtest_runs` 적재. 사전확정 기준 7개 중 6개 FAIL, 1개 PASS(BTC risk-off
> sanity, n=0). 핵심 메트릭: STRONG=0, NEUTRAL=0, BLOCKED=0, mean +4h=+0.000%.
>
> **FAIL 원인 = 데이터 sparseness**(전략 미스매치 아님): oi_history 가 12 심볼(비보호)
> × 23일분(2026-04-30~05-23) 뿐. 6-조건 AND base trigger 의 c1(OI ≤ −3%)이 OI 데이터
> 없는 99%+ 봉에서 NaN→False. 1.98M ohlcv 행은 풍부하나 OI 가 사실상 단기 sample.
> 진단 결과: OI=-1.5%·vol=2x·price=-1%·no_taker 등 *완화 임계*에서도 12 심볼 총 1 이벤트.
> v1.1 의 6-조건 자체는 *의도된 보수성*(false event 0 수렴)이며, R0 평가하려면:
>   - oi_history 2y × ≥30 심볼 backfill (Binance OI hist API 30일 한계 → 외부 데이터
>     소스 필요 또는 60+ 일 단순 누적 후 점진 평가)
>   - 또는 c1 임계 사전 *재고정*(데이터 마이닝 위험 — 권장 안 함)
>
> **정직 종료 처리(계획서 §FAIL 시 규칙)**: 데이터 충분성 미달도 미달이다. CCS-Full·V3
> 재시도, c1 임계 완화로의 *골대 옮기기* 모두 금지. 미래 OI 데이터 ≥2y×30 심볼 누적
> 시점에 동일 사전확정 R0 단일 평가만 허용. 그 전엔 실거래 GO **HOLD** 유지.
> 영구 자산 = 검증 인프라 + CCS-Lite v1.1 5 모듈 + 단위테스트(재실행 가능).

## Context
v3.1.2 봇의 실거래 투입 전 안정화 작업을 다회 세션에 걸쳐 진행 중. 최근 세션에서
실거래 중단급 수정→algoOrder 전환→안전장치→소액 라이브 연결검증→전략 빈도 진단→
검증 인프라 구축까지 완료. 핵심 결론: **전략(OI-급증)이 현 임계로는 사실상 무거래이며,
검증에 필요한 OI 데이터가 부족(~30일)해 실거래 GO는 HOLD.** 본 문서는 컨텍스트를
지워도 다음 세션이 즉시 따라잡도록 전체 상태·관례·남은 일·재개 프롬프트를 담는다.

---

## 1. 저장소 / 환경 (중요 — 작업 메커니즘)
- **작업본**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT` — **git 저장소가 아님**(여기서 코드 수정·실행).
- **git 저장소(클론)**: `C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT_GITHUB` — origin = `kong9365/BINANCE_FUTURES_BOT`.
- **수집 전용 클론**: `C:\bots\BINANCE_FUTURES_BOT` (2026-05-21 추가, OneDrive 밖 fresh clone + `.env` 복사 + 캐시 이관). 무인 OI 수집 스케줄 작업(`BinanceOICollect`)이 여기서 실행됨. 코드 수정은 여전히 OneDrive 작업본에서.
- **GitHub 반영 = "A안"**: 작업본에서 변경 파일을 클론으로 **명시 복사** → 클론에서 보안체크 → `pytest` → `git add <파일명 명시>`(`git add .`/`-A` 금지) → 커밋 → push.
  - 커밋은 git 신원 미설정이라 `git -c user.name="kong9365" -c user.email="jaehong9365@gmail.com" commit ...` 1회 오버라이드 사용.
  - 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **현재 GitHub HEAD = `018b109`**, 전체 테스트 **367 passed**. (C:\bots 클론도 동일 HEAD로 pull됨.)
- **Supabase MCP 커넥터 연동됨**: 프로젝트 `aoeqgptytzuhvvchsgul`("Binance", ap-southeast-1). 스키마/검증은 MCP로 관리, 봇 런타임은 `data/persistence.py`가 service_role 키로 접속.
- **절대 커밋 금지**: `.env`, `.env.SAFETY_BACKUP`, `logs/`, `reports/`, `data/*.db`, `backtests/cache/`, 실제 키/토큰. (모두 .gitignore 확인됨.)

## 2. .env 상태 (시크릿 값은 절대 출력 금지)
- `USE_TESTNET=true` (안전 기본 — 실거래는 실행 시 커맨드라인 `USE_TESTNET=false` 오버라이드만; `.env`는 true 유지).
- `DB_PATH` **주석 처리됨**(미지정) → `USE_TESTNET=false` 시 `data/bot_live.db`, true 시 `data/bot.db` 자동 분리.
- live 키(`BINANCE_API_KEY/SECRET`) + testnet 키(`BINANCE_TESTNET_*`) + `TELEGRAM_*` + `OPENAI_API_KEY` + `CMC_API_KEY` 모두 설정됨. live/testnet 키는 별개 발급처, `collector._build_client`가 `USE_TESTNET`로 자동 선택.
- 보호종목 6: **BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT** (`config/settings.py` `_DEFAULT_PROTECTED_SYMBOLS`, env `PROTECTED_SYMBOLS` 우선).
- **Supabase**(2026-05-21 추가): `SUPABASE_URL=https://aoeqgptytzuhvvchsgul.supabase.co` + `SUPABASE_SERVICE_ROLE_KEY`(운영자 입력, 미커밋). 작업본·C:\bots 양쪽 .env에 설정됨.
- **`ACTIVE_STRATEGY`**(선택): 미설정/`oi_surge`=기존, `breakout`=Donchian/ATR 돌파. 라이브 전환은 운영자 명시 시만.

## 3. 절대 룰 (CLAUDE.md TIER 1 + 본 세션 합의)
- 키/시크릿/Telegram 토큰을 코드·로그·메시지·보고서에 **출력 금지**. (httpx/httpcore INFO 억제 적용 — `main_7590._suppress_http_client_logs`.)
- 보호종목 6개: **신규 진입 금지 + 기존 보유분 청산/취소/수정 금지**(emergency close·reconcile에서도 제외). `close_position`이 보호종목이면 `protected_symbol_close_blocked` 반환.
- 게이트(RiskManager/CostGuard/PairWhitelist/CapitalManager) **완화 금지**.
- 실거래 실행 전 **live read-only preflight 게이트**: 비보호종목 기존 포지션/주문/algo = 0, One-way Mode, 예산 cap. 하나라도 위반 시 차단.
- 신규 거래 예산 = `LIVE_PROBE_CONFIG.live_probe_budget_usdt`(기본 $300) cap.
- **실거래 GO = HOLD** (해제 조건은 §6).

## 4. 완료된 작업 (커밋별)
| 커밋 | 내용 |
|---|---|
| `d46790b` | algoOrder 전환(보호주문 STOP/TP를 `futures_create_algo_order`로 — 2025-12-09 -4120 대응) + STOP 갱신 create-first + A-5 고아 포지션 방지 + A-4 Hedge Mode 차단 |
| `afc38df` | httpx/httpcore INFO 억제(Telegram 토큰 URL 로그 유출 차단) |
| `8096fc6` | 보호종목 INJUSDT 추가(6개) + 기본 차단 테스트 |
| `eeeaed6` | `docs/LIVE_CONNECTION_PROBE_PLAN.md` |
| `daced7c` | **Protected Existing Position Coexist Mode**(기존 보호종목 보유분과 공존, 비보호만 거래) + live probe 예산 cap |
| `2c13c11` | Telegram 알림 강화(시작/preflight/heartbeat(1h)/진입/보호주문/청산/CRITICAL) |
| `9896eea` | 보호종목 변경 알림 CRITICAL→정보성(운영자 자신의 stop은 정상) + 상장폐지(-4108) 스캔 노이즈 제거(skip-set) |
| `aabe47f` | **OIScanner 윈도우 교정**: OI 변화를 30초(스캔간격) 대신 OI 이력 룩백(기본 15m)으로 측정 + `OIScannerConfig` |
| `92b1ad3` | **BacktestEngine `strategy="oi_surge"`**(라이브 OIScanner 재현) + `backtesting/data_loader.py`(OI+OHLCV 캐시/누적) |
| `48b284a` | `backtesting/collect_oi.py` OI 누적 수집 CLI + `docs/OI_COLLECTION.md` |
| `c48cf3d` | OneDrive 경로가 스케줄 실행을 막는다는 caveat 문서화 |
| `4f5760e` | HANDOFF.md 갱신(안정화 상태) |
| `67200ca` | **수집 무인실행 진짜 원인 = 배터리 설정**(OneDrive 아님) 규명·수정 + C:\bots 이전 |
| `64bec37` | **전략 현실성 분석 보고서**(`docs/STRATEGY_REALISM_REVIEW.md`) — OI-급증 음의 기대값·역선택 정량 입증 + 단계별 개선계획 |
| `26e50cc` | **P0 Supabase 영속 계층** — 19테이블(RLS) + `data/persistence.py`(주저장+sqlite 폴백 큐) |
| `d673e13` | **P1 Donchian/ATR 돌파 전략**(`strategy/breakout.py`, 룩어헤드 안전, 라이브/백테스트 공유) + `BacktestEngine strategy="breakout"` |
| `d908292` | **P2 라이브 통합** — `ACTIVE_STRATEGY` 선택자 + `_scan_breakout` + 게이트 공유 `_execute_decision`(oi_surge/breakout 동일 게이트) |
| `c259f03` | **P4 데이터 누적** — collect_oi가 OHLCV/OI/펀딩을 Supabase에 멱등 적재(배치 upsert, 최근48봉/`--backfill`) |
| `0769ce3` | BTC 시장-인덱스 + CMC 제안 평가(§6) + 단계 P5 추가 |
| `6cec59b` | **P5a 시장-컨텍스트** — BTC/ETH 인덱스 OHLCV + CMC 글로벌(도미넌스/총시총/F&G) → `market_global` |
| `018b109` | **#2 돌파 청산 정교화** — ATR 샹들리에 트레일(opt-in, **기본 off** — 21일 표본서 열세, P4서 재검토) |

## 5. 핵심 발견 (전략·검증)
- **현 기본 임계(OI≥5% / 가격≥2%)로는 사실상 영원히 무거래** — 실데이터 측정으로 입증:
  - 15분 창: 5,988관측 0신호. 15분 |OI변화| 최댓값 4.50% < 5% 임계 → 수학적으로 거의 불가.
  - 1시간 창: 0신호(기본 임계). 재보정(1h, OI≥1.5%/가격≥0.7%) 시 ~14거래/20.8일 나오나 **표본이 노이즈 수준**(임계마다 부호 뒤집힘) → 수익성 판단 불가.
- 윈도우(30초→15m)는 고쳤으나 **임계도 비현실적**이었음(2차 병목).
- **검증 불가의 근본 원인 = 데이터**: Binance OI 이력 `futures_open_interest_hist` **~30일만** 제공. 명세 §10-4(거래 ≥200건, 다년 OOS) 충족 불가.
- 기존 BacktestEngine은 추세추종 플레이스홀더였음 → `strategy="oi_surge"` 추가로 라이브 전략 백테스트 가능해짐(단 데이터가 30일뿐).
- 라이브 소액 연결검증(30분 + 14h19m): **무오류·무누출·진입 0건**(연결/DB분리/보호종목 공존/예산 cap/토큰억제 모두 정상). INJUSDT는 운영자 자신의 stop으로 청산됨(정상, 봇 무관).

## 6. 현재 라이브 계좌 상태 (참고)
- One-way Mode. 기존 보유: LYNUSDT 포지션 + ETHUSDT 주문(모두 보호종목, 봇 비접근). INJUSDT는 stop으로 청산됨.
- wallet ≈ $3,419 / available ≈ $1,886 (예산 cap $300이 신규 거래 노출 제한).
- 비보호종목 기존 항목 0. preflight 통과 가능 상태.

## 7. 미해결 / 남은 작업 (우선순위)
1. ✅ **(완료, 2026-05-21) 무인 OI 수집 정상화.** 진단 정정: 무인 실행 실패의 **진짜 원인은 OneDrive가 아니라** Windows 작업 기본 설정 `DisallowStartIfOnBatteries=True`(노트북 배터리 전원이면 미실행, schtasks는 Result 0 보고)였음. 조치: 수집 전용 클론을 `C:\bots\BINANCE_FUTURES_BOT`로 이전 + 작업을 `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable`로 재등록 → 스케줄 트리거 시 실제 수집(로그 새 줄·캐시 갱신) 검증 완료. 상세는 `docs/OI_COLLECTION.md`.
2. **OI 데이터 수개월 누적** (`python -m backtesting.collect_oi` 시간별) → 충분(거래 ≥200건 분량) 시 **oi_surge walk-forward 검증**.
3. **임계/윈도우 재보정** — 검증 데이터 확보 후. 현재 후보: 1h 창 + OI 1~2% + 가격 0.5~1%(단 노이즈, 데이터 더 필요).
4. **백테스트 방향 로직 정합** — `_evaluate_oi_surge`는 가격 모멘텀 부호로 방향 결정(라이브의 QualityGate/regime 필터 미반영) → 데이터 축적 후 정합.
5. ✅ **(완료) 스케줄 작업 정리** — `BinanceOICollect`를 `C:\bots\BINANCE_FUTURES_BOT\run_collect_oi.bat` 경로 + 배터리 허용 설정으로 재등록(구 OneDrive 작업 삭제). `run_collect_oi.bat`는 머신특정·미커밋 유지.
6. **Testnet 전체 생애주기 실관측** (자연 STOP/TP 트리거→reconcile→DB CLOSED) — 라이브 GO 전 권장.
7. **전략 근본 판단**: OI-급증이 데이터/인프라상 검증 가능한지, 아니면 검증 가능한 다른 신호로 전환할지 결정(데이터 누적 결과 보고 판단).

## 8. 검증 명령 (재개 시 상태 확인)
```bash
# (클론에서) 동기화·테스트 상태
cd <repo>; git log --oneline -3            # HEAD c48cf3d 또는 이후
python -m compileall .                     # 에러 0
pytest tests/                              # 332 passed (또는 그 이상)
# OI 신호 빈도/임계 재측정(read-only, 라이브 OI 이력)
#   → 이전 결과: 기본 임계 0신호; 1h+1.5%/0.7%만 노이즈 수준 양(+)
# OI 누적 수집 1회(직접 실행은 OneDrive에서도 정상)
python -m backtesting.collect_oi --limit 500
# oi_surge 백테스트(누적 데이터로)
#   backtesting.data_loader.load_universe + BacktestEngine(strategy="oi_surge")
```
- 보호종목 확인: `python -c "from dotenv import load_dotenv;load_dotenv();from config.settings import PAIR_WHITELIST_CONFIG as p;print(sorted(p.protected_symbols))"` → 6개.
- 실거래 실행은 **금지(HOLD)**. live read-only 점검만(주문 없음).

## 9. 핵심 파일 맵
- `data/oi_scanner.py` — OIScanner(OI 이력 룩백, `oi_lookback_period`/`oi_lookback_count`).
- `data/collector.py` — `get_open_interest_history` + 상장폐지 skip-set(`_delisted_symbols`).
- `trading/executor.py` — algoOrder 보호주문, A-5 `_resolve_fill_timeout`/`_position_state`, A-4 `_ensure_one_way_mode`, 보호종목 `close_position` 가드, preflight 헬퍼.
- `main_7590.py` — `_live_account_preflight`, `_close_all_positions_safe`(보호 제외), Telegram 알림, `_check_protected_unchanged`(정보성), 예산 cap, `_suppress_http_client_logs`.
- `config/settings.py` — `TradeExecutorConfig`/`LiveProbeConfig`/`OIScannerConfig` + 보호종목.
- `backtesting/backtest_engine.py` — `strategy="oi_surge"` `_evaluate_oi_surge`(룩어헤드 차단).
- `backtesting/data_loader.py` / `backtesting/collect_oi.py` — OI+OHLCV 수집/누적.
- `docs/OI_COLLECTION.md` / `docs/LIVE_CONNECTION_PROBE_PLAN.md` — 운영 가이드.

## 10. 다음 세션 재개 프롬프트 (복사용)
> 아래를 새 세션 첫 메시지로 사용. (CLAUDE.md는 자동 로드됨.)

```
이 프로젝트(Binance Futures Bot, C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT)의
이전 세션을 이어서 진행한다. 먼저 docs/HANDOFF.md(특히 §11)와 docs/STRATEGY_REALISM_REVIEW.md,
docs/OI_COLLECTION.md를 읽고 현재 상태를 따라잡아라. Supabase는 MCP 커넥터로 연동돼 있다.

핵심 컨텍스트: GitHub main HEAD는 018b109 이후, pytest 367+ passed, 실거래 GO는 HOLD.
전략을 OI-급증(음의 기대값) → Donchian/ATR 돌파로 교체했고, Supabase 영속 + 시장데이터
누적(ohlcv/oi/funding/index/market_global)까지 완료해 "데이터 누적 대기" 상태다.
작업 메커니즘은 §1 "A안"(클론 복사→테스트→명시 add→commit override→push), 보안·보호종목·
HOLD 룰은 §3 절대 준수.

지금 할 일: [여기에 — 예: "(1) §11의 데이터 충분성 체크 실행 후 결과 보고" / "(2) 충분하면
돌파 walk-forward 검증" / "(3) P5b BTC risk-off 게이트 백테스트" / "(4) P3 펀딩 페이드 설계" 중 택1].
실거래 실행/주문은 금지(read-only 점검만), 코드 변경은 plan 승인 후.
```

---

## 11. 전략 개편 세션 (2026-05-21) 요약 & 데이터-대기 재개 가이드

### 무엇을 했나 (이번 세션)
- **현 전략 OI-급증을 정량 기각**: 캐시 20일·6,012봉에서 64개 파라미터 조합 전부 음의 기대값(PF<1).
  역선택 측정: 신호 방향 forward return이 +1~3시간만 +0.15%였다가 6시간 내 소멸/반전 → 비용
  0.26%보다 작음. **재보정이 아니라 교체 대상**으로 결론.
- **전략 리서치 후 채택**: 레짐 전환형 — 추세장 **Donchian/ATR 돌파**(채택·구현 완료), 횡보장
  **펀딩 극단 페이드**(P3, 미구현). 둘 다 보유 데이터로 백테스트 가능·스팟 레그 불필요.
- **P0~P2, P4, P5a, #2 완료**(커밋 §4 표). 돌파 전략 라이브 경로 연결(HOLD), Supabase 주저장,
  OHLCV/OI/펀딩 + BTC/ETH 인덱스 + CMC 글로벌 시간별 자동 누적.
- **돌파 백테스트(20일)**: 승률 ~46%·expectancy_R +0.11·PF 0.82~1.06(저승률·고R) — OI-급증의
  질적 반전. **단 13~19거래/단일레짐 = 통계적 검증 아님.**

### 왜 대기하나
돌파 walk-forward(거래≥200, OOS), P5b BTC 게이트, P3 펀딩 페이드는 **모두 수개월 다레짐 데이터가
있어야 검증 가능**. 21일 표본으론 결론이 노이즈(트레일 청산 #2가 그 예 — 짧은 표본서 오히려 열세).
그래서 검증 안 된 기능을 더 쌓지 않고, **자동 누적이 충분해질 때까지 대기**한다.

### 자동으로 일어나는 일
- Windows 작업 `BinanceOICollect`(C:\bots, 배터리 허용 설정)가 **매시간** collect_oi 실행 →
  Supabase `ohlcv`/`oi_history`/`funding_history`/`market_global`에 멱등 누적.

### 재개 시 — 데이터 충분성 체크 (Supabase MCP `execute_sql`, project `aoeqgptytzuhvvchsgul`)
```sql
select
  (select count(*) from ohlcv) as ohlcv_rows,
  (select min(ts)::date from ohlcv) as data_from,
  (select max(ts)::date from ohlcv) as data_to,
  (select max(ts)::date - min(ts)::date from ohlcv) as span_days,
  (select count(*) from funding_history) as funding_rows,
  (select count(*) from market_global) as market_global_rows;
```
- **충분 기준(권장)**: span_days ≥ ~120일(다레짐 포함) 그리고 돌파 백테스트 거래수 ≥ 200.
  그 전엔 walk-forward 결과를 신뢰하지 말 것(§10-4).

### 데이터가 충분해지면 — 우선순위(확정)
1. **돌파 walk-forward 검증** — `data_loader.load_universe` + `BacktestEngine(strategy="breakout")`,
   `backtest_runs` 기준선(`p1_breakout_baseline_20260521`) 대비. 통과(OOS Sharpe≥1.0/PF≥1.2/MDD<20%)
   못하면 임계/룩백 재튜닝 또는 전략 재검토.
2. **#2 트레일 재평가** — `breakout_trail_exit` on/off 다레짐 비교(현재 기본 off).
3. **P5b BTC risk-off 게이트** — BTC(인덱스) 추세 하락 시 알트 LONG 차단, on/off 백테스트로
   expectancy↑·MDD↓ 입증 시만 채택.
4. **P3 펀딩 극단 페이드** — `funding_history` percentile 극단 + OI 롤오버 확인 역추세(횡보 게이트).
5. 통과 후에만 **실거래 GO 재검토**(여전히 운영자 최종 승인 + testnet 생애주기 관측 선행).

### 핵심 신규 파일/위치
- `data/persistence.py`(Supabase 주저장+폴백), `strategy/breakout.py`(돌파 신호·지표),
  `backtesting/collect_oi.py`(누적+Supabase 적재), `data/cmc_client.get_global_metrics`,
  `main_7590._scan_breakout`/`_execute_decision`(공유 게이트), Supabase `market_global` 테이블.
- 회귀 기준선은 Supabase `backtest_runs` 테이블(run_id로 조회).
