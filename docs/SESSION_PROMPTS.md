# Claude Code 세션 프롬프트 — v3.1.1 봇 구현

> **v3.1 → v3.1.1 변경**: 세션 2.5 신설 (CapitalManager + PairWhitelist 보호 종목), 세션 9·12 일부 수정.
> **사용법**: 각 세션마다 해당 프롬프트를 **그대로 복사해서** Claude Code에 붙여넣기.
> 한 세션 끝나면 **반드시 `/clear`** 후 다음 세션 시작.

---

## 세션 0 — 환경 검증 (1회만)

```
docs/SPEC_v3.1.md, docs/SPEC_v3.1_APPENDIX_E.md, CLAUDE.md를 view하고, 다음을 확인해줘:

1. 프로젝트 구조가 명세서 §부록 D-5와 일치하는가
2. requirements.txt의 모든 패키지가 설치되어 있는가
   - 설치되지 않은 게 있으면 `pip install -r requirements.txt` 실행
3. .env 파일이 존재하고 필수 키가 있는가
   - 없으면 .env.example을 복사하고 사용자에게 안내
4. Python 버전이 3.10 이상인가
5. pytest로 빈 테스트 디렉토리가 인식되는가 (`pytest tests/ --collect-only`)
6. ★ v3.1.1: docs/SPEC_v3.1_APPENDIX_E.md가 존재하는가 (보호 자산 정책)

결과를 표로 정리해서 보여주고, 문제 있는 항목이 있으면 해결책을 제시해줘.
코드는 아직 작성하지 마. 검증만.
```

---

## 세션 1 — DB 스키마 + 마이그레이션 (v3.1.1)

```
docs/SPEC_v3.1.md §13과 docs/SPEC_v3.1_APPENDIX_E.md §E-7을 view해서 DB 스키마를 작성해줘.

작업:
1. db/schema.sql — 명세서 §13-1, §13-2의 모든 CREATE TABLE
   + ★ v3.1.1: 부록 E-7-2, E-7-3의 신규 테이블 (capital_initial, capital_daily_snapshot)
2. db/migrations/v3_0_to_v3_1.sql — 명세서 §13-3
3. db/migrations/v3_1_to_v3_1_1.sql — ★ 부록 E-7-4
4. db/init_db.py — schema.sql 실행 + 마이그레이션 적용
5. tests/test_db_init.py — 모든 테이블 존재 검증
   + ★ trades 테이블의 v3.1.1 신규 컬럼 검증
     (wallet_balance_at_entry, available_at_entry, locked_margin_at_entry)

규칙:
- 명세서 §13과 부록 E-7의 모든 컬럼·인덱스 누락 금지
- v3.1.1 신규 컬럼은 ALTER TABLE 마이그레이션으로 추가 (재실행 안전)
- 작업 전 plan만 보여주고 내 승인 후 진행

완료 후:
- `python db/init_db.py` 실행 → data/bot.db 생성, schema_migrations에 v3.1, v3.1.1 등록
- `pytest tests/test_db_init.py` 통과
- 사용자에게 "/clear 후 세션 2로 넘어가도 됨" 안내
```

---

## 세션 2 — Config (settings.py + macro_events.yaml) (v3.1.1)

```
docs/SPEC_v3.1.md §14와 docs/SPEC_v3.1_APPENDIX_E.md §E-6을 view해서 Config을 작성해줘.

작업:
1. config/settings.py — 명세서 §14-1의 모든 dataclass
   - SystemConfig, RegimeConfig, CostGuardConfig, SizingConfig
   - RegimeTradingParams (5개 레짐 instance)
   - RiskRules, WeeklyAnalystConfig, MacroEventConfig
   - HealthMonitorConfig, PairWhitelistConfig, BacktestConfig
   + ★ v3.1.1: CapitalManagerConfig (부록 E-6-1)
   + ★ v3.1.1: PairWhitelistConfig.protected_symbols 추가 (부록 E-6-2)
   - 모든 인스턴스(SYSTEM_CONFIG, ..., CAPITAL_MANAGER_CONFIG) export
2. config/macro_events.yaml — 명세서 §8-5-3 예시 + 향후 90일 주요 이벤트 3~5개
3. config/__init__.py — 모든 *_CONFIG export
4. tests/test_settings.py — 모든 dataclass 기본값 검증
   + ★ v3.1.1: protected_symbols = ["BTCUSDT", "ETHUSDT", "HOLOUSDT"] 확인

규칙:
- 명세서 §14와 부록 E-6의 모든 파라미터 누락 금지
- field 기본값 중 dict/list는 field(default_factory=...) 사용
- macro_events.yaml 이벤트는 web search로 향후 90일 일정 확인하여 작성

완료 후:
- `python -c "from config.settings import PAIR_WHITELIST_CONFIG; print(PAIR_WHITELIST_CONFIG.protected_symbols)"` 실행
  → ['BTCUSDT', 'ETHUSDT', 'HOLOUSDT'] 출력 확인
- `pytest tests/test_settings.py` 통과
```

