# Binance USDT-M Futures 자동매매 봇 — 시스템 설계 청사진

> **Document ID**: `SYSTEM_DESIGN_BLUEPRINT_v1.0`
> **Project**: BINANCE_FUTURES_BOT (kong9365/BINANCE_FUTURES_BOT)
> **Owner**: 혜리 (광동제약 QC2 팀장, 품질부문 AI TF Lead)
> **Updated**: 2026-05-26
> **Status**: ACTIVE — Phase 1 실행 중 (Phase 1 v3 prompt 진행)
>
> **본 문서의 목적**: Claude Code 기반 자동매매 시스템의 *4-Layer 아키텍처 + GMP 검증 단계 매핑* 종합 청사진. Phase 1 검증 prompt (v3) 와 *상호 보완*적인 *상위 레벨 비전 명세*.

---

# 1. 문서 정보

## 1.1 목적

본 문서는 운영자 *9번의 자동매매 전략 실패* 후 재설계되는 시스템의 **전체 아키텍처 청사진**이다. 단일 봇 코드 명세가 아닌, **데이터 → 지능 → 실행 → 거버넌스** 4-Layer 구조 + GMP 검증 단계 매핑 + ALCOA+ 데이터 인티그리티 원칙을 통합한 *상위 레벨 시스템 설계 명세서*다.

핵심 차별점:
- **Claude Code 자산 (MCP, Skills, Subagents, Hooks)의 트레이딩 도메인 적용**
- **GMP/QC 검증 사고방식 (IQ → OQ → PQ) 의 자동매매 적용**
- **5-Agent Subagent 검토단** (Strategy/Risk/Execution/Data/Ops 다관점 검증)
- **ALCOA+ 원칙의 트레이딩 결정 적용** (Attributable, Legible, Contemporaneous, Original, Accurate)

## 1.2 범위

### In Scope
- Claude Code 기반 자동매매 봇의 *전체 아키텍처*
- MCP Connector 활용 (Binance Futures, 시장 데이터, 온체인)
- Subagent 분업 패턴 (5-Agent 검토단)
- Skills + Hooks 기반 거버넌스
- Phase 1 ~ Phase 5 단계별 구축 로드맵
- 안전장치 5가지 (ALCOA+, API 권한, 감사 로그, 킬 스위치, 일일 손실 한도)
- CLAUDE.md 템플릿, 환경변수, 체크리스트

### Out of Scope
- 구체적 전략 룰 코드 (Phase 1 v3 prompt에서 다룸)
- Phase 1 실행 절차 (별도 `PHASE1_CLAUDE_CODE_PROMPT_V3.md` 참조)
- Claude API 사용량 최적화 세부
- 세금/법무 (운영자 본인 책임 영역)

## 1.3 용어 정의

| 용어 | 정의 |
|---|---|
| **MCP** | Model Context Protocol — Claude가 외부 시스템과 표준 통신하는 프로토콜 (Dify 워크플로우의 표준화 버전) |
| **Skill** | Claude가 도메인에서 *어떤 데이터를 어떻게 평가할지* 사전 정의한 결정 규칙. ALCOA+ 추적 자동화 |
| **Subagent** | 메인 Claude 세션과 분리된 *전문 도메인 에이전트*. 5-Agent = Strategy/Risk/Execution/Data/Ops |
| **Hook** | 파일 작성·테스트 통과 등 *이벤트 시점*에 자동 트리거되는 검증 게이트 |
| **DSR** | Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) — 다중검정 보정 |
| **R0 후보** | Round 0 검증 통과 후보 (페이퍼 트레이딩 진입 자격) |
| **Single-Position Rotation** | 후보 풀 계속 스캔, 실제 보유는 항상 1개만 (자금 분산 X) |
| **EV** | Expected Value (기대값) — 보상 × 체결확률 - 비용 - 페널티 |
| **Setup** | 매매 전략 하나의 묶음 (예: `1d_tsmom_donchian_long_v1`) |
| **ALCOA+** | GMP 데이터 인티그리티 원칙 — Attributable, Legible, Contemporaneous, Original, Accurate (+) Complete, Consistent, Enduring, Available |
| **GMP** | Good Manufacturing Practice — 운영자의 제약 산업 품질 관리 표준 |
| **IQ/OQ/PQ** | Installation / Operational / Performance Qualification — GMP 검증 3단계 |
| **dry-run** | 실거래 X, 시뮬레이션만. Skills 기본값 |
| **kill switch** | 슬랙 한 단어로 봇 즉시 중단. 코드 첫 줄부터 설계 |

## 1.4 버전 이력

| 버전 | 날짜 | 변경 | 작성 |
|---|---|---|---|
| v1.0 | 2026-05-26 | 최초 작성 (10개 섹션, GMP 매핑, 5-Agent 상세, CLAUDE.md template) | Claude Opus 4.7 |

## 1.5 관련 문서

| 문서 | 역할 |
|---|---|
| `docs/HANDOFF.md` | 9개 실패 strategy 이력, 현재 상태 |
| `docs/STRATEGY_REALISM_REVIEW.md` | 각 실패 strategy 사유 분석 |
| `docs/PHASE1_CLAUDE_CODE_PROMPT_V3.md` | Phase 1 실행 명세 (Phase 1 v3) |
| `CLAUDE.md` | TIER 1 절대 룰 + 본 청사진 §10 template |
| (향후) `docs/STRATEGY_VERIFICATION_REPORT.md` | Phase 1 결과 보고서 |
| (향후) `docs/CANDIDATE_CONTEXT_LAYER_SPEC.md` | Phase 1 Context Layer 명세 |
| (향후) `docs/PHASE1_5_MICRO_LIVE_PROMPT.md` | Phase 1.5 micro-live 명세 |

---

# 2. 시스템 개요

## 2.1 비전

> **"GMP 수준의 품질 관리 + Claude Code 거버넌스 + Single-Position Rotation 운영 + Phase 검증 단계"**
>
> 9번의 자동매매 실패 후, *즉흥적 전략 추가나 단기 신호 사냥*이 아닌, **제약 산업 QC 검증 사고방식**을 자동매매에 그대로 이식한다. Phase 1 (검증) → Phase 1.5 (Micro-live) → Phase 2 (멀티 엔진) → Phase 3 (Microstructure) → Phase 4 (자본 확장) → Phase 5 (자율 운영) 의 5단계가 각각 **IQ → MV → OQ → PQ → 정식 배포**의 GMP 비유에 정확히 매핑된다.

### 핵심 슬로건
**"GMP 트레이딩 (GMP Trading)"** — 검증되지 않은 전략은 *실거래 진입 금지*, 모든 결정은 *ALCOA+ 추적 가능*, 운영 중에는 *킬 스위치 우선* 작동.

## 2.2 핵심 차별점 (단순 봇과의 구분)

| 항목 | 단순 트레이딩 봇 | **본 시스템** |
|---|---|---|
| **데이터 입력** | 거래소 API 직접 호출 | **MCP Connectors** (표준화, 재사용) |
| **결정 로직** | 코드 안에 하드코딩 | **Strategy Skill** (ALCOA+ 자동 추적) |
| **검증 방법** | 백테스트만 | **5-Agent 다관점 검토** + DSR + 3-Gate |
| **실행 안전** | 코드 안에 분산 | **Hooks + Kill Switch + 리스크 게이트** 명시적 |
| **검증 단계** | 즉시 라이브 | **Phase 1~5 (IQ→MV→OQ→PQ)** |
| **데이터 인티그리티** | 로그만 | **ALCOA+** (제약 산업 표준) |
| **알림/감사** | 콘솔 출력 | **Slack/Notion MCP** + DB 추론 기록 |
| **킬 스위치** | 사후 추가 | **코드 첫 줄부터 설계** |

## 2.3 전체 아키텍처 (4-Layer 청사진)

```
┌─────────────────────────────────────────────────────────────────┐
│                     1. 데이터 레이어 (Data)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │ Binance MCP │  │ 시장 데이터  │  │ 온체인 시그널│               │
│  │ 선물 API     │  │ WebSocket   │  │ 고래·자금흐름 │               │
│  └─────────────┘  └─────────────┘  └─────────────┘               │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  2. 지능 레이어 (Intelligence)                    │
│  ┌─────────────┐  ┌─────────────────────────────────────────┐    │
│  │ Strategy    │  │  5-Agent Subagent 검토단                 │    │
│  │ Skill       │  │  ┌────────┐ ┌────────┐ ┌────────┐       │    │
│  │ (ALCOA+)    │  │  │Strategy│ │  Risk  │ │  Exec  │       │    │
│  │             │  │  └────────┘ └────────┘ └────────┘       │    │
│  │             │  │  ┌────────┐ ┌────────┐                 │    │
│  │             │  │  │  Data  │ │  Ops   │                 │    │
│  │             │  │  └────────┘ └────────┘                 │    │
│  └─────────────┘  └─────────────────────────────────────────┘    │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  3. 실행 레이어 (Execution)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │ 주문 엔진    │  │ 포지션 관리  │  │ 리스크 게이트│               │
│  │ (Binance MCP)│ │ SL/TP 자동  │  │ 일일 손실 한도│               │
│  └─────────────┘  └─────────────┘  └─────────────┘               │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                4. 거버넌스 레이어 (Governance)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │ Hooks        │  │ Slack/Notion│  │ Kill Switch  │              │
│  │ 자동 게이트  │  │ 알림·감사    │  │ 긴급 중단    │               │
│  └─────────────┘  └─────────────┘  └─────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

## 2.4 GMP 비유 — 운영자가 가장 친숙한 사고방식

운영자의 *광동제약 QC2 팀장* 경험에 따라, 본 시스템은 GMP 검증 단계 비유를 **전면 채택**한다.

| GMP 단계 | 자동매매 매핑 | 목표 |
|---|---|---|
| **IQ** (Installation Qualification) | Phase 1: 인프라 셋업 | 외장 SSD, MCP 연결, 의존성, 환경변수 *제대로 설치* |
| **MV** (Method Validation) | Phase 1: 4-후보 사전 검증 | 전략이 *학계 기준 + 실증* 으로 작동하는지 *방법 검증* |
| **OQ** (Operational Qualification) | Phase 1.5: Micro-live Sandbox | 시스템이 *실제 운영 조건*에서 작동하는지 *기능 검증* |
| **PQ** (Performance Qualification) | Phase 2~3: 멀티 엔진, Microstructure | *실거래 성능*이 *예상대로* 나오는지 *성능 검증* |
| **정식 배포** | Phase 4~5: 자본 확장, 자율 운영 | 검증 완료 후 *전면 운영* |

→ GMP에서 IQ/OQ/PQ를 *생략* 하면 *제품 회수* 발생. 자동매매도 마찬가지 — **Phase 건너뛰기는 9번 실패 반복**.

## 2.5 OOS/OOT 사고방식의 적용

운영자가 일상에서 다루는 **OOS** (Out Of Specification) / **OOT** (Out Of Trend) 개념은 자동매매에 정확히 매핑된다.

### OOS (Out Of Specification) ↔ 사전확정 임계값 위반
```
GMP OOS 예시:
  HPLC 분석 결과 99.2% (사양: 98.0~102.0%) → IN-SPEC
  HPLC 분석 결과 97.5% → OOS → CAPA 발동

자동매매 OOS 예시:
  Phase 1 후보 1 PF 1.32 (사양: ≥ 1.25) → IN-SPEC
  Phase 1 후보 1 PF 1.18 (사양: ≥ 1.25) → OOS → 11-체크리스트 미통과 → DISABLED
```

### OOT (Out Of Trend) ↔ Setup 성능 drift
```
GMP OOT 예시:
  최근 10배치 평균 99.5%, 추세선 99.3% → drift
  → OOT → 원인 분석 (장비, 시약, 분석자 등)

자동매매 OOT 예시:
  Setup A 최근 30일 PF 1.40, 직전 90일 PF 1.20 → drift
  → OOT → 원인 분석 (시장 regime, 비용 변화, slippage 증가)
  → COOLING 상태 전환
```

본 시스템의 **Setup Registry**는 GMP의 *방법 변경 통제 (Change Control)* 와 동일하다:
- 모든 임계 변경 = 새 `setup_id` (params_hash 변경)
- 누적 평가 횟수 추적 = *유의차 (Significant Difference)* 자동 인식
- DSR 보정 = *다중 비교 (Multiple Comparison)* 통계 보정

---

# 3. 4-Layer 아키텍처 상세

## 3.1 데이터 레이어 — MCP Connectors가 핵심

### 3.1.1 MCP (Model Context Protocol) 개념

MCP는 Claude가 외부 시스템과 *표준 프로토콜로* 통신하는 방법이다. 운영자가 *Dify로 LIMS Excel을 처리하셨던 워크플로우*와 비슷하나, **훨씬 더 표준화·재사용 가능**하다.

```
[Dify 워크플로우 (기존)]            [MCP Connector (본 시스템)]
   ┌────────────┐                       ┌────────────┐
   │ LIMS Excel │                       │ Binance API│
   └─────┬──────┘                       └─────┬──────┘
         ▼                                     ▼
   ┌────────────┐                       ┌────────────┐
   │ Dify YAML  │                       │ MCP Server │  ← 표준
   │ workflow   │                       │ (재사용)    │
   └─────┬──────┘                       └─────┬──────┘
         ▼                                     ▼
   ┌────────────┐                       ┌────────────┐
   │   Claude   │                       │   Claude   │
   └────────────┘                       └────────────┘
