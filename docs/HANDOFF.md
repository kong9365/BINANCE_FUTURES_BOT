# 핸드오프 노트 — Binance Futures Bot v3.1.1

> 새 Claude Code 인스턴스가 파일을 일일이 안 뒤지고 즉시 따라잡기 위한 1페이지 요약.
> 최종 갱신: 2026-05-14 (세션 12 완료 시점)

## 현재 상태: 구현 완료

13개 세션(0~12) 전부 완료. **`pytest tests/` → 222개 통과.** Testnet dry-run 검증 완료(exit 0, ERROR 없음).
남은 건 운영자/환경 작업뿐 — 코드 작업 없음. 상세는 `docs/MODULE_CHECKLIST.md` 하단 "남은 작업 요약" 참조.

## 먼저 읽을 것 (순서대로)

1. `CLAUDE.md` — 프로젝트 절대 룰(TIER 1~3) + 모듈↔명세 챕터 맵. **작업 전 필독.**
2. `docs/MODULE_CHECKLIST.md` — 세션별 진행상황(☑/□). 무엇이 끝났는지 한눈에.
3. `docs/SPEC_v3.1.md` + `docs/SPEC_v3.1_APPENDIX_E.md` — 명세서. 모듈 작업 시 해당 §섹션 view.
4. `docs/CORRECTIONS_v3.1.2.md` — 명세서 대비 정정 누적 기록(세션 5·7·10·11).

## 세션 12에서 한 일 (이 핸드오프의 직전 작업)

신규 8개 파일 + main 통합 + CMC 연동:
- `data/collector.py` (BinanceDataCollector), `data/oi_scanner.py` (OIScanner)
- `strategy/oi_filter.py` (OIFilter), `strategy/quality_gate.py` (QualityGate)
- `trading/executor.py` (TradeExecutor — Post-Only + 전량/분할 청산), `trading/exit_plan.py` (ExitPlanController)
- `analytics/shadow_mode.py` (ShadowRecorder)
- `data/cmc_client.py` (CMCClient — CoinMarketCap 시총 순위, 운영 중 추가 요청)
- `main_7590.py` (MainBot — 14개 모듈 조립 + v3.1.1 시작 시퀀스)
- `data/__init__.py`, `strategy/__init__.py` (누락돼 있던 패키지 초기화)

명세서가 v3.0 레거시 모듈(collector/oi_scanner/oi_filter/quality_gate/executor/exit_plan/shadow)의
**상세 코드를 제공하지 않아**, §8-8 데이터 흐름·D-2의 호출 계약 기준으로 신규 작성함.

## 세션 12 중 발견·수정한 버그 4건

1. `load_dotenv()` 미호출 → `.env`가 무시되던 문제. `main()`에 추가.
2. 시작 실패 시 raw traceback 노출 → `_amain`에서 한 줄 에러 로그로 정리.
3. WS 스텁 환경에서 health 체크가 매 루프 "kline 수신 이력 없음"으로 실패 → `start()` 프라이밍
   + `_iter()` 캔들 조회 성공 시 `record_ws_kline_received()` 호출로 REST 폴링을 신선도로 인정.
4. `_sync_initial_capital`이 0 이하 무효 초기 자본을 복원하던 문제 → 무효 행 비활성화 후 재기록.

## 운영/환경 핵심 사실

- **`.env` 키 구조**: 실거래(`BINANCE_API_KEY/SECRET`)와 테스트넷(`BINANCE_TESTNET_API_KEY/SECRET`)
  키가 분리됨. `USE_TESTNET` 값에 따라 `collector._build_client`가 자동 선택. 둘은 별개 발급처.
- **보호 종목** (`protected_symbols`): `BTCUSDT, ETHUSDT, HOLOUSDT, LYNUSDT` — 봇이 절대 거래 못함.
  환경변수 `PROTECTED_SYMBOLS` 우선, 없으면 `config/settings.py` 기본값.
- **CMC**: `CMC_API_KEY` 있으면 PairWhitelist Tier 3 시총 검증에 사용, 없으면 graceful 스킵.
- **dry-run**: `python main_7590.py --dry-run --duration <초>` — 거래소 주문 스킵, DB 기록은 수행.
- **WebSocket은 스텁** — 봇은 REST 폴링(30초 주기)으로 동작. 실 WS는 미구현.
- **로그는 콘솔 전용** — 파일 로깅/로테이션 미구현.

## 검증 방법

```bash
pip install -r requirements.txt
pytest tests/                                  # 222개 통과해야 정상
USE_TESTNET=true python main_7590.py --dry-run --duration 100   # exit 0, ERROR 없으면 정상
```

## 작업 규칙 리마인더 (CLAUDE.md 발췌)

- 새 파일/큰 수정 전 **plan 먼저 보여주고 승인** 받기 (TIER 1 #2)
- 모든 신규 모듈은 `tests/test_<module>.py` 동반 (TIER 1 #5)
- 보호 종목·Spot 격리 룰 절대 우회 금지 (TIER 1 #6, 부록 E)
- API 키는 코드·로그·주석 어디에도 출력 금지 (TIER 1 #4)