---

## 세션 2.5 ★ NEW — CapitalManager + PairWhitelist 보호 종목 통합 (v3.1.1)

```
docs/SPEC_v3.1_APPENDIX_E.md §E-2와 §E-3을 view해서 다음 두 모듈을 작성해줘.
PairWhitelist는 기존 명세서 §8-7에도 정의돼 있으므로 §8-7도 함께 view.

작업 ① CapitalManager 신규 작성:
1. data/capital_manager.py — 부록 E-2-3 코드 기반
   - @dataclass CapitalSnapshot (margin_utilization_pct 프로퍼티 포함)
   - get_snapshot(force_refresh) 비동기 메서드
   - 캐시 (10초 TTL)
   - 초기 자본 기록 (_initial_wallet_balance)
   - 일일 시작 잔고 기록 (_daily_start_wallet_balance)
   - get_spot_balance() → ValueError (Spot 호출 차단)
2. tests/test_capital_manager.py — 부록 E-2-4의 8개 시나리오 모두
   - binance_client을 unittest.mock.MagicMock으로 mock
   - futures_account() 반환 형식:
     {"totalWalletBalance": "1000", "totalMarginBalance": "1030",
      "availableBalance": "970", "totalPositionInitialMargin": "30",
      "totalUnrealizedProfit": "30"}

작업 ② PairWhitelist 보호 종목 통합:
3. strategy/pair_whitelist.py — 부록 E-3-2 변경된 코드
   - 명세서 §8-7-2 기존 코드 기반으로 다음 변경:
     · __init__에 protected_symbols 파라미터 추가
     · 초기 등록 시 protected_symbols은 tier=0으로 설정
     · is_allowed() 최우선 차단
     · refresh() 보호 종목 검증 스킵
     · manual_unblock() 보호 종목 거부
4. tests/test_pair_whitelist.py — 기존 7개 + 부록 E-3-3의 4개 추가
   - 시나리오 8: protected_symbols=["BTCUSDT"] → is_allowed("BTCUSDT") == False
   - 시나리오 9: refresh() 후에도 차단 유지
   - 시나리오 10: manual_unblock() 거부 (경고 로그만)
   - 시나리오 11: get_active() 결과에 BTCUSDT 미포함

규칙:
- 부록 E-2와 E-3의 코드를 정확히 옮기되, docstring·logger 보강
- protected_symbols 룰이 다른 모든 룰보다 우선
- 작업 전 plan을 매우 상세하게 (두 모듈이므로) 보여주고 내 승인 후 진행

검증:
- pytest tests/test_capital_manager.py -v → 8개 통과
- pytest tests/test_pair_whitelist.py -v → 11개 통과 (기존 7 + 신규 4)
- 통합 테스트:
  ```python
  from strategy.pair_whitelist import PairWhitelist
  from unittest.mock import MagicMock
  pw = PairWhitelist(MagicMock(), protected_symbols=["BTCUSDT"])
  assert pw.is_allowed("BTCUSDT", capital=10000) == False
  assert pw.is_allowed("SOLUSDT", capital=10000) == True
  print("OK")
  ```

완료 후:
- 두 모듈의 import가 가능한지 확인
- docs/MODULE_CHECKLIST.md에 두 모듈 체크
- /clear 안내
```

---

## 세션 3 — CostGuard (의존성 0, 기존 유지)

```
docs/SPEC_v3.1.md §8-2를 view해서 CostGuard를 작성해줘.

(이하 기존 세션 3 프롬프트 그대로, 변경 없음)
... [기존 v3.1 세션 3 프롬프트 유지] ...
```

세션 3의 상세 내용은 기존 SESSION_PROMPTS.md(v3.1)의 세션 3과 동일.

---

## 세션 4 — DynamicPositionSizer (의존성 0, 기존 유지)