```

**핵심 차이**:
- Dify 워크플로우 = *한 번에 한 작업*, 사용자 정의 필요
- MCP Connector = *표준 인터페이스*, 한 번 만들면 *모든 Claude 세션*에서 재사용

### 3.1.2 Binance Futures MCP

#### 기능
- 계좌 잔액 조회 (USDT, BNB 등)
- 실시간 포지션 모니터링 (롱/숏, 평균단가, 미실현 손익, 청산가)
- 복잡한 주문 유형 실행:
  - 시장가 (MARKET)
  - 지정가 (LIMIT)
  - 스톱 (STOP, STOP_MARKET)
  - OCO (One-Cancels-Other)
  - 아이스버그 (Iceberg, 부분 노출 주문)
  - Post-only (Maker 강제)
  - reduceOnly (포지션 축소만)
- 펀딩비 조회 + 정산 알림
- OI (Open Interest) 모니터링
- 주문 이력 조회

#### 설정 (`.env`)
```bash
# Binance Futures USDT-M API
BINANCE_API_KEY=<운영자 키>
BINANCE_API_SECRET=<운영자 시크릿>
USE_TESTNET=false  # Phase 1 검증 단계는 false

# Phase 1 검증 후 PASS 시 testnet 추가
BINANCE_TESTNET_API_KEY=<testnet 키>  # Phase 1.5 paper-trade
BINANCE_TESTNET_API_SECRET=<testnet 시크릿>

# MCP 서버 (Phase 1.5 이후 활성화)
BINANCE_MCP_SERVER_URL=https://mcp.binance.example.com  # (가상 예시)
```

#### GMP 비유 — *시약 발주 시스템 (LIMS API)*
- LIMS API = 시약 발주, 재고 조회, 만료일 추적
- Binance MCP = 자본 발주, 포지션 조회, 청산가 추적
- 두 시스템 모두 *권한 분리* (열람만 vs 수정 권한) 필수

### 3.1.3 시장 데이터 (WebSocket)

#### 데이터 종류
- **호가 (Depth)**: bid/ask 가격, 수량, 깊이
- **체결 (Trade)**: 실시간 거래 발생
- **캔들 (Kline)**: OHLCV (1m, 5m, 15m, 1h, 4h, 1d, 1w)
- **펀딩비 (Funding)**: 8시간마다 정산
- **마크 프라이스 (Mark Price)**: 청산 기준 가격
- **OI (Open Interest)**: 미결제 약정

#### Phase별 활용
| Phase | 활용 시간대 |
|---|---|
| Phase 1 | 1d, 4h, 1h, funding, OI (백테스트 only) |
| Phase 1.5 | 1d, 4h 실시간 (micro-live $30~50) |
| Phase 2 | 1d, 4h, 1h, 5m 실시간 (멀티 엔진) |
| Phase 3 | + 5m, depth, trade, liquidation (Microstructure) |
| Phase 4~5 | 전체 (성능 PQ + 자율 운영) |

#### GMP 비유 — *환경 모니터링 (Environmental Monitoring, EM)*
- EM = 청정실 온도, 습도, 차압, 미생물 실시간 모니터링
- 시장 데이터 = 가격, 호가, 펀딩, OI 실시간 모니터링
- 둘 다 *연속 데이터 수집* + *임계 초과 시 알림*

### 3.1.4 온체인 시그널 (선택)

#### 데이터 종류
- 고래 지갑 입출금 (CryptoQuant, Glassnode, Arkham)
- 거래소 입출금 (Bitcoin/Ethereum/Solana 등)
- 스마트머니 진입/청산 (DeBank, Nansen)
- ETF 자금 흐름 (Farside, SoSoValue)

#### 본 시스템 사용 원칙
- **본 시스템은 *USDT-M perp 선물*** 전용 → 온체인은 *후보선정 보조*만
- 직접 진입 신호 X (Phase 1 v3 §11 참조)
- Phase 1.5 이후 *Context Layer*에서 활용

#### GMP 비유 — *원료 공급망 정보 (Supply Chain)*
- 원료 공급사 동향 = 직접 생산 결정 X, *공급 위험 관리*
- 온체인 = 직접 매매 결정 X, *시장 환경 위험 관리*

### 3.1.5 Phase 1 우선순위 데이터 (v3 통합)

Phase 1 검증 단계에서 활용하는 데이터:

```python
# Phase 1 v3에서 정의된 데이터
PHASE_1_DATA = {
    "ohlcv_1d": "50종목 × 5년",     # 후보 1, 2, 3 메인
    "ohlcv_4h": "50종목 × 5년",     # MTF 게이트
    "ohlcv_5m": "50종목 × 1년",     # MTF 진입 시뮬레이션
    "ohlcv_1h": "50종목 × 1년",     # 참고
    "funding": "50종목 × 5년",      # 후보 4 메인
    "oi_history": "50종목 × 1년",   # 후보 4 보조
    "market_global": "BTC dominance, F&G",  # BTC-Beta
    "universe_meta": "상장일, status",  # 생존편향 차단
}
```

**저장 경로**: `E:/bot_data/candles/` (외장 SSD)
**형식**: Parquet (DuckDB 분석)

---

## 3.2 지능 레이어 — Skills + 5-Agent Subagents의 분업

> **본 레이어가 "단순 봇"과 "고도화된 시스템"을 가르는 결정적 차이.**

### 3.2.1 Strategy Skill — 결정 규칙의 표준화

#### 개념
Strategy Skill = Claude가 자산을 평가하기 *전에* 다음을 *사전에 알고 있는* 구조:
- **어떤 데이터를 고려할지** (피처 명세)
- **시그널 가중치** (Phase 1 v3의 4-후보 weights)
- **액션 추천 confidence 임계값** (3-Gate 통과 기준)
- **추론을 평문으로 표현** (사람이 읽을 수 있는 reasoning)

그리고 그 추론은 *모든 시그널과 함께 데이터베이스에 저장*된다 (Setup Registry).

#### 왜 중요한가 — ALCOA+ 자동화
운영자가 GMP에서 익숙한 **ALCOA+ 원칙**이 트레이딩 결정에 그대로 적용:

| ALCOA+ | GMP 정의 | 트레이딩 적용 |
|---|---|---|
| **A**ttributable | 누가/무엇이 수행 | `setup_id`, `claude_session_id`, `signal_source` 기록 |
| **L**egible | 읽을 수 있게 | 자연어 reasoning + JSON 구조화 |
| **C**ontemporaneous | 실시간 | 시그널 발생 즉시 DB 기록 (Outbox 패턴) |
| **O**riginal | 원본 | raw 시장 데이터 hash + 시그널 hash 보존 |
| **A**ccurate | 정확 | 사후 변경 금지, append-only log |
| **+ C**omplete | 완전 | 시그널 + reasoning + 비용 + 결과 모두 |
| **+ C**onsistent | 일관 | params_hash로 *동일 시그널 재현 가능성* |
| **+ E**nduring | 영속 | Parquet + SQLite 백업 + GitHub 미러 |
| **+ A**vailable | 접근 가능 | DuckDB 쿼리, Notion 대시보드 |

#### 구현 예시

```python
# strategies/tsmom_donchian_skill.py
class TSMOMDonchianSkill:
    """
    Vol-targeted Long-or-Flat 1d TSMOM / Donchian Core
    
    ALCOA+ 자동 추적:
      - Attributable: setup_id = "1d_tsmom_donchian_long_v1"
      - Legible: reasoning field (자연어)
      - Contemporaneous: signal_ts = now()
      - Original: raw_data_hash = sha256(candles)
      - Accurate: append-only
    """
    
    SETUP_ID = "1d_tsmom_donchian_long_v1"
    PARAMS_HASH = sha256(json.dumps(PARAMS, sort_keys=True))
    
    def evaluate(self, symbol: str, candles: pd.DataFrame, market_state: dict) -> SignalDecision:
        """
        평가 + ALCOA+ 추적 자동.
        
        Returns:
            SignalDecision(
                signal_id=uuid4(),
                setup_id=SETUP_ID,
                params_hash=PARAMS_HASH,
                symbol=symbol,
                action="LONG" | "FLAT" | "EXIT",
                confidence=0.0~1.0,
                reasoning="...",  # 자연어
                features_json={"donchian_breakout_20": True, ...},
                cost_estimate_json={"case_a_pct": 0.063, "case_c_pct": 0.090},
                raw_data_hash=sha256(candles),
                ts=datetime.utcnow(),
                expires_at=ts + timedelta(hours=24),
            )
        """
        features = self._compute_features(candles)
        
        # 의사결정 룰
        if not self._gate_check(features, market_state):
            return SignalDecision(action="FLAT", reasoning="Gate failed: ...")
        
        confidence = self._compute_confidence(features)
        if confidence < self.CONFIDENCE_THRESHOLD:
            return SignalDecision(action="FLAT", reasoning="Confidence below threshold")
        
        reasoning = self._generate_reasoning(features, confidence)
        
        return SignalDecision(
            action="LONG",
            confidence=confidence,
            reasoning=reasoning,
            features_json=features,
            # ... ALCOA+ 자동 채움
        )
