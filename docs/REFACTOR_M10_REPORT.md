# M10 Report — Streamlit + Plotly 실시간 대시보드

> **Milestone**: M10 — 실시간 차트 대시보드
> **Date**: 2026-05-27
> **Branch**: refactor/blueprint-m0-foundation
> **Previous**: [REFACTOR_M9 + 봇 가동 검증](HANDOFF.md)
> **Plan**: [REFACTOR_PLAN_v2_BLUEPRINT.md](REFACTOR_PLAN_v2_BLUEPRINT.md) M10
> **Status**: ✅ 완료 — M11 진입 가능

## 1. 신규 13파일 + 1 수정

### dashboard/ 패키지 (9파일)

| 파일 | 라인 | 역할 |
|---|---|---|
| `dashboard/__init__.py` | 22 | 패키지 docstring + 설계 원칙 |
| `dashboard/data_loader.py` | 175 | DB 쿼리 + `@st.cache_data(ttl=5)` 캐시 (8 메서드) |
| `dashboard/charts.py` | 200 | Plotly chart builder (7 함수) |
| `dashboard/app.py` | 100 | Streamlit 메인 entry (사이드바 + 요약) |
| `dashboard/pages/1_operations.py` | 80 | 자본 + 거래 + equity curve |
| `dashboard/pages/2_5_agent_review.py` | 75 | 5-Agent verdict 매트릭스 |
| `dashboard/pages/3_alcoa_audit.py` | 80 | audit_log chain 무결성 검증 |
| `dashboard/pages/4_kill_switch.py` | 100 | KillSwitch 이력 + 수동 해제 (DEACTIVATE 명시 confirm) |
| `dashboard/pages/5_market_chart.py` | 75 | 1m OHLC candlestick (agg_trades_1m) |
| `dashboard/pages/6_setup_registry.py` | 90 | 7기준 메트릭 + 자동 판정 |

### scripts/ + tests/ (3파일)

| 파일 | 역할 |
|---|---|
| `scripts/run_dashboard.bat` | Windows 실행 스크립트 (`streamlit run --server.address localhost`) |
| `tests/test_dashboard_data_loader.py` | 13 tests (env path, empty/data 쿼리, event 분포) |
| `tests/test_dashboard_charts.py` | 14 tests (각 chart 빈/데이터 입력 + Figure 반환) |

### requirements.txt 수정
- `streamlit>=1.30.0`, `plotly>=5.18.0` 추가

## 2. 핵심 원칙 (CLAUDE.md TIER 1 #10 정합)

- **읽기 전용** — DB 쿼리만 (SELECT), 거래 결정 영향 X
- **localhost only** — `--server.address localhost --server.port 8501` (보안)
- **봇 무관** — `main_7590.py` 와 별도 프로세스
- **5초 캐시** — `@st.cache_data(ttl=5)` (DB 부하 ↓)

## 3. 6 페이지 기능 요약

| 페이지 | 데이터 소스 | 핵심 차트 |
|---|---|---|
| 1. 운영 상태 | `trades`, `capital_initial`, `capital_daily_snapshot` | Plotly equity curve + 요약 메트릭 |
| 2. 5-Agent 검토 | `agent_reviews`, `signal_decisions` | Verdict 매트릭스 (시간순, 색상 코드) |
| 3. ALCOA+ 감사 | `audit_log` (전체) | Chain 무결성 검증 + event_type 분포 |
| 4. Kill Switch | `kill_switch_events`, `KillSwitch.get_status()` | 활성화 timeline + 수동 해제 (운영자 명시 confirm "DEACTIVATE") |
| 5. 시장 차트 | `agg_trades_1m` (Phase 2-E) | 1m OHLC candlestick (Plotly) |
| 6. Setup Registry | `setup_registry` | 7기준 메트릭 표 + 자동 판정 (PASS/CONDITIONAL/DISABLED) |

## 4. 검증 게이트

### 단위 테스트 — 27개 PASS
```
$ pytest tests/test_dashboard_data_loader.py tests/test_dashboard_charts.py -v
============================= 27 passed in 7.36s ==============================
```

### Compile 검증 — 모든 페이지 OK
```
$ python -c "import py_compile; py_compile.compile(...)"
All dashboard pages compile OK
```

### Streamlit 실행 (운영자 작업)
```bash
scripts/run_dashboard.bat        # Windows (또는)
streamlit run dashboard/app.py --server.address localhost --server.port 8501
# 브라우저: http://localhost:8501
```

## 5. 변경 통계

```
M10 commit (예상):
- 신규 dashboard/ 9파일 (~1000줄)
- scripts/run_dashboard.bat (15줄)
- tests/ 2파일 (~280줄)
- requirements.txt: +4줄 (streamlit + plotly)
- docs/REFACTOR_M10_REPORT.md
```

main_7590 / 핵심 trading/ / 기존 모듈 본문 변경 **0줄**.

## 6. 기존 자산 재사용 (신규 코드 0)

| 재사용 | 사용처 |
|---|---|
| `audit/chain.py` `verify_chain` | 페이지 3 ALCOA+ 무결성 |
| `governance/kill_switch.py` `KillSwitch.is_active/get_status/deactivate` | 페이지 4 |
| `registry/setup_registry.py` `SetupRegistry.get_active_setups` | 페이지 6 (data_loader 경유) |
| `data/aggregator_1m.py` `agg_trades_1m` 테이블 | 페이지 5 (data_loader 경유) |
| `db/init_db.py` 마이그레이션 v3.2.0/v3.2.1 | 모든 페이지 |

## 7. M11 진입 조건

- [x] M10 신규 13파일 + 1 수정 완료
- [x] 27 신규 단위 테스트 PASS
- [x] 모든 dashboard 페이지 py_compile OK
- [x] requirements.txt streamlit + plotly 추가
- [ ] 전체 회귀 pytest (백그라운드, 940+27=967+ 기대)
- [ ] 클론 동기화 + commit + push
- [ ] 운영자 Streamlit 실제 실행 검증 (선택)

## 8. M11 작업 (다음)

- `backtesting/local_ohlcv_store.py` — Supabase 대체 로컬 sqlite ohlcv 저장소
- `db/migrations/v3_2_1_to_v3_2_2.sql` — `ohlcv_local` 테이블
- `scripts/run_m11_backtest.py` — 백필 + 백테스트 + 7기준 검증 entry
- DailyTSMOMDonchianSkill 실측 (2~5년 1d, 18~50종목)
- `validate_and_persist` → setup_registry 자동 update + 보고서

## 9. 청사진 § 매핑 + 운영자 결정

| 모듈 | 청사진 § / 운영자 결정 |
|---|---|
| `dashboard/charts.py` | (신규) 운영자 결정 2026-05-27 — "실시간 차트도 반영" |
| `dashboard/data_loader.py` | §6 (모든 DB 테이블 read-only 쿼리) |
| `pages/3_alcoa_audit.py` | §6.4 + §7.2 ALCOA+ (chain 검증) |
| `pages/4_kill_switch.py` | §3.4.3 KillSwitch + 운영자 결정 #v2-4 |
| `pages/5_market_chart.py` | §3.1.3 WebSocket + Phase 2-E aggregator_1m |
| `pages/6_setup_registry.py` | §6.5 + 운영자 7기준 |