```
docs/SPEC_v3.1.md §8-3을 view해서 DynamicPositionSizer를 작성해줘.

(이하 기존 세션 4 프롬프트 그대로)
```

---

## 세션 5 — RegimeDetector (의존성 0, 기존 유지)

```
docs/SPEC_v3.1.md §8-1을 view해서 RegimeDetector를 작성해줘.

(이하 기존 세션 5 프롬프트 그대로)
```

---

## 세션 6 — MacroEventAnalyzer (기존 유지)

```
docs/SPEC_v3.1.md §8-5를 view해서 MacroEventAnalyzer를 작성해줘.

(이하 기존 세션 6 프롬프트 그대로)
```

---

## 세션 7 — SystemHealthMonitor (기존 유지)

```
docs/SPEC_v3.1.md §8-6을 view해서 SystemHealthMonitor를 작성해줘.

(이하 기존 세션 7 프롬프트 그대로)
```

---

## 세션 8 — PairWhitelist (★ 세션 2.5에서 이미 작성됨, 스킵)

```
세션 2.5에서 PairWhitelist를 이미 작성했습니다.
v3.1 SESSION_PROMPTS.md의 세션 8은 v3.1.1에서 세션 2.5로 이동되었습니다.
이 세션은 건너뛰고 세션 9로 진행하세요.
```

---

## 세션 9 — RiskManager (v3.1.1 강화)

```
docs/SPEC_v3.1.md §9와 docs/SPEC_v3.1_APPENDIX_E.md §E-4를 함께 view해서 
RiskManager를 작성해줘.

작업:
1. trading/risk_manager.py
   - 명세서 §9의 모든 RISK_RULES 검증
   + ★ v3.1.1: capital_manager 의존성 주입 (부록 E-4-2)
   + ★ check_all()이 비동기, capital_manager.get_snapshot() 호출
   + ★ _check_daily_loss(current, daily_start) — wallet_balance 기준
   + ★ _check_total_drawdown(current, initial) — initial 기준
   + ★ _check_min_balance(available) — available_balance 기준
2. tests/test_risk_manager.py — 기존 10개 + 부록 E-4-3의 4개 추가
   시나리오:
   1~10. (기존 명세서 §9 시나리오)
   11. wallet_balance=$950, daily_start=$1000 → -5% → 차단
   12. wallet_balance=$840, initial=$1000 → -16% → 차단
   13. available_balance=$80 → _check_min_balance 차단
   14. capital_manager.get_initial_capital() == None → check_all 차단 (안전 우선)

추가 요구:
- @dataclass CheckResult (passed, reason)
- DB 조회 메서드 (recent trades에서 연패 계산) — 명세서 §9
- 모든 룰은 config/settings.py의 RISK_RULES에서 읽기

DB 의존성:
- trades 테이블 필요 (세션 1에서 만든 schema.sql)
- 테스트는 임시 DB로 (tmp_path fixture)

CapitalManager 의존성:
- 테스트에서 capital_manager을 MagicMock으로 주입
- get_snapshot()이 CapitalSnapshot 반환하도록 mock 설정

완료 후:
- pytest 통과
- /clear 안내
```

---

## 세션 10 — WeeklyGPTAnalyst (기존 유지)

```
docs/SPEC_v3.1.md §8-4를 view해서 WeeklyGPTAnalyst를 작성해줘.

(이하 기존 세션 10 프롬프트 그대로)
```

---

## 세션 11 — BacktestEngine (기존 유지)

```
docs/SPEC_v3.1.md §10을 view해서 BacktestEngine을 작성해줘.

(이하 기존 세션 11 프롬프트 그대로)
```

---

## 세션 12 — main_7590.py 통합 (v3.1.1 강화)