```

#### GMP 비유 — *SOP (Standard Operating Procedure)*
- SOP = 분석자가 *언제, 어떤 단계로, 어떤 결정 룰로* 실험 수행
- Strategy Skill = 봇이 *언제, 어떤 데이터로, 어떤 룰로* 시그널 발행
- 둘 다 *변경 시 Change Control* 필수

### 3.2.2 5-Agent Subagent 검토단 — 다관점 검증

#### 핵심 통찰 (실증 사례)
크립토 선물 트레이딩 봇 *5-Agent 검토 실증*:
- 봇 결과: 승률 **60%** + 순손익 **-$39.20**
- 단일 Claude 분석 = "승률 60% = 양호"
- 5-Agent 분석:
  - Strategy Agent: "위험/보상 구조 역전 (작은 수익, 큰 손실)"
  - Risk Agent: "**일일 최대 손실 한도 코드에 누락**"
  - Execution Agent: "**포지션 사이징 로직 버그**"
  - Data Agent: "백테스트 데이터 충분"
  - Ops Agent: "로그 충분"

→ **단일 Claude는 표면적 분석**, **5-Agent는 도메인 전문가 시각**. 운영자 9번 실패 중 일부는 *5-Agent가 *사전*에 작동했다면* 차단 가능했을 것.

#### 5-Agent 역할 명세

##### Agent 1: Strategy/Quant Agent

**역할**: 전략 자체의 통계적 유효성, alpha 원천, 다중검정 위험 검증.

**입력**:
- Setup Registry의 전략 명세 (params_json)
- 백테스트 결과 (PF, Sharpe, MDD, n)
- 4-후보 IC, Decile, DSR 결과

**출력 (JSON)**:
```json
{
  "agent": "strategy_quant",
  "setup_id": "1d_tsmom_donchian_long_v1",
  "ts": "2026-05-26T12:00:00Z",
  "verdict": "PASS|CONDITIONAL|FAIL",
  "alpha_source": "TSMOM (Han·Kang·Ryu 2023 학계 검증)",
  "alpha_strength": "STRONG|MEDIUM|WEAK",
  "multiple_testing_risk": "LOW|MEDIUM|HIGH",
  "dsr_check": 0.97,
  "concerns": [
    "Effective N = 110 (운영자 9개 + 4-후보 × 5 파라미터)",
    "Cross-sectional momentum 학계 약함 → 후보 2 우선순위 격하 권고"
  ],
  "recommendations": [
    "후보 1 R0 진행 OK",
    "후보 2~4 보조 피처로 활용"
  ],
  "confidence": "HIGH"
}
```

**판정 룰**:
- IC > 0.05 + DSR ≥ 0.95 + 학계 근거 명확 → PASS
- IC borderline + DSR borderline → CONDITIONAL
- IC < 0.02 또는 DSR < 0.80 → FAIL

**참조 학계 근거**:
- Bailey & Lopez de Prado (2014) DSR
- Han·Kang·Ryu (2023) TSMOM
- MOP (2012) TSMOM 보편성
- Grayscale (2023) 50d MA Sharpe 1.9

##### Agent 2: Risk Agent

**역할**: 자본 보존, 일일/주간 손실 한도, MDD, 단일 종목 집중 검증.

**입력**:
- Strategy Skill의 SignalDecision
- 현재 포지션 + 자본 상태
- Risk 게이트 룰 (CLAUDE.md)
- 백테스트 MDD, 단일 종목 기여도

**출력 (JSON)**:
```json
{
  "agent": "risk",
  "ts": "2026-05-26T12:00:00Z",
  "verdict": "APPROVE|REJECT|ESCALATE",
  "daily_loss_check": {
    "current_pct": -0.8,
    "limit_pct": -3.0,
    "status": "OK"
  },
  "weekly_loss_check": {
    "current_pct": -2.1,
    "limit_pct": -8.0,
    "status": "OK"
  },
  "mdd_check": {
    "expected_mdd_pct": 22.4,
    "limit_pct": 25.0,
    "status": "OK"
  },
  "concentration_check": {
    "single_symbol_max_pct": 18.5,
    "limit_pct": 25.0,
    "status": "OK"
  },
  "leverage_check": {
    "proposed_leverage": 3.0,
    "limit": 5.0,
    "status": "OK"
  },
  "concerns": [],
  "escalation_required": false
}
```

**판정 룰**:
- 모든 한도 미만 → APPROVE
- 1개 한도 borderline (90%+) → ESCALATE (운영자 확인 필요)
- 1개 한도 초과 → REJECT (시그널 차단)

**참조**:
- Phase 1 v3 사전확정 11-체크리스트 §8
- CLAUDE.md TIER 1 절대 룰
- arXiv 2502.18625 메이커 경고

##### Agent 3: Execution/Exchange API Agent

**역할**: 주문 실행 가능성, 체결 확률, slippage, MCP 호출 정확성 검증.

**입력**:
- Risk Agent APPROVE된 SignalDecision
- 현재 호가창 (depth)
- 최근 슬립 통계
- Binance MCP 상태

**출력 (JSON)**:
```json
{
  "agent": "execution",
  "ts": "2026-05-26T12:00:00Z",
  "verdict": "EXECUTE|DELAY|SKIP",
  "order_plan": {
    "symbol": "SOLUSDT",
    "side": "BUY",
    "quantity": 5.0,
    "order_type": "LIMIT",
    "post_only": true,
    "price": 142.50,
    "reduce_only": false
  },
  "fill_probability": 0.75,
  "estimated_slippage_pct": 0.05,
  "case_distribution": {
    "case_a_prob": 0.65,
    "case_b_prob": 0.10,
    "case_c_prob": 0.20,
    "case_d_prob": 0.05
  },
  "mcp_status": "HEALTHY",
  "concerns": [
    "Spread 0.08% (정상)",
    "Bid depth 충분"
  ],
  "fallback_plan": "5분 미체결 시 taker fallback (case C)"
}
```

**판정 룰**:
- Fill probability ≥ 0.6 + Spread OK → EXECUTE
- Fill probability 0.3~0.6 → DELAY (재시도)
- Fill probability < 0.3 → SKIP

**참조**:
- arXiv 2502.18625 maker 232,897건 실증
- Phase 1 v3 §7 Gate 3 4-case 분포

##### Agent 4: Data/Backtest Agent

**역할**: 데이터 품질, 백테스트 무결성, 점-인-타임 검증, 룩어헤드 차단.

**입력**:
- 입력 데이터 (candles, funding, OI)
- 데이터 hash + 메타
- 상장일 메타 (universe_meta.parquet)

**출력 (JSON)**:
```json
{
  "agent": "data_backtest",
  "ts": "2026-05-26T12:00:00Z",
  "verdict": "VALID|INVALID|STALE",
  "data_quality": {
    "missing_ratio_pct": 0.02,
    "gap_count": 0,
    "outlier_count": 1,
    "staleness_minutes": 2
  },
  "survivorship_bias_check": {
    "point_in_time": true,
    "onboard_filter_applied": true,
    "excluded_symbols": ["KDA_pre_listing", "LUNA_post_collapse"]
  },
  "lookahead_check": {
    "feature_uses_future_data": false,
    "next_bar_fill_applied": true
  },
  "concerns": [],
  "raw_data_hash": "sha256:abc...",
  "candles_count": 91250
}
```

**판정 룰**:
- 데이터 품질 OK + 상장일 필터 OK + 룩어헤드 X → VALID
- 데이터 quality borderline → STALE (운영자 확인)
- 룩어헤드 발견 → INVALID (즉시 작업 중단)

**참조**:
- Phase 1 v3 §5-2 상장일 필터
- 9번 실패 #6 CSM의 LAB 단일 종목 집중

##### Agent 5: Ops/Observability Agent

**역할**: 시스템 상태, 로그 완전성, 알림 동작, 킬 스위치 작동 검증.

**입력**:
- 봇 헬스 메트릭 (CPU, 메모리, 디스크, 네트워크)
- 로그 누락 여부
- Slack/Telegram 알림 동작
- 킬 스위치 응답성

**출력 (JSON)**:
```json
{
  "agent": "ops_observability",
  "ts": "2026-05-26T12:00:00Z",
  "verdict": "HEALTHY|DEGRADED|CRITICAL",
  "system_health": {
    "cpu_pct": 12,
    "memory_pct": 35,
    "disk_pct": 18,
    "external_ssd_status": "MOUNTED",
    "network_latency_ms": 45
  },
  "data_pipelines": {
    "binance_ws_status": "CONNECTED",
    "ws_uptime_hours": 23.5,
    "queue_overflow": false
  },
  "alerting": {
    "slack_last_msg_minutes_ago": 5,
    "telegram_last_msg_minutes_ago": 5,
    "kill_switch_responsive": true
  },
  "alcoa_compliance": {
    "all_signals_logged": true,
    "outbox_pending": 0,
    "reconciliation_diff": 0
  },
  "concerns": [
    "WS 24h 재연결 임박 (30분 남음)"
  ],
  "recommendations": [
    "WS 자동 재연결 스크립트 실행"
  ]
}
```

**판정 룰**:
- 모든 시스템 OK + ALCOA+ 준수 → HEALTHY
- 일부 degraded → DEGRADED (자동 복구 시도)
- 킬 스위치 응답 없음 또는 ALCOA+ 위반 → CRITICAL (전체 중단)

**참조**:
- Polyphemus 36,000줄 봇의 hooks/MCP/체크포인팅 패턴
- ALCOA+ 원칙 (§3.2.1)

#### 5-Agent 사용 원칙

**Subagent는 무분별하게 띄우지 않는다**:
- ✅ **읽기 위주 탐색** (분석, 검토, 검증)
- ✅ **경계 명확한 작업** (도메인 단독)
- ❌ **코드 작성은 메인 세션** (서브에이전트 X)

#### 5-Agent 작동 시점

```
신호 발생
  ↓
[Strategy Agent] ← 알파 + 다중검정 검증
  ↓ PASS
[Data Agent] ← 데이터 품질 + 룩어헤드 검증
  ↓ VALID
[Risk Agent] ← 자본 + 손실 한도 검증
  ↓ APPROVE
[Execution Agent] ← 체결 가능성 + slippage 검증
  ↓ EXECUTE
주문 실행
  ↓
[Ops Agent] ← 시스템 헬스 + ALCOA+ 검증 (지속)
```

**병렬 가능**:
- Strategy + Data + Ops는 *병렬* (동시 분석)
- Risk + Execution은 *순차* (Risk 후 Execution)

#### GMP 비유 — *QC 검사 단계*
- 원료 검사 (Data Agent) → 공정 검증 (Strategy Agent) → 최종 검사 (Risk Agent) → 출하 검정 (Execution Agent) → 시판 후 감시 (Ops Agent)
- 단계별 *책임 분리* + *교차 확인*

### 3.2.3 Strategy Skill + 5-Agent 결합 — 워크플로우 예시

```python
# 매 1d 봉 마감 시 (00:00 UTC)
def daily_evaluation_workflow():
    """
    Phase 1 v3의 후보 1 (TSMOM/Donchian) 평가 + 5-Agent 검토.
    """
    # 1. Strategy Skill 평가
    skill = TSMOMDonchianSkill()
    signals = []
    for symbol in universe_50:
        decision = skill.evaluate(symbol, candles_1d, market_state)
        if decision.action == "LONG":
            signals.append(decision)
    
    # 2. 5-Agent 병렬 검토 (Strategy + Data + Ops)
    parallel_reviews = await asyncio.gather(
        strategy_agent.review(signals),
        data_agent.review(signals),
        ops_agent.review(signals),
    )
    
    # 3. Strategy 또는 Data 또는 Ops FAIL 시 즉시 중단
    if any(r.verdict in ["FAIL", "INVALID", "CRITICAL"] for r in parallel_reviews):
        await slack_alert("🚨 5-Agent 검토 실패", parallel_reviews)
        return  # 진입 X
    
    # 4. Single-Position Rotation — EV 1위 선정
    ranked = single_position_rotation.rank_by_ev(signals, market_state)
    if not ranked:
        return  # 후보 없음
    
    top_candidate = ranked[0]
    
    # 5. Risk Agent 검토
    risk_review = risk_agent.review(top_candidate)
    if risk_review.verdict != "APPROVE":
        await slack_alert(f"🛑 Risk Agent REJECT", risk_review)
        return
    
    # 6. Execution Agent 검토
    exec_review = execution_agent.review(top_candidate)
    if exec_review.verdict != "EXECUTE":
        if exec_review.verdict == "DELAY":
            await schedule_retry(top_candidate, minutes=5)
        return
    
    # 7. 실제 주문 (메인 세션, Binance MCP)
    order_result = await binance_mcp.place_order(exec_review.order_plan)
    
    # 8. ALCOA+ 기록 (Setup Registry + Outbox)
    await alcoa_record(top_candidate, order_result, parallel_reviews + [risk_review, exec_review])
    
    # 9. Slack/Notion 알림
    await slack_notify(f"✅ 진입: {top_candidate.symbol} {top_candidate.action}")
```

---

## 3.3 실행 레이어 — 안전장치 내장

### 3.3.1 주문 엔진 (Binance MCP)

#### 주문 흐름
```
SignalDecision → 5-Agent 검토 → Risk 게이트 → Execution Agent
  ↓ EXECUTE
주문 빌더 (post-only LIMIT)
  ↓
Binance MCP (place_order)
  ↓
체결 모니터 (5분 timeout)
  ↓ 체결 / 미체결
체결: 포지션 등록 + SL/TP 자동 주문
미체결: Case C 또는 Case B 판정
```

#### post-only LIMIT 강제 (기본값)
```python
# 기본 주문 파라미터 (변경 금지)
DEFAULT_ORDER_PARAMS = {
    "type": "LIMIT",
    "timeInForce": "GTX",  # Post-only (Maker 보장)
    "reduceOnly": False,    # 진입 시 False
}

# Taker fallback (5분 미체결 후)
TAKER_FALLBACK = {
    "type": "MARKET",
    "reduceOnly": False,
}
```

#### GMP 비유 — *제조 공정 (Manufacturing Process)*
- 제조 공정 = 원료 → 혼합 → 충전 → 검사 → 출하
- 주문 엔진 = 시그널 → 검토 → 주문 → 체결 → 모니터링

### 3.3.2 포지션 관리

#### 추적 항목
- 평균단가 (average price)
- 미실현 손익 (unrealized P&L)
- 청산가 (liquidation price)
- 동적 SL/TP (Trailing Stop, ATR-based)
- 보유 시간 (holding duration)
- 시간 스톱 (time stop, 30일)

#### Single-Position Rotation 통합
```python
# trading/position_manager.py
class PositionManager:
    """
    Single-Position 강제.
    동시 보유 = 1개만.
    보유 중 신규 진입 시도 → 자동 차단.
    """
    
    MAX_OPEN_POSITIONS = 1
    
    def can_open_new(self) -> bool:
        return self.current_positions_count() == 0
    
    def manage_active_position(self):
        """매 5분: SL/TP 자동 조정, 트레일링 스톱."""
        position = self.get_current_position()
        if not position:
            return
        
        # ATR-based trailing stop
        new_sl = self._compute_atr_trail(position)
        if new_sl > position.current_sl:
            self._update_sl(position, new_sl)
        
        # Time stop (30d)
        if position.holding_days >= 30:
            self._exit_position(position, reason="time_stop")
        
        # ALCOA+ 기록
        self._log_position_state(position)
```

### 3.3.3 리스크 게이트

#### 게이트 종류 (Risk Agent 자동 검증)

```python
RISK_LIMITS = {
    # 일일/주간 손실
    "daily_loss_max_pct": -3.0,
    "weekly_loss_max_pct": -8.0,
    
    # MDD
    "mdd_warn_pct": 20.0,
    "mdd_halt_pct": 25.0,
    
    # 단일 종목
    "single_symbol_max_pct": 25.0,
    
    # 레버리지
    "leverage_max": 5.0,
    "leverage_default": 3.0,
    
    # 연속 손실
    "consecutive_loss_max": 5,
    "cooldown_hours_after_consecutive_loss": 24,
    
    # 동일 종목
    "same_symbol_consecutive_loss_max": 2,
    "same_symbol_cooldown_hours": 12,
    
    # BTC risk-off
    "btc_risk_off_block_new_entries": True,
}
```

#### CloddsBot 참고 — 통합 리스크 엔진
Polyphemus 36,000줄 봇과 CloddsBot은 다음 패턴 표준화:
- 서킷 브레이커 (Circuit Breaker)
- VaR/CVaR (Value at Risk / Conditional VaR)
- 변동성 체제 감지 (Volatility Regime Detection)
- 스트레스 테스트

본 시스템은 *Phase 1.5 이후 점진 도입*:
- Phase 1: 일일 손실 + MDD + 단일 종목만
- Phase 1.5: + 연속 손실 + 동일 종목 cooldown
- Phase 2: + 서킷 브레이커
- Phase 3: + VaR/CVaR
- Phase 4: + 변동성 체제 감지

#### GMP 비유 — *Hold/Release 시스템*
- 제조 후 *QC 검사 통과 전*까지 *Hold* (출하 금지)
- 자동매매 *Risk 게이트 통과 전*까지 *주문 차단*
- 두 시스템 모두 *명시적 Release 결정* 필수

### 3.3.4 동적 SL/TP 자동 조정

```python
# trading/sl_tp_manager.py
class SLTPManager:
    """
    Trailing Stop + ATR-based dynamic SL.
    
    Phase 1 v3에서 정의:
      SL: entry - 2 × ATR(20)
      TP1: +1.5R, 50% 청산
      TP2: +3.0R, 30% 청산
      나머지: Donchian low_10 trailing
    """
    
    def update_sl(self, position: Position, current_candle: Candle) -> Optional[float]:
        # ATR-based trail
        atr_trail = current_candle.close - 2 * position.atr_20
        
        # Donchian low_10
        donchian_trail = position.donchian_low_10
        
        # 가장 높은 trail (더 보수적)
        new_sl = max(atr_trail, donchian_trail, position.current_sl)
        
        if new_sl > position.current_sl:
            return new_sl
        return None
```

---

## 3.4 거버넌스 레이어 — Claude Code의 진짜 강점

### 3.4.1 Hooks (Claude Code 기능)

#### Hook 종류

**1. PreToolUse Hook** — 파일 작성 *전*
```bash
# .claude/hooks/pre_tool_use.sh
#!/bin/bash
# Risk file (trading/, config/) 수정 차단

if [[ "$TOOL_NAME" == "str_replace" || "$TOOL_NAME" == "create_file" ]]; then
  if [[ "$FILE_PATH" == *"trading/executor.py"* ||
        "$FILE_PATH" == *"trading/risk_manager.py"* ||
        "$FILE_PATH" == *"trading/capital_manager.py"* ||
        "$FILE_PATH" == *"main_7590.py"* ||
        "$FILE_PATH" == *"config/settings.py"* ]]; then
    echo "🛑 BLOCKED: $FILE_PATH 는 보호 파일 (TIER 1 절대 룰)."
    exit 1
  fi
fi
```

**2. PostToolUse Hook** — 파일 작성 *후* Gate 자동 실행
```bash
# .claude/hooks/post_tool_use.sh
#!/bin/bash
# 코드 변경 후 자동 테스트

if [[ "$TOOL_NAME" == "create_file" || "$TOOL_NAME" == "str_replace" ]]; then
  if [[ "$FILE_PATH" == *.py ]]; then
    # 1. compile 체크
    python -m py_compile "$FILE_PATH"
    if [ $? -ne 0 ]; then
      echo "🛑 컴파일 실패: $FILE_PATH"
      exit 1
    fi
    
    # 2. 관련 테스트 실행
    test_file="tests/test_$(basename $FILE_PATH .py).py"
    if [ -f "$test_file" ]; then
      pytest "$test_file" -q
    fi
  fi
fi
```

**3. UserPromptSubmit Hook** — 운영자 입력 *전*
```bash
# .claude/hooks/user_prompt.sh
#!/bin/bash
# 위험 키워드 감지

if echo "$USER_PROMPT" | grep -iE "(withdraw|출금|레버리지.*10x)" > /dev/null; then
  echo "⚠️ 위험 키워드 감지. 운영자 확인 필요."
  # Slack 알림
  curl -X POST $SLACK_WEBHOOK -d "{\"text\":\"위험 키워드: $USER_PROMPT\"}"
fi
```

#### Polyphemus 사례 (참고)
36,000줄 프로덕션 트레이딩 봇 *Polyphemus*는 다음 hooks를 사용:
- 파일 작성마다 Gate 1 자동 실행
- 비싼 작업을 작은 모델로 라우팅하는 서브에이전트 (점진적 컨텍스트 로딩으로 **44% 비용 절감**)
- 병렬 기능 개발을 위한 agent team
- 안전한 아키텍처 실험을 위한 체크포인팅
- 디버깅용 라이브 DB 직접 접근 MCP 서버

운영자 시스템도 *Phase 1.5 이후* 점진 도입 권장.

#### GMP 비유 — *공정 자동 검증 (In-Process Control)*
- 제조 중 *각 공정 단계*에서 자동 *Critical Control Point (CCP)* 검증
- 코드 작성 중 *각 파일 변경*에서 자동 컴파일 + 테스트
- 둘 다 *불량 조기 차단*

### 3.4.2 Slack/Notion MCP — 알림 + 감사 로그

#### Slack 알림 종류

**1. 거래 발생 알림** (즉시)
```
✅ 진입 발생
  Symbol: SOLUSDT
  Side: LONG
  Quantity: 5.0
  Avg Entry: $142.50
  SL: $137.20 (-3.7%)
  TP1: $150.45 (+5.6%)
  Setup: 1d_tsmom_donchian_long_v1
  Confidence: 0.78
  Reasoning: Donchian 20일 돌파 + EMA200 위 + ADX 28 + BTC risk-off off
```

**2. 이상 신호 알림**
```
🚨 이상 신호 감지
  Type: 5-Agent CRITICAL
  Agent: Ops Agent
  Issue: Kill Switch 응답 없음 (60초)
  Action: 자동 거래 중단
  Time: 2026-05-26 12:35:42 UTC
```

**3. 일일 P&L 요약**
```
📊 Daily Summary (2026-05-26)
  Trades: 2 (1 entry, 1 exit)
  Net P&L: +$23.40 (+2.3%)
  Win Rate: 100%
  Current Equity: $1,023.40
  Open Positions: 1 (SOLUSDT LONG, +1.2%)
  MDD (30d): -8.5%
  Setup Performance:
    - 1d_tsmom_donchian_long_v1: PF 1.38 (n=15)
```

#### Notion DB — 감사 로그 (ALCOA+)

```
Notion DB: "Bot Audit Log"
  Columns:
    - signal_id (Title, UUID)
    - setup_id (Select)
    - timestamp_utc (Date)
    - symbol (Text)
    - action (Select: LONG/SHORT/FLAT/EXIT)
    - confidence (Number)
    - reasoning (Text, 자연어)
    - features_json (Text)
    - cost_estimate_json (Text)
    - raw_data_hash (Text)
    - claude_session_id (Text)
    - 5_agent_verdicts (Text, JSON)
    - executed (Checkbox)
    - exec_result (Text)
    - pnl_realized_pct (Number)
```

#### 운영자 친숙 — 이전 자산 재활용
운영자가 *이전에 만드신* Telegram/Slack 알림 시스템 + Notion DB 자산이 *이미 존재* — Phase 1.5 이후 *그대로 재활용*.

#### GMP 비유 — *Batch Record (배치 기록)*
- Batch Record = 각 생산 배치의 *완전한 추적 기록*
- Notion Audit Log = 각 시그널의 *완전한 ALCOA+ 추적 기록*
- 둘 다 *5년 이상 보관 의무* (운영자 봇은 자율, GMP는 법적 의무)

### 3.4.3 Kill Switch — 긴급 중단

#### 작동 원리
```python
# trading/kill_switch.py
class KillSwitch:
    """
    슬랙에서 한 단어로 봇 즉시 중단.
    
    파일 기반: E:/bot_data/state/KILLSWITCH (존재 = HALT)
    슬랙 명령: "/bot halt" → 파일 생성
    웹훅: POST /killswitch → 파일 생성
    """
    
    KILLSWITCH_FILE = "E:/bot_data/state/KILLSWITCH"
    
    @classmethod
    def is_active(cls) -> bool:
        """모든 거래 결정 *전*에 체크."""
        return os.path.exists(cls.KILLSWITCH_FILE)
    
    @classmethod
    def activate(cls, reason: str, source: str):
        """킬 스위치 활성화."""
        with open(cls.KILLSWITCH_FILE, "w") as f:
            json.dump({
                "activated_at": datetime.utcnow().isoformat(),
                "reason": reason,
                "source": source,  # "manual" / "ops_agent" / "system"
            }, f)
        slack_alert(f"🛑 Kill Switch 활성화\nReason: {reason}\nSource: {source}")
    
    @classmethod
    def deactivate(cls, by: str):
        """운영자만 수동 해제."""
        if by != "operator":
            raise PermissionError("Only operator can deactivate kill switch")
        os.remove(cls.KILLSWITCH_FILE)
        slack_alert(f"✅ Kill Switch 해제 (by {by})")
```

#### Kill Switch 작동 시점
- 시스템 시작 시 (preflight check)
- 매 거래 결정 *전* (모든 시그널마다)
- 매 5분 헬스체크
- 시그널: SIGTERM, SIGINT 받으면 자동 활성화

#### 자동 활성화 조건
1. **일일 손실 -5% 도달** (한도 -3%의 1.67배)
2. **5-Agent CRITICAL 판정**
3. **MCP 연결 끊김 5분 초과**
4. **데이터 stream stale > 3분**
5. **API 키 권한 변경 감지**
6. **외장 SSD disconnect**

#### 운영자 수동 활성화
- Slack: `/bot halt` 또는 `킬스위치` 메시지
- Telegram: 동일
- 웹훅 POST (긴급 시)

#### GMP 비유 — *제품 회수 (Product Recall)*
- 제품 회수 = *심각한 결함* 발견 시 *즉시 시장에서 회수*
- 킬 스위치 = *심각한 시스템 이상* 시 *즉시 거래 중단*
- 둘 다 *조기 차단* 핵심

#### 코드 첫 줄부터 설계 (Polyphemus 권고)
> "Kill Switch는 사후 추가가 아닌 *첫 줄부터* 설계해야 함. 코드 작성 *전*에 '어떻게 멈출 것인가'를 먼저 결정." — Polyphemus

본 시스템도 *Phase 1.5 prompt 작성 시 Kill Switch 명세 *최우선*.

---

# 4. Claude Code 자산 매핑표

## 4.1 자산 카테고리별 매핑

| 카테고리 | Claude Code 자산 | 본 시스템 활용 | 도입 Phase |
|---|---|---|---|
| **데이터** | Binance MCP | 주문/조회/포지션 | Phase 1.5 |
| | Supabase MCP | Phase 1 마이그레이션 / Phase 2 요약 | Phase 1 |
| | Notion MCP | 감사 로그, Setup 대시보드 | Phase 1.5 |
| | Slack MCP | 알림, Kill Switch 트리거 | Phase 1.5 |
| | Telegram (기존) | 일일 P&L (운영자 자산 재활용) | Phase 1 |
| **지능** | Strategy Skill | TSMOM/Donchian, BTC-Beta 등 | Phase 1 |
| | Subagent (Strategy) | 알파 + 다중검정 검증 | Phase 1.5 |
| | Subagent (Risk) | 손실 한도 + MDD 검증 | Phase 1.5 |
| | Subagent (Execution) | 체결 가능성 + slippage | Phase 1.5 |
| | Subagent (Data) | 데이터 품질 + 룩어헤드 | Phase 1 (수동) |
| | Subagent (Ops) | 시스템 헬스 + ALCOA+ | Phase 1.5 |
| **실행** | Binance MCP | post-only LIMIT 주문 | Phase 1.5 |
| | Trading Skill | 룰 기반 실행 | Phase 1.5 |
| **거버넌스** | Hooks (PreToolUse) | 보호 파일 차단 | Phase 1 (즉시) |
| | Hooks (PostToolUse) | 자동 컴파일 + 테스트 | Phase 1 (즉시) |
| | Hooks (UserPrompt) | 위험 키워드 감지 | Phase 1.5 |
| | Kill Switch | 긴급 중단 | Phase 1.5 (필수) |
| | Slack Webhook | 알림 + 명령 | Phase 1.5 |
| | Notion DB | ALCOA+ 감사 로그 | Phase 1.5 |
| **개발** | Claude Code 메인 세션 | 코드 작성 (모든 Phase) | 전체 |
| | pytest hooks | 테스트 자동화 | Phase 1 |
| | git A안 (명시 add) | 보안 우선 커밋 | 전체 |

## 4.2 Phase별 도입 우선순위

### Phase 1 (현재, v3 prompt) — **즉시 도입**
- ✅ Supabase MCP (마이그레이션)
- ✅ Hooks PreToolUse (보호 파일 차단)
- ✅ Hooks PostToolUse (자동 테스트)
- ✅ Strategy Skill (4-후보 정의)
- ✅ Subagent Data (수동, 운영자 검토)
- ✅ Telegram (기존 자산 재활용)

### Phase 1.5 (Micro-live) — **반드시 도입**
- 🔴 **Kill Switch** (코드 첫 줄부터)
- 🔴 **Binance MCP** (실거래 시작)
- 🔴 Slack MCP (Kill Switch 트리거)
- 🔴 Notion DB (감사 로그)
- 🔴 Subagent Risk (자동)
- 🔴 Subagent Execution (자동)
- 🟡 Subagent Strategy (자동)
- 🟡 Subagent Ops (자동, 5분 주기)

### Phase 2 (멀티 엔진) — **확장**
- 🟢 5-Agent 병렬 검토 워크플로우
- 🟢 Hooks UserPromptSubmit (위험 키워드)
- 🟢 Notion 대시보드 (Setup 성과)

### Phase 3 (Microstructure) — **고도화**
- 🟢 Subagent 점진적 컨텍스트 로딩 (44% 비용 절감, Polyphemus)
- 🟢 라이브 DB 직접 접근 MCP (디버깅)
- 🟢 Polyphemus 패턴 체크포인팅

### Phase 4~5 (자율 운영) — **표준화**
- 🟢 모든 5-Agent 자율 작동
- 🟢 자동 Setup Feedback Loop
- 🟢 ML ensemble layer (선택)

---

# 5. 핵심 워크플로우

## 5.1 워크플로우 #1 — 신규 시그널 발생 (실거래)

```
[시간 t]
1. 1d 봉 마감 (00:00 UTC)
   ↓
2. Strategy Skill 평가
   - 50종목 universe 스캔
   - 각 종목 features 계산
   - 진입 조건 충족 → SignalDecision 생성
   ↓
3. ALCOA+ 자동 기록 (Setup Registry + Outbox)
   ↓
4. 5-Agent 병렬 검토 (Strategy + Data + Ops)
   - Strategy Agent: 알파 + 다중검정
   - Data Agent: 데이터 품질 + 룩어헤드
   - Ops Agent: 시스템 헬스
   ↓ ALL PASS