```
docs/SPEC_v3.1.md §8-8과 docs/SPEC_v3.1_APPENDIX_E.md §E-5를 함께 view해서 
메인 봇을 통합해줘.

작업:
1. main_7590.py — MainBot 클래스 + 메인 루프
   + ★ v3.1.1: CapitalManager 통합 (부록 E-5-2)
     · _current_capital 멤버 제거 → capital_manager.get_snapshot() 사용
     · _handle_signal()에 capital_snapshot 인자 추가
     · sizer.calculate(capital=capital_snapshot.available_balance)
     · 마진 부족 추가 검증 (사이즈 > available 차단)
     · _verify_api_key_permissions() 시작 시퀀스 (부록 E-8-2)
     · 보호 종목 시작 시 Telegram 알림 (부록 E-5-3)
   + ★ PairWhitelist 초기화 시 protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols 주입
   + ★ trades 기록 시 wallet_balance_at_entry, available_at_entry 컬럼에 저장
2. data/collector.py — BinanceDataCollector (kline/ticker/funding REST + WS)
3. data/oi_scanner.py — OIScanner (OI 변화율)
4. strategy/oi_filter.py — OIFilter (regime_state 입력)
5. strategy/quality_gate.py — QualityGate
6. trading/executor.py — TradeExecutor (Post-Only Limit)
   + ★ enter_trade(decision) — decision에 wallet_balance_at_entry 컬럼 INSERT
7. trading/exit_plan.py — ExitPlanController
8. analytics/shadow_mode.py — ShadowRecorder
9. tests/test_main_integration.py — 통합 흐름 (mock 환경):
   - 봇 시작 → 보호 종목 알림 발송
   - BTCUSDT 시그널 → is_allowed=False, 진입 안 됨
   - SOLUSDT 시그널 → 모든 게이트 통과 → 진입 시뮬레이션
   - available_balance < size_usdt → 차단

규칙:
- 명세서 §8-8과 부록 E-5의 흐름 정확히 구현
- 각 모듈의 책임 분리 엄수
- 이 세션은 큰 작업이므로 파일별로 plan 보여주고 한 파일씩 진행
- 각 파일 완료 후 사용자에게 확인 요청

중요 검증:
- 봇 시작 시 다음 로그 메시지:
  · "[Start] 초기 자본: wallet=$X, available=$Y, locked_margin=$Z"
  · "🛡️ 보호 종목 활성: BTCUSDT, ETHUSDT, HOLOUSDT"
- BTCUSDT가 OIScanner 결과에 있어도 진입 차단 확인

완료 후:
- 페이퍼 환경에서 5분간 dry-run:
  `USE_TESTNET=true python main_7590.py --dry-run --duration 300`
- 로그에 ERROR 없음
- docs/MODULE_CHECKLIST.md 전체 항목 체크 완료
- Telegram에 보호 종목 알림 수신 확인
```

---

## 세션 후 작업 흐름

각 세션 완료 시:
```
✅ 모듈 코드 작성 완료
✅ 단위 테스트 모두 통과
✅ MODULE_CHECKLIST.md 해당 항목 체크
→ /clear 입력
→ 다음 세션 프롬프트 복사 붙여넣기
```

13개 세션 (0, 1, 2, 2.5, 3, 4, 5, 6, 7, 9, 10, 11, 12) 모두 완료 후 → Phase 0 백테스트 실행.

---

## v3.1.1 변경 사항 요약

| 세션 | v3.1 | v3.1.1 |
|---|---|---|
| 0 | 환경 검증 | + APPENDIX_E.md 존재 확인 |
| 1 | DB 스키마 | + capital_initial, capital_daily_snapshot 테이블 |
| 2 | Config | + CapitalManagerConfig, protected_symbols |
| 2.5 ★ | (없음) | **신규**: CapitalManager + PairWhitelist 보호 종목 |
| 3~7 | 동일 | 변경 없음 |
| 8 | PairWhitelist | 세션 2.5로 이동 (스킵) |
| 9 | RiskManager | + capital_manager 의존성, 자본 기준 분리 |
| 10~11 | 동일 | 변경 없음 |
| 12 | main_7590 통합 | + CapitalManager 통합, 보호 종목 알림 |

---

## 트러블슈팅

### Q. Claude Code가 명세서를 무시하는 것 같아요
A. 프롬프트 첫 줄에 "docs/SPEC_v3.1.md §X-X를 먼저 view해줘"를 명시했는지 확인.

### Q. 세션 2.5에서 CapitalManager가 Binance API를 실제 호출하려고 해요
A. 이 세션은 unit test만 작성하는 단계. 통합 호출은 세션 12에서. mock으로만 진행하라고 명시.

### Q. PairWhitelist 테스트에서 protected_symbols 차단이 작동 안 함
A. is_allowed()의 가장 처음 줄에서 protected_symbols 체크해야 함. 다른 룰보다 우선.

### Q. RiskManager가 capital_manager이 None일 때 차단이 안 됨
A. 부록 E-4-2의 check_all() 코드 확인. snapshot/initial/daily_start 중 하나라도 None이면 차단.