5. Single-Position Rotation — EV 1위 선정
   - 점수가 아닌 *기대값 (EV)* 1위
   - 보유 중이면 STOP (보유 1개만)
   ↓ TOP 후보
6. Risk Agent 검토
   - 일일/주간 손실 한도
   - MDD 예상
   - 단일 종목 집중
   - 레버리지
   ↓ APPROVE
7. Execution Agent 검토
   - 체결 확률
   - Slippage 추정
   - 4-case 분배
   ↓ EXECUTE
8. Binance MCP 주문 (post-only LIMIT)
   ↓
9. 체결 모니터 (5분 timeout)
   ├─ 체결 → 포지션 등록 + SL/TP 자동 주문
   └─ 미체결 → Case C (taker fallback) 또는 Case B (skip)
   ↓
10. ALCOA+ 최종 기록
    - 체결 가격
    - 실제 slippage
    - case 판정
   ↓
11. Slack/Notion 알림
    - 진입 발생
    - 5-Agent 판정 요약
   ↓
12. 포지션 관리 시작 (PositionManager)
```

## 5.2 워크플로우 #2 — 포지션 청산 + 재스캔

```
[보유 중]
1. 매 5분: PositionManager.manage_active_position()
   - SL/TP 자동 조정 (ATR trail)
   - Time stop 체크 (30d)
   ↓
2. 청산 조건 발생
   ├─ SL 도달 → Stop Loss
   ├─ TP1 도달 → 50% 청산
   ├─ TP2 도달 → 30% 추가 청산
   ├─ Trailing stop → 20% 청산
   └─ Time stop → 종가 청산
   ↓
3. Binance MCP 청산 주문
   ↓
4. ALCOA+ 기록 (청산 가격, P&L)
   ↓
5. Slack/Notion 알림
   - 청산 발생
   - P&L 요약
   ↓
6. Single-Position Rotation — 즉시 재스캔
   - 다음 1d 봉까지 대기 X
   - 즉시 SCAN → RANK → WATCH
   - 새 1위 후보 (이전 1위 아님!)
   ↓
7. 새 진입 조건 충족 시 워크플로우 #1 반복
```

## 5.3 워크플로우 #3 — 이상 상황 대응

```
[시스템 정상 운영]
1. Ops Agent 5분 주기 헬스체크
   - CPU, 메모리, 디스크
   - WS 연결 상태
   - Slack/Telegram 응답성
   - Kill Switch 응답성
   - ALCOA+ 준수 (Outbox pending = 0?)
   ↓
2. CRITICAL 판정 발생
   ├─ Kill Switch 응답 없음 (60초)
   ├─ ALCOA+ 위반 (Outbox pending > 100)
   ├─ MCP 연결 끊김 (5분+)
   ├─ 데이터 stream stale (3분+)
   └─ 외장 SSD disconnect
   ↓
3. 자동 Kill Switch 활성화
   ↓
4. 모든 진행 중 주문 *유지* (이미 발주된 것은 그대로)
   - 새 진입 차단
   - 청산 주문은 *수동 진행 OK*
   ↓
5. Slack/Telegram CRITICAL 알림
   - 운영자 즉시 호출
   ↓
6. 운영자 수동 점검 + Kill Switch 해제
```

## 5.4 워크플로우 #4 — Phase 1 검증 (현재)

```
[Phase 1 v3 prompt 실행]
1. 단계 A: 환경 점검 (30분)
   - 문서 view
   - pytest
   - 의존성 확인
   - 운영자 plan 승인
   ↓
2. 단계 B: 외장 SSD + Supabase 마이그레이션 (1~2시간)
   - .env 설정
   - 폴더 구조 생성
   - 데이터 이전
   - 운영자 승인
   ↓
3. 단계 C: 50종목 4~5년 backfill (2~4시간)
   - 1d, 4h, 5m, 1h, funding, OI
   - 상장일 필터
   - 운영자 승인
   ↓
4. 단계 D: 4-후보 사전 검증 (4~6시간)
   - 3-Gate (IC, Decile, Backtest)
   - 후보 1: DSR ≥ 0.95
   - 4-case 분석 + arXiv caveat
   - Setup Registry 등록
   - 운영자 승인
   ↓
5. 단계 E: Context Layer + MTF + Single-Position 시뮬레이션 (3~5시간)
   - AI Research JSON Schema
   - Mock CMC/CoinGecko/Upbit
   - MTF 백테스트 (1d + 4h + 5m)
   - Single-Position vs Multi-Position 비교
   - 운영자 승인
   ↓
6. 단계 F: 보고서 + HANDOFF + GitHub push (1~2시간)
   - STRATEGY_VERIFICATION_REPORT.md
   - CANDIDATE_CONTEXT_LAYER_SPEC.md
   - HANDOFF.md 갱신
   - GitHub A안 push
   - 운영자 최종 보고
   ↓
[Phase 1 결과 판정]
   ├─ R0_QUALIFIED → Phase 1.5 (Micro-live $30~50)
   ├─ CONDITIONAL → 운영자 결정
   └─ ALL FAIL → Stage 3 하드 룰 (자동매매 archetype 재고)
```

---

# 6. 데이터 모델

## 6.1 시그널 (SignalDecision)

```python
# data_models/signal.py
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Literal
import json
from uuid import uuid4
from hashlib import sha256

@dataclass
class SignalDecision:
    """ALCOA+ 완전 호환."""
    
    # === Attributable ===
    signal_id: str  # UUID
    setup_id: str  # "1d_tsmom_donchian_long_v1"
    params_hash: str  # sha256 of params
    claude_session_id: str
    signal_source: str  # "strategy_skill" / "manual" / "test"
    
    # === Legible ===
    reasoning: str  # 자연어
    
    # === Contemporaneous ===
    ts_signal_generated: datetime
    ts_db_recorded: datetime  # 자동
    
    # === Original ===
    raw_data_hash: str  # sha256 of input candles
    features_snapshot_json: str  # 의사결정 시점 features
    
    # === Accurate ===
    immutable: bool = True  # append-only
    
    # === Decision ===
    symbol: str
    action: Literal["LONG", "SHORT", "FLAT", "EXIT"]
    confidence: float  # 0.0 ~ 1.0
    
    # === Cost Estimate (Phase 1 v3) ===
    cost_estimate_json: str  # {case_a, case_b, case_c, case_d}
    
    # === Single-Position Rotation ===
    ev_estimated: float  # 기대값
    rank_in_universe: int  # 1 = top
    
    # === Expires ===
    expires_at: datetime
    
    # === Status ===
    status: Literal["GENERATED", "REVIEWED", "APPROVED", "REJECTED", "EXECUTED", "FAILED"] = "GENERATED"
    
    def to_audit_record(self) -> dict:
        d = asdict(self)
        d["ts_signal_generated"] = self.ts_signal_generated.isoformat()
        d["ts_db_recorded"] = self.ts_db_recorded.isoformat()
        d["expires_at"] = self.expires_at.isoformat()
        return d
```

## 6.2 포지션 (Position)

```python
@dataclass
class Position:
    position_id: str  # UUID
    signal_id: str  # FK to SignalDecision
    setup_id: str
    
    # === Entry ===
    symbol: str
    side: Literal["LONG", "SHORT"]
    quantity: float
    avg_entry_price: float
    ts_entry: datetime
    
    # === Risk Levels ===
    initial_sl: float
    current_sl: float  # trailing stop으로 변경 가능
    tp1: float
    tp2: float
    atr_at_entry: float
    
    # === Status ===
    status: Literal["OPEN", "PARTIAL_CLOSED", "CLOSED"]
    quantity_remaining: float
    
    # === Live ===
    unrealized_pnl_pct: float
    liquidation_price: float
    
    # === Time Stop ===
    expires_at: datetime  # entry + 30d
    
    # === ALCOA+ ===
    raw_order_response_hash: str
    
    # === Exit (when closed) ===
    ts_exit: Optional[datetime] = None
    avg_exit_price: Optional[float] = None
    realized_pnl_pct: Optional[float] = None
    exit_reason: Optional[Literal["TP1", "TP2", "TRAIL", "SL", "TIME_STOP", "MANUAL"]] = None
```

## 6.3 5-Agent 검토 결과

```python
@dataclass
class AgentReview:
    review_id: str  # UUID
    signal_id: str  # FK
    agent: Literal["strategy_quant", "risk", "execution", "data_backtest", "ops_observability"]
    ts: datetime
    verdict: str  # Agent별 다름
    full_json: str  # Agent JSON output 전체
    concerns: list[str]
    recommendations: list[str]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
```

## 6.4 감사 로그 (AuditLog — ALCOA+)

```python
@dataclass
class AuditLog:
    """모든 시스템 이벤트의 영속 기록."""
    
    log_id: str  # UUID
    ts: datetime
    
    # === Event Type ===
    event_type: Literal[
        "SIGNAL_GENERATED",
        "AGENT_REVIEW",
        "ORDER_PLACED",
        "ORDER_FILLED",
        "ORDER_CANCELED",
        "POSITION_OPENED",
        "POSITION_CLOSED",
        "SL_UPDATED",
        "TP_HIT",
        "KILL_SWITCH_ACTIVATED",
        "KILL_SWITCH_DEACTIVATED",
        "SYSTEM_START",
        "SYSTEM_STOP",
        "PHASE_TRANSITION",
        "SETUP_STATUS_CHANGE",
    ]
    
    # === Reference ===
    related_signal_id: Optional[str] = None
    related_position_id: Optional[str] = None
    related_setup_id: Optional[str] = None
    
    # === ALCOA+ ===
    actor: str  # "system" / "agent_<name>" / "operator" / "claude_session"
    payload_json: str
    payload_hash: str  # sha256
    
    # === Linkage (chain) ===
    previous_log_hash: Optional[str] = None  # blockchain-like chain
```

## 6.5 Setup Registry (Phase 1 v3 §10에 정의됨, 재게재)

```sql
CREATE TABLE setup_registry (
    setup_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    params_hash TEXT NOT NULL,
    params_json TEXT NOT NULL,
    last_evaluation_ts TIMESTAMP,
    last_metric_pf REAL,
    last_metric_n INTEGER,
    last_metric_mdd_pct REAL,
    last_metric_dsr REAL,
    gate1_ic REAL,
    gate2_spread REAL,
    gate3_pf REAL,
    reason TEXT,
    notes TEXT
);

CREATE TABLE setup_evaluations (...);  -- (Phase 1 v3 §10-1 참조)
```

## 6.6 ER 다이어그램

```
┌──────────────────┐         ┌──────────────────┐
│ setup_registry   │ 1──n    │ setup_evaluations│
└──────────────────┘         └──────────────────┘
        │
        │ 1
        ▼
        n
┌──────────────────┐         ┌──────────────────┐
│ SignalDecision   │ 1──n    │ AgentReview      │
└──────────────────┘         └──────────────────┘
        │
        │ 1
        ▼
        n
┌──────────────────┐
│ Position         │
└──────────────────┘
        │
        │ n
        ▼
        n
┌──────────────────┐
│ AuditLog         │
└──────────────────┘
```

---

# 7. 안전장치 (ALCOA+ 적용)

## 7.1 5가지 핵심 안전장치 (CLAUDE.md 명시 의무)

### 안전장치 #1: 절대 출금 API 호출 금지
```python
# trading/executor.py 첫 줄 검증
FORBIDDEN_ENDPOINTS = [
    "/sapi/v1/capital/withdraw/apply",
    "/sapi/v1/capital/deposit/address",
    # Spot wallet 관련 모두 금지 (USDT-M Futures만 허용)
]

def validate_endpoint(endpoint: str):
    for forbidden in FORBIDDEN_ENDPOINTS:
        if forbidden in endpoint:
            raise PermissionError(f"FORBIDDEN endpoint: {endpoint}")
```

### 안전장치 #2: 5x 이상 레버리지 금지
```python
MAX_LEVERAGE = 5
DEFAULT_LEVERAGE = 3

def set_leverage(symbol: str, leverage: int):
    if leverage > MAX_LEVERAGE:
        raise ValueError(f"Leverage {leverage} exceeds max {MAX_LEVERAGE}")
    # ...
```

### 안전장치 #3: 일일 -3% 도달 시 봇 중단
```python
def check_daily_loss():
    daily_pnl_pct = compute_daily_pnl()
    if daily_pnl_pct <= -3.0:
        KillSwitch.activate(
            reason=f"Daily loss {daily_pnl_pct}% reached -3% limit",
            source="risk_agent"
        )
```

### 안전장치 #4: 모든 실행 Skills 기본값 dry-run
```python
# Strategy Skills 기본 동작
class TradingSkill:
    DRY_RUN_DEFAULT = True  # 명시적 confirm 없이는 dry-run
    
    def execute(self, order, confirm: bool = False):
        if not confirm and self.DRY_RUN_DEFAULT:
            return self._simulate(order)  # 시뮬레이션만
        # 실거래는 명시적 confirm=True 필요
```

### 안전장치 #5: API 키 권한 최소화
```bash
# Binance API Key 권한 설정 (운영자 작업)
✅ Spot & Margin Trading       — OFF (현물 X)
✅ Futures Trading             — ON  (USDT-M Perp만)
✅ Withdrawals                 — OFF (출금 금지)
✅ Internal Transfer           — OFF
✅ Universal Transfer          — OFF
✅ IP Access Restriction       — ON (집 PC IP만 화이트리스트)
```

## 7.2 ALCOA+ 적용 매트릭스

| ALCOA+ | 구현 |
|---|---|
| **A**ttributable | `signal_id`, `claude_session_id`, `signal_source` 모든 결정에 |
| **L**egible | 자연어 reasoning + JSON 구조화 (사람 + 봇 둘 다 읽기 가능) |
| **C**ontemporaneous | Outbox 패턴 + `ts_db_recorded` (시그널 발생 즉시 DB) |
| **O**riginal | `raw_data_hash` (입력 데이터 sha256 보존) |
| **A**ccurate | append-only (Update 금지, Insert + 새 row) |
| **+ C**omplete | 시그널 + 5-Agent 판정 + 체결 + P&L 모두 |
| **+ C**onsistent | `params_hash` 동일하면 동일 결과 재현 가능 |
| **+ E**nduring | Parquet + SQLite + GitHub + Notion 4중 백업 |
| **+ A**vailable | DuckDB 쿼리, Notion 대시보드, Slack 검색 |

## 7.3 API 키 권한 매트릭스

| 권한 | Spot Account | USDT-M Futures | 본 시스템 사용 |
|---|---|---|---|
| Read | ✓ | ✓ | ✓ (조회) |
| Trade | OFF | ✓ | ✓ (Futures만) |
| Withdraw | OFF | N/A | ❌ (절대 금지) |
| Internal Transfer | OFF | OFF | ❌ |
| Universal Transfer | OFF | OFF | ❌ |
| Margin | OFF | N/A | ❌ |

## 7.4 일일/주간 손실 한도

```python
RISK_LIMITS_HIERARCHY = {
    # Warning level (Slack 경고)
    "daily_warn_pct": -1.5,
    "weekly_warn_pct": -4.0,
    "mdd_warn_pct": 20.0,
    
    # Soft limit (자동 거래 빈도 감소)
    "daily_soft_pct": -2.0,
    "weekly_soft_pct": -6.0,
    
    # Hard limit (Kill Switch 자동 활성화)
    "daily_hard_pct": -3.0,
    "weekly_hard_pct": -8.0,
    "mdd_hard_pct": 25.0,
    
    # Emergency (즉시 전체 청산)
    "daily_emergency_pct": -5.0,
    "weekly_emergency_pct": -12.0,
    "mdd_emergency_pct": 30.0,  # 운영자 허용 한도
}
```

## 7.5 Phase 1 v3와의 연결

Phase 1 v3에서 정의된 *사전확정 11-체크리스트* + *DSR ≥ 0.95* + *4-case 분석* + *Stage 3 하드 룰* 모두 본 안전장치의 *상위 layer*다.

### Stage 3 하드 룰 (10번째 실패 방지)

```python
class Stage3HardRule:
    """
    Phase 1 v3 §6-6 + Stage 3 통합.
    
    Phase 1.5+에서:
      90일 누적 -10% 이하 또는 MDD > 25% → 자동매매 자체 중단
    
    옵션 (운영자 결정):
      A: Manual semi-discretionary 전환
      B: Buy-and-hold + 50d MA cash exit (Grayscale 2023)
      C: 운영자 가설 청취
    """
    
    @classmethod
    def check(cls):
        rolling_90d_pnl = compute_rolling_90d_pnl()
        mdd_90d = compute_mdd_90d()
        
        if rolling_90d_pnl <= -10.0 or mdd_90d > 25.0:
            KillSwitch.activate(
                reason=f"Stage 3 hard rule: 90d PnL {rolling_90d_pnl}%, MDD {mdd_90d}%",
                source="stage_3_rule"
            )
            slack_alert(
                "🛑 STAGE 3 HARD RULE ACTIVATED\n"
                "운영자: 자동매매 archetype 재고 권장\n"
                "옵션:\n"
                "  A. Manual semi-discretionary\n"
                "  B. Buy-and-hold + 50d MA cash exit\n"
                "  C. 운영자 가설 청취"
            )
```

---

# 8. 단계별 구축 로드맵 — GMP 검증 단계 매핑

## 8.1 5-Phase 전체 로드맵

| Phase | 단계 | 기간 | 목표 | GMP 매핑 | Phase 1 v3 통합 |
|---|---|---|---|---|---|
| **1** | 검증 인프라 + 4-후보 사전 검증 | 3~4주 | 외장 SSD 셋업, Phase 1 v3 6단계 실행, R0 후보 식별 | **IQ + MV** (Installation + Method Validation) | ✅ **v3 prompt 그대로** |
| **1.5** | Micro-live Sandbox | 2~4주 | $30~50 소액 실거래, 시스템 검증 (수익 X) | **OQ** (Operational Qualification) | (별도 prompt) |
| **2** | 1d 멀티 엔진 | 4~6주 | PASS 후보(들) 확장 + Single-Position Rotation 실거래 | **PQ Part 1** (Performance Qualification) | (별도 prompt) |
| **3** | Microstructure Execution Filter | 4~6주 | 5m 진입 타이밍, post-only adverse selection 측정 | **PQ Part 2** | (별도 prompt) |
| **4** | 자본 확장 + Setup Feedback Loop | 6~12주 | $100 → $300 → $1,000 단계적 확대, 자율 운영 시작 | **정식 배포 Stage 1** | (별도 prompt) |
| **5** | 자율 운영 + 진화 | 지속 | 4-엔진 자율 평가/비활성, 5-Agent 자동 작동 | **정식 배포 Stage 2** | (별도 prompt) |

## 8.2 Phase 1 — IQ + MV (현재 진행 중)

### 목적
- **IQ**: 외장 SSD, MCP, 환경변수, 의존성 *정상 설치* 확인
- **MV**: 4-후보 전략이 *학계 + 실증 기준*으로 *방법론적으로* 작동하는지 검증

### 산출물
- ✅ `PHASE1_CLAUDE_CODE_PROMPT_V3.md` (이미 발행, 1,904줄)
- 외장 SSD 인프라 셋업
- 50종목 4~5년 backfill
- 4-후보 3-Gate 검증 (IC, Decile, Cost-Aware Backtest)
- 후보 1 사전확정 11-체크리스트 + DSR ≥ 0.95
- Candidate Context Layer 설계 (schema + mock)
- MTF 백테스트 시뮬레이션 (1d + 4h + 5m)
- Single-Position Rotation 백테스트 시뮬레이션
- `STRATEGY_VERIFICATION_REPORT.md`
- `CANDIDATE_CONTEXT_LAYER_SPEC.md`

### Phase 1 PASS 기준 (GMP MV 통과)
- 최소 1개 후보가 *R0_QUALIFIED* (3-Gate + 11-체크리스트 + DSR ≥ 0.95)
- 또는 1개 *CONDITIONAL* + 운영자 진행 결정

### Phase 1 FAIL 시
- **Stage 3 하드 룰 자동 발동**
- 자동매매 archetype 재고 (옵션 A/B/C)

### GMP 비유
- **IQ 실패** = 시약 미입고, 장비 미설치 → MV 시작 불가
- **MV 실패** = 분석 방법 자체가 *유효성 부족* → 정식 분석 시작 불가
- 본 시스템 Phase 1 FAIL = *정식 자동매매 시작 불가*

## 8.3 Phase 1.5 — OQ (Micro-live Sandbox)

### 목적
**OQ** = 시스템이 *실제 운영 조건*에서 *기능적으로* 작동하는지 검증.
- 수익 X, *시스템 검증*만 목적

### 작업
1. **Kill Switch 코드 첫 줄 도입** (필수)
2. Binance MCP 통합 (실거래 첫 연결)
3. Slack MCP + Notion DB 통합
4. 5-Agent 자동 작동 시작 (Risk, Execution, Strategy, Ops)
5. $30~50 소액 자본
6. 1회 리스크 0.05~0.1% ($1.5~5/회)
7. R0_QUALIFIED 후보(들)만 사용
8. 동시 보유 1개 (Single-Position)
9. 2~4주, 실제 거래 5~10건 누적

### 검증 항목
- ✅ 실거래 체결 정상 (post-only 작동)
- ✅ 메이커 체결률 실측 (arXiv 2502.18625 caveat 보정)
- ✅ Adverse selection 측정 (Case D 실제 빈도)
- ✅ Missed trade 비율 (Case B)
- ✅ Taker fallback 빈도 (Case C)
- ✅ DB 기록 무오류 (ALCOA+)
- ✅ Slack/Telegram 알림 정상
- ✅ Kill Switch 작동 (수동 + 자동)
- ✅ 5-Agent JSON 출력 정상
- ✅ Preflight 무오류

### Phase 1.5 PASS 기준
- 모든 검증 항목 ✅
- 시스템 안정 (downtime < 0.5%)
- ALCOA+ 무위반 (Outbox pending 항상 0)

### GMP 비유
- **OQ** = 새 HPLC 장비 *기능 모든 항목 작동* 확인 (재현성, 정확도, 직선성 등)
- 시스템 *기능* OK ≠ *실제 분석 성능* OK (PQ는 별도)

## 8.4 Phase 2 — PQ Part 1 (1d 멀티 엔진)

### 목적
**PQ** = 검증된 시스템의 *실제 성능*이 *예상 사양*과 일치하는지 검증.
- 다른 PASS 후보들도 *실거래 진입* 시작

### 작업
1. Phase 1.5 PASS 후보(들) 확장
2. 추가 엔진 통합 (후보 2, 3, 4 중 PASS된 것)
3. 후보 풀에 *동시 등록* (Setup Registry)
4. Single-Position Rotation으로 *EV 1위* 선정
5. 자본 $100~$300 단계 확장
6. Setup Feedback Loop 시작 (3일/7일/30일 cycle)

### 검증 항목
- 실거래 PF ≥ 1.15 (백테스트 ≥ 1.25의 90%)
- 실거래 MDD ≤ 25%
- 90일 누적 수익 ≥ 5%
- 단일 종목 집중 < 25%
- 5-Agent CRITICAL 0건

### Phase 2 PASS 기준 (GMP PQ Part 1)
- 90일 안정 운영
- 모든 안전장치 무 위반
- Stage 3 하드 룰 미발동

### GMP 비유
- **PQ Part 1** = HPLC 장비로 *실제 제품 분석* 시작, 90일 안정 운영 확인

## 8.5 Phase 3 — PQ Part 2 (Microstructure Filter)

### 목적
- Local Data Lake (Parquet + DuckDB) 도입 — *raw microstructure 수집*
- 5m 진입 타이밍 (호가 imbalance, CVD, spread)
- Adverse selection 본격 측정 (arXiv 2502.18625 캘리브레이션)
- Post-only fill rate 최적화

### 작업
1. WS 실시간 수집기 구축 (depth, trade, liquidation)
2. Tier 분류 적용 (Tier 0~3)
3. Parquet 저장 + DuckDB 분석
4. 5m execution filter 통합
5. 자본 $300~$1,000

### 검증 항목
- 메이커 체결률 향상 (Phase 1.5 baseline 대비 +10%)
- 평균 slippage 감소 (baseline 대비 -20%)
- Case A 분포 향상

### Phase 3 PASS 기준
- 6개월 안정 운영
- ROI 월 5%+ (운영자 최소 목표)

## 8.6 Phase 4 — 정식 배포 Stage 1 (자본 확장)

### 작업
- 자본 $1,000+ 본격 운영
- Setup Feedback Loop 자동화
- 신규 후보 추가 워크플로우 (3-Gate 자동 통과 시 PAPER_ONLY)
- Notion 대시보드 완성

### 단계적 확장
```
Probe ($1,000) ─→ 안정 ─→ Normal ($3,000) ─→ Normal+ ($10,000) ─→ Pro ($30,000+)
```

각 단계 확장 기준:
- PF 유지 (이전 단계 ± 10%)
- MDD ≤ 이전 단계 + 5%
- 5-Agent CRITICAL 0건 (이전 단계)
- 운영자 명시 승인

## 8.7 Phase 5 — 자율 운영 + 진화

### 작업
- 4-엔진 자율 평가/비활성
- 5-Agent 자동 작동
- 신규 후보 자동 backfill + 3-Gate (운영자 검토만)
- ML ensemble layer (선택)

### 운영 모드
- *운영자 개입 최소* (월 1회 검토)
- *5-Agent 자율 결정* (운영자는 감사만)
- *Setup Feedback Loop 완전 자동*

## 8.8 로드맵 차트 (시각화)

```
2026 Q2                              2026 Q3                              2026 Q4                              2027 Q1+
┌────────┐  ┌────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐
│ Phase 1│→│Phase1.5│→│ Phase 2 │→│ Phase 3 │→│ Phase 4 │→│ Phase 5 │→│ Continuous Operation│
│  IQ+MV │  │   OQ   │  │ PQ Part1│  │ PQ Part2│  │  Live   │  │  Auto   │  │                     │
│  3~4w  │  │  2~4w  │  │  4~6w   │  │  4~6w   │  │  6~12w  │  │  지속   │  │                     │
│  현재  │  │        │  │         │  │         │  │         │  │         │  │                     │
└────────┘  └────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────────────────┘
   ↑
운영자
현재 위치
```

---

# 9. 기술 스택 및 참고 저장소

## 9.1 핵심 기술 스택

| 카테고리 | 기술 | 용도 |
|---|---|---|
| **언어** | Python 3.10+ | 모든 봇 코드 |
| **데이터** | Pandas, NumPy, scipy | 시계열 분석 |
| **저장** | Parquet (pyarrow) | 시계열 데이터 |
| | SQLite | 운영 상태, Setup Registry |
| | DuckDB | 분석 쿼리 |
| **검증** | scipy.stats (DSR) | Bailey-Lopez de Prado |
| | pydantic | JSON Schema validation |
| **MCP** | Binance MCP | Futures API |
| | Supabase MCP | Phase 1 마이그레이션 |
| | Slack MCP | 알림 + Kill Switch |
| | Notion MCP | 감사 로그 |
| **거래소** | Binance USDT-M Perp | 주 거래소 |
| **인프라** | 외장 SSD (Samsung T7 Shield) | 데이터 저장 |
| | 집 PC (Windows) | 봇 운영 |
| | Telegram (기존 자산) | 일일 P&L |

## 9.2 참고 저장소 / 학계 자료

### 학계 자료 (필수)

1. **Bailey & Lopez de Prado (2014)**
   - "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality"
   - Journal of Portfolio Management 40(5), SSRN 2460551
   - **본 시스템 적용**: DSR ≥ 0.95 사전확정

2. **arXiv 2502.18625 (Feb 27, 2025)**
   - "The Market Maker's Dilemma: Navigating the Fill Probability vs. Post-Fill Returns Trade-Off" (Albers et al.)
   - 232,897건 실제 Binance BTCUSDT maker 실험
   - **본 시스템 적용**: 메이커 1/3 비용 가정 약화, Case A는 상한 추정

3. **Han, Kang, Ryu (Dec 26, 2023)**
   - SSRN 4675565 — 암호화폐 Time-Series and Cross-Sectional Momentum
   - (28d × 5d) TSMOM Sharpe 1.51
   - **본 시스템 적용**: 후보 1 TSMOM/Donchian Core 직접 근거

4. **Grayscale Research (2023)**
   - "The Trend is Your Friend: Managing Bitcoin's Volatility with Momentum Signals"
   - 50d MAVG Sharpe 1.9 (수수료 미차감)
   - **본 시스템 적용**: Phase 1 FAIL 시 옵션 B 대안

5. **Moskowitz, Ooi, Pedersen (2012)**
   - JFE 104, "Time Series Momentum"
   - 58 futures 자산군 보편적 TSMOM
   - **본 시스템 적용**: TSMOM 학계 보편성 근거

### 참고 트레이딩 봇 (오픈소스)

1. **Polyphemus** (36,000줄)
   - **링크**: chudinnorukam.com (블로그 사례)
   - **특징**: 6주 동안 하루 3-4시간 작업, 첫 2주 비효율 → 구조화 접근으로 성공
   - **본 시스템 적용**: Hooks, 점진적 컨텍스트 로딩 (44% 비용 절감), 체크포인팅

2. **tbot v2** (Strategy Skill 사례)
   - **특징**: Claude가 평가 전에 데이터, 가중치, 임계값, reasoning 표현 *사전 정의*
   - 추론 = DB 저장 = ALCOA+ 자동
   - **본 시스템 적용**: Strategy Skill 패턴 (§3.2.1)

3. **CloddsBot** (Risk Engine 사례)
   - **GitHub**: github.com/CloddsBot (가상 예시)
   - **특징**: 통합 리스크 엔진 (서킷 브레이커, VaR/CVaR, 변동성 체제 감지, 스트레스 테스트)
   - **본 시스템 적용**: Phase 1.5+ 점진 도입

4. **agiprolabs/claude-trading-skills**
   - **특징**: 모든 실행 Skills 기본값 dry-run
   - **본 시스템 적용**: 안전장치 #4 (§7.1)

5. **5-Agent Subagent 검토 실증**
   - **출처**: DEV Community (실증 사례)
   - **결과**: 승률 60% + 순손익 -$39.20
   - **본 시스템 적용**: 5-Agent 표준 패턴 (§3.2.2)

### Anthropic 공식

1. **Claude Code 문서**: docs.claude.com/en/docs/claude-code
2. **MCP 사양**: modelcontextprotocol.io
3. **Hooks 가이드**: docs.claude.com/en/docs/claude-code/hooks
4. **Subagents 가이드**: docs.claude.com/en/docs/claude-code/sub-agents

### 본 프로젝트 문서 (운영자 작성)

1. `docs/HANDOFF.md` — 9개 실패 strategy 이력
2. `docs/STRATEGY_REALISM_REVIEW.md` — 실패 사유 분석
3. `docs/PHASE1_CLAUDE_CODE_PROMPT_V3.md` — Phase 1 실행 명세
4. `CLAUDE.md` — TIER 1 절대 룰 (본 청사진 §10 template)
5. `docs/SYSTEM_DESIGN_BLUEPRINT.md` — 본 문서

---

# 10. 부록

## 10.1 CLAUDE.md 템플릿 (즉시 사용 가능)

운영자가 작업본 루트에 `CLAUDE.md`로 저장. Claude Code 새 세션마다 자동 인식.

```markdown
# CLAUDE.md — BINANCE_FUTURES_BOT 절대 룰

> 본 문서는 Claude Code 모든 세션에서 *자동 인식*되는 TIER 1 절대 룰.
> 위반 시 Claude Code는 *즉시 작업 중단*하고 운영자 보고.

---

## TIER 1 — 절대 금지 (예외 없음)

### 1. 출금 API 금지
- `/sapi/v1/capital/withdraw/*` 호출 절대 금지
- Spot wallet 관련 모든 API 금지 (본 봇은 USDT-M Futures 전용)
- 위반 시 즉시 작업 중단

### 2. 보호종목 거래 금지
- BTCUSDT, ETHUSDT, HOLOUSDT, CFXUSDT, LYNUSDT, INJUSDT
- 진입 후보에서 모두 제외
- BTC/ETH는 시장 인덱스 read-only만 허용

### 3. 5x 이상 레버리지 금지
- MAX_LEVERAGE = 5
- DEFAULT_LEVERAGE = 3
- 위반 시 즉시 차단

### 4. 일일 -3% 도달 시 봇 중단
- 일일 손실 -3% 도달 → Kill Switch 자동 활성화
- 운영자 수동 해제 후만 재개

### 5. 보호 파일 수정 금지
- `trading/executor.py`
- `trading/risk_manager.py`
- `trading/capital_manager.py`
- `main_7590.py`
- `config/settings.py` (보호종목 부분)
- 수정 시도 시 Hook으로 자동 차단

### 6. 시크릿/토큰/API 키 출력 금지
- 코드, 로그, 메시지, 보고서 일체
- `.env` 파일 절대 git 커밋 금지
- `git add .` 또는 `-A` 절대 금지 (보안)

### 7. 모든 Skills 기본값 dry-run
- 실거래는 명시적 `confirm=True` 필요
- 시뮬레이션이 기본

### 8. 사전확정 임계 변경 금지
- Phase 1 v3 §6-4 11-체크리스트 임계 절대 X
- 변경 = 새 setup_id (다중검정 자동 카운트)

### 9. 9개 실패 strategy 재시도 금지
- OI-급증, 돌파 1h, 펀딩 페이드, ORB, CSM, LCR, CCS-Lite, ML EV
- 본 세션은 4-후보 *사전 검증*만

### 10. AI 리서치 직접 진입 신호 금지
- AI 리서치는 후보선정 + 위험 차단만
- 직접 LONG/SHORT 결정 X
- 포지션 사이즈 결정 X

---

## TIER 2 — 운영자 명시 승인 필요

### 1. 단계 진입
- 단계 A → B → C → D → E → F 진입 전 *운영자 명시 승인*
- 자동 진행 금지

### 2. 코드 변경
- Plan 보여주고 운영자 승인 *후*에만
- `git add <파일명 명시>` (변경 파일 *명시*)

### 3. 자본 확장
- $30 → $100 → $300 → $1,000 → $3,000 → $10,000+
- 각 단계 *운영자 명시 승인*

### 4. 새 Setup 등록
- Setup Registry 신규 등록 시 *운영자 검토*
- 자동 등록은 PAPER_ONLY까지만

---

## TIER 3 — 자동 (운영자 통보)

### 1. Kill Switch 자동 활성화
- 일일 -3% 도달
- 5-Agent CRITICAL
- MCP 연결 끊김 5분+
- 데이터 stale 3분+
- 외장 SSD disconnect

### 2. Setup 자동 비활성화
- 연속 5패 → COOLING (24h)
- 동일 종목 연속 2패 → COOLDOWN (12h)
- 90일 PF < 0.85 → DISABLED

### 3. 자동 알림 (Slack/Telegram)
- 거래 발생 시
- Kill Switch 활성화 시
- 일일 P&L 요약 (매일 22:00 KST)
- 주간 요약 (매주 일요일 22:00 KST)
- 월간 보고서 (매월 1일 22:00 KST)

---

## 작업 절차

### 새 세션 시작 시
1. `view` 본 CLAUDE.md
2. `view` docs/HANDOFF.md
3. `pytest tests/ -q` (367+ passed 확인)
4. 운영자 plan 보고 + 승인 대기

### 코드 변경 시
1. Plan 명시 + 운영자 승인
2. `pytest` 변경 후 통과 확인
3. `git add <파일명>` (-A 금지)
4. 커밋 메시지에 변경 사유 명시

### 단계 전환 시
1. 현재 단계 산출물 보고
2. 운영자 검토 + 명시 승인
3. 다음 단계 plan 보고
4. 승인 후 진행

---

## 학계 + 실증 근거 (변경 금지)

### Bailey & Lopez de Prado (2014)
- DSR (Deflated Sharpe Ratio) ≥ 0.95
- 다중검정 보정 필수
- effective N = 110 (운영자 9개 + 4-후보 × 5 파라미터)

### arXiv 2502.18625 (Feb 2025)
- 메이커 232,897건 실증
- 메이커 1/3 비용 가정 약화
- Case A는 상한 추정, Case C 보수 기준

### Han·Kang·Ryu (2023) SSRN 4675565
- TSMOM (28d × 5d) Sharpe 1.51
- Long-or-Flat 권장
- Cross-sectional momentum 약함

### Grayscale (2023)
- 50d MAVG Sharpe 1.9 (수수료 미차감)
- Phase 1 FAIL 시 옵션 B 대안

---

## 거버넌스

### Kill Switch
- 파일: `E:/bot_data/state/KILLSWITCH`
- Slack: `/bot halt`
- Telegram: `킬스위치`
- 운영자만 해제 가능

### 5-Agent 검토
- Strategy + Risk + Execution + Data + Ops
- 모든 시그널마다 자동 작동 (Phase 1.5+)
- ALL PASS 후만 주문

### ALCOA+
- 모든 결정 → Notion DB + Parquet + SQLite
- append-only (Update 금지)
- params_hash로 재현성 보장

---

## 끝

본 룰은 *프로젝트 전체 수명* 유지. 변경 시 운영자 명시 승인 + GitHub commit 의무.
```

## 10.2 환경변수 (.env) 템플릿

```bash
# .env — BINANCE_FUTURES_BOT 환경변수

# ==========================================
# Binance API
# ==========================================
USE_TESTNET=false  # Phase 1.5 시작 시 true 변경 (paper-trade)
BINANCE_API_KEY=<운영자 키, IP 화이트리스트 ON>
BINANCE_API_SECRET=<운영자 시크릿>

# Testnet (Phase 1.5)
BINANCE_TESTNET_API_KEY=<testnet 키>
BINANCE_TESTNET_API_SECRET=<testnet 시크릿>

# ==========================================
# 외장 SSD
# ==========================================
BOT_DATA_DIR=E:/bot_data  # 운영자 외장 SSD 드라이브 문자

# ==========================================
# Supabase (Phase 1에서 비활성화)
# ==========================================
SUPABASE_ENABLED=false  # 로컬 only
SUPABASE_URL=https://aoeqgptytzuhvvchsgul.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<운영자 키>

# ==========================================
# Telegram (기존 자산 재활용)
# ==========================================
TELEGRAM_BOT_TOKEN=<운영자 토큰>
TELEGRAM_CHAT_ID=<운영자 chat id>

# ==========================================
# Slack MCP (Phase 1.5)
# ==========================================
SLACK_MCP_URL=https://mcp.slack.com/mcp
SLACK_WEBHOOK_URL=<운영자 webhook>
SLACK_CHANNEL_ALERTS=#bot-alerts
SLACK_CHANNEL_TRADES=#bot-trades

# ==========================================
# Notion MCP (Phase 1.5)
# ==========================================
NOTION_MCP_URL=https://mcp.notion.com/mcp
NOTION_DB_AUDIT_LOG=<audit log DB id>
NOTION_DB_SETUP_DASHBOARD=<setup dashboard DB id>

# ==========================================
# AI Research (Phase 1: mock, Phase 1.5+: 실제)
# ==========================================
OPENAI_API_KEY=<운영자 키>  # mock 또는 실제
AI_RESEARCH_MODE=mock  # mock | live

# ==========================================
# 보호종목 (TIER 1, 변경 금지)
# ==========================================
PROTECTED_SYMBOLS=BTCUSDT,ETHUSDT,HOLOUSDT,CFXUSDT,LYNUSDT,INJUSDT

# ==========================================
# 거래 한도 (TIER 1)
# ==========================================
MAX_LEVERAGE=5
DEFAULT_LEVERAGE=3
MAX_OPEN_POSITIONS=1  # Single-Position Rotation

DAILY_LOSS_HARD_PCT=-3.0
WEEKLY_LOSS_HARD_PCT=-8.0
MDD_HARD_PCT=25.0
MDD_EMERGENCY_PCT=30.0

# ==========================================
# Logging
# ==========================================
LOG_LEVEL=INFO
LOG_DIR=E:/bot_data/logs

# ==========================================
# CMC / CoinGecko (Phase 1.5+)
# ==========================================
CMC_API_KEY=<운영자 키, 무료 333/day>
COINGECKO_API_KEY=<운영자 키, 무료 30/min>

# ==========================================
# Upbit (Phase 1.5+)
# ==========================================
UPBIT_OPEN_API_KEY=<운영자 키>
UPBIT_OPEN_API_SECRET=<운영자 시크릿>
```

## 10.3 단계별 체크리스트

### Phase 1 시작 전 체크리스트
- [ ] 외장 SSD 1TB 준비 (Samsung T7 Shield 권장)
- [ ] 집 PC USB 포트 연결, 드라이브 문자 확인
- [ ] 작업본 git 상태 클린 (`git status`)
- [ ] `pytest tests/ -q` 367+ passed
- [ ] Python 3.10+ 설치
- [ ] 의존성 설치: `scipy, pydantic, pyarrow, duckdb`
- [ ] Supabase MCP 접근 가능
- [ ] Telegram 알림 작동
- [ ] `.env` 파일 시크릿 모두 채워짐
- [ ] `CLAUDE.md` 작업본 루트에 저장 (본 §10.1)
- [ ] `PHASE1_CLAUDE_CODE_PROMPT_V3.md` `docs/` 폴더에 저장

### Phase 1 진행 중 체크리스트 (단계별)
**단계 A** (환경 점검):
- [ ] 15개 문서 view 완료
- [ ] pytest 통과
- [ ] 의존성 확인
- [ ] 운영자 plan 승인

**단계 B** (외장 SSD):
- [ ] `.env` BOT_DATA_DIR, SUPABASE_ENABLED 추가
- [ ] `E:/bot_data/` 폴더 구조 생성
- [ ] Supabase 마이그레이션 무결성 검증
- [ ] persistence.py local-only mode 작동
- [ ] 운영자 승인

**단계 C** (Backfill):
- [ ] 1d ohlcv 50종목 × 5년
- [ ] 4h ohlcv 50종목 × 5년
- [ ] 5m ohlcv 50종목 × 1년
- [ ] funding 50종목 × 5년
- [ ] OI 50종목 × 1년
- [ ] universe_meta.parquet
- [ ] DuckDB 적재 검증
- [ ] 운영자 승인

**단계 D** (4-후보 검증):
- [ ] feature_engineering.py
- [ ] ic_analysis.py (Gate 1)
- [ ] decile_analysis.py (Gate 2)
- [ ] simple_backtest.py (Gate 3)
- [ ] dsr_calculator.py
- [ ] 4-후보 모두 3-Gate 실행
- [ ] 후보 1 11-체크리스트 + DSR ≥ 0.95
- [ ] Setup Registry 등록
- [ ] 운영자 승인

**단계 E** (Context Layer + MTF + Single-Position):
- [ ] context_layer.py (Mock)
- [ ] ai_research_schema.py (Pydantic)
- [ ] mtf_backtest.py (1d + 4h + 5m)
- [ ] single_position_rotation.py
- [ ] 후보 1 MTF A/B/C 비교
- [ ] 후보 1 Multi vs Single 비교
- [ ] 운영자 승인

**단계 F** (보고서):
- [ ] STRATEGY_VERIFICATION_REPORT.md
- [ ] CANDIDATE_CONTEXT_LAYER_SPEC.md
- [ ] HANDOFF.md 갱신
- [ ] GitHub A안 push
- [ ] 운영자 최종 보고

### Phase 1.5 진입 체크리스트
- [ ] Phase 1 R0_QUALIFIED 후보 1개 이상
- [ ] Kill Switch 코드 첫 줄 도입
- [ ] Binance MCP 통합 (실거래 키, IP 화이트리스트)
- [ ] Slack MCP 통합 (Kill Switch 트리거)
- [ ] Notion DB 통합 (Audit Log)
- [ ] 5-Agent 자동 작동 (Risk, Execution, Strategy, Ops)
- [ ] $30~50 소액 자본 입금
- [ ] 1회 리스크 0.05~0.1% 설정
- [ ] 운영자 명시 GO

### Phase 2 진입 체크리스트
- [ ] Phase 1.5 검증 모두 ✅
- [ ] 시스템 안정 (downtime < 0.5%)
- [ ] ALCOA+ 무위반
- [ ] 추가 PASS 후보 통합 준비
- [ ] 운영자 명시 GO

### Phase 3 진입 체크리스트
- [ ] Phase 2 90일 안정 운영
- [ ] PF ≥ 1.15 실거래
- [ ] MDD ≤ 25%
- [ ] WS 수집기 24h 안정
- [ ] Tier 분류 universe 확정
- [ ] 운영자 명시 GO

### Stage 3 하드 룰 발동 시 체크리스트
- [ ] Kill Switch 자동 활성화 확인
- [ ] 모든 포지션 *유지* (이미 발주 X 신규만 차단)
- [ ] 운영자 즉시 Slack/Telegram 호출
- [ ] 옵션 결정:
  - [ ] A. Manual semi-discretionary 전환
  - [ ] B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)
  - [ ] C. 운영자 가설 청취
- [ ] 자동매매 archetype 재고 회의 (운영자 본인)

## 10.4 워크플로우 다이어그램 (요약)

```
[시스템 시작]
   ↓
[CLAUDE.md 자동 인식]
   ↓
[Kill Switch 체크] ─→ HALT 시 즉시 종료
   ↓ 정상
[데이터 수집] (Binance MCP, WS)
   ↓
[Strategy Skill 평가]
   ↓
[ALCOA+ 자동 기록] (Outbox + Notion + Parquet)
   ↓
[5-Agent 병렬 검토]
   │
   ├─ Strategy Agent (알파)
   ├─ Data Agent (품질)
   └─ Ops Agent (시스템)
   ↓ ALL PASS
[Single-Position Rotation] (EV 1위)
   ↓ TOP 후보
[Risk Agent] (한도 검증)
   ↓ APPROVE
[Execution Agent] (체결 가능성)
   ↓ EXECUTE
[Binance MCP 주문] (post-only LIMIT)
   ↓
[체결 모니터]
   │
   ├─ 체결 → 포지션 등록 + SL/TP
   ├─ 미체결 (case B) → skip
   └─ 미체결 + fallback (case C) → taker
   ↓
[Slack/Notion 알림]
   ↓
[포지션 관리 (5분 주기)]
   │
   ├─ SL/TP 자동 조정 (ATR trail)
   ├─ Time stop 체크 (30d)
   └─ Manual 청산 (kill switch)
   ↓ 청산
[ALCOA+ 최종 기록] (P&L, 이유)
   ↓
[Single-Position Rotation 즉시 재스캔]
   ↓
[반복]
```

## 10.5 비상 절차 (Emergency Procedures)

### 비상 #1: Kill Switch 응답 없음
1. 운영자 PC 직접 접근
2. `E:/bot_data/state/KILLSWITCH` 파일 수동 생성 (touch)
3. Binance 웹/앱에서 *직접 청산* (수동)
4. API 키 *비활성화* (Binance 설정)
5. 사후 디버깅 후 재개

### 비상 #2: 외장 SSD disconnect
1. 자동 Kill Switch 활성화 (Ops Agent)
2. 봇 자동 중단
3. SSD 재연결 + `pytest`
4. 데이터 무결성 검증 (DuckDB)
5. 운영자 명시 재개

### 비상 #3: Binance API 차단
1. IP 화이트리스트 확인
2. 의심스러운 활동 감지 (Binance 알림)
3. API 키 *비활성화* + 신규 발급
4. `.env` 업데이트
5. 운영자 명시 재개

### 비상 #4: 5-Agent CRITICAL
1. 즉시 Kill Switch 활성화
2. CRITICAL 사유 Slack 알림
3. 운영자 검토 + 원인 파악
4. 시스템 패치 + `pytest`
5. 운영자 명시 재개

### 비상 #5: 90일 -10% 도달 (Stage 3)
1. Kill Switch 자동 활성화
2. Stage 3 하드 룰 발동
3. 옵션 A/B/C 결정 회의
4. **자동매매 archetype 재고**
5. 운영자 본인 솔직한 판단

## 10.6 GMP-Trading 매핑 요약

| GMP 개념 | 본 시스템 |
|---|---|
| **SOP** (Standard Operating Procedure) | Strategy Skill (`tsmom_donchian_skill.py`) |
| **Change Control** | Setup Registry params_hash |
| **OOS** (Out Of Specification) | 사전확정 임계값 미달 → DISABLED |
| **OOT** (Out Of Trend) | Setup Performance drift → COOLING |
| **CAPA** (Corrective and Preventive Action) | Phase 1 FAIL → Stage 3 하드 룰 옵션 A/B/C |
| **Batch Record** | Notion DB AuditLog |
| **In-Process Control** | Claude Code Hooks (자동 게이트) |
| **Hold/Release** | Risk Agent APPROVE/REJECT |
| **Product Recall** | Kill Switch 활성화 |
| **IQ/OQ/PQ** | Phase 1/1.5/2~3 |
| **ALCOA+** | SignalDecision + AuditLog 영속 기록 |
| **5-Agent 검토** | QC 검사 단계 (원료/공정/최종/출하/사후) |

## 10.7 7명 외부 평가 + 운영자 비전 수렴 요약

| 평가 | 점수 | 핵심 기여 |
|---|---|---|
| 1차 외부 AI | 7.5/10 | OOS>IS 과장 정정, ZEC 집중 우려 |
| 2차 (자료) | 6.5/10 | 인프라 좋음, 5분 알파 위험 |
| 3차 외부 AI | 8.5/10 | 메이커 4-case 분해 |
| 4차 외부 AI | 8.8/10 | 상장일 필터, 연도별 PF 현실화, Phase 1.5 추가 |
| 5차 deep research | 8.5/10 | 실무 구현 강함 |
| 6차 내 학계 리서치 | 8.5/10 | DSR, arXiv, Han·Kang·Ryu |
| 7차 운영자 최종 plan | 9.0/10 | 4-후보 + Context Layer + Single-Position 종합 vision |
| **8차 운영자 시스템 청사진** | **9.5/10** | **4-Layer + 5-Agent + ALCOA+ + GMP 매핑** |

→ **8개 평가 *완벽 수렴*** = 본 청사진 v1.0의 기반.

---

## 10.8 운영자 자산 활용 매핑 (광동제약 QC2 경험)

| 운영자 기존 자산 | 본 시스템 활용 |
|---|---|
| GMP/QC 사고방식 | 전체 청사진 *기반* (Phase 1~5 = IQ→MV→OQ→PQ→배포) |
| OOS/OOT 처리 경험 | Setup Registry + Stage 3 하드 룰 |
| ALCOA+ 원칙 익숙 | SignalDecision + AuditLog *자동* 적용 |
| Google Apps Script 능력 | Notion DB 보고서 자동 생성 (Phase 1.5+) |
| Python 능력 | 봇 코드 직접 검토 가능 |
| Tableau 능력 | Phase 4+ 대시보드 (Notion 보완) |
| n8n / Dify 경험 | MCP Connector 패턴 친숙 |
| Telegram 알림 시스템 | 일일 P&L 알림 *그대로 재활용* |
| LIMS 자동화 경험 | Binance MCP 통합 (LIMS API와 동형) |
| Method Validation 경험 | Phase 1 3-Gate + DSR (MV와 동형) |
| Stability Testing 경험 | Phase 1.5+ 90일 운영 (안정성 시험과 동형) |
| MFDS 사찰 대비 경험 | ALCOA+ 감사 로그 (사찰 대비와 동형) |

---

# 끝.

## 본 청사진의 가치

> **"운영자의 광동제약 QC2 + 품질부문 AI TF Lead 경험을 *자동매매에 그대로 이식*한 청사진. 9번의 실패 + 7명 외부 평가 + 학계 메타-분석 + 운영자 본인 비전 + 4-Layer 아키텍처 + GMP 매핑 = 본 v1.0. Phase 1 v3 prompt와 *상호 보완*적으로 작동하여, 운영자의 자동매매 봇 프로젝트를 *제약 산업 품질 관리 수준*으로 구축한다."**

## 다음 단계

1. **Phase 1 v3 실행** (`PHASE1_CLAUDE_CODE_PROMPT_V3.md`)
   - 4-후보 검증 + Context Layer + MTF + Single-Position
   - 6단계 (A~F), 12~20시간, 3~5일 분산

2. **결과에 따른 분기**:
   - R0_QUALIFIED → Phase 1.5 prompt (별도 작성 예정)
   - CONDITIONAL → 운영자 결정
   - ALL FAIL → Stage 3 하드 룰 (옵션 A/B/C)

3. **본 청사진 update**:
   - Phase 1 결과를 §8.2 추가
   - Phase 1.5 prompt 작성 시 §10.3 체크리스트 활용
   - Phase 2~5 진입마다 §8 진행 상황 update

---

## 변경 이력

| 버전 | 날짜 | 변경 | 작성 |
|---|---|---|---|
| v1.0 | 2026-05-26 | 최초 작성 (10개 섹션, GMP 매핑, 5-Agent, CLAUDE.md template) | Claude Opus 4.7 |

---

**Status**: ACTIVE — Phase 1 실행 중. 결과 후 v1.1 update 예정.
