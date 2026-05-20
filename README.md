# Binance Futures Bot v3.1 — Starter Kit

> Claude Code에서 명세서 기반으로 코드 작성을 시작하기 위한 스타터 패키지

## 시작 전 알아둘 점 (중요)

⚠️ **이 봇은 자동매매 봇입니다. 실전 자본 손실 가능성이 매우 높습니다.**
학술 데이터상 단타 트레이더의 80~97%는 손실로 종료합니다 (Chague 2020, Barber 2014, BIS WP#1087). 본 시스템의 목표는 "수익 극대화"가 아니라 **"잃는 90~97%에 들지 않는 것"** 입니다.

명세서 §1-1, §16을 반드시 읽고 시작하세요.

---

## 폴더 구조

```
binance-bot-starter/
├── CLAUDE.md                          ← Claude Code 자동 로드 (수정 가능)
├── README.md                          ← 이 파일
├── requirements.txt
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── docs/
│   ├── SPEC_v3.1.md                   ← 명세서 (직접 넣어야 함, 아래 참조)
│   ├── SESSION_PROMPTS.md             ← 12개 세션 프롬프트
│   └── MODULE_CHECKLIST.md            ← 모듈별 검증 체크리스트
│
├── config/, strategy/, sizing/, analytics/, ops/, trading/
├── data/, backtesting/, db/, tests/
├── reports/, backtests/, logs/        ← 런타임 산출물
```

---

## 셋업 (10분)

### Step 1. 명세서 파일 추가 (필수)

이전 채팅에서 받은 `BINANCE_FUTURES_BOT_SPEC_v3.1.md` 파일을 다음 위치로 복사:

```bash
cp ~/Downloads/BINANCE_FUTURES_BOT_SPEC_v3.1.md docs/SPEC_v3.1.md
```

확인:
```bash
ls -lh docs/SPEC_v3.1.md
# -rw-r--r-- ... 약 200KB
wc -l docs/SPEC_v3.1.md
# 약 5,160줄
```

### Step 2. Python 가상환경 + 패키지 설치

```bash
# Python 3.10 이상 확인
python --version    # 3.10.x 이상

# 가상환경 생성
python -m venv venv

# 가상환경 활성화
source venv/bin/activate              # macOS/Linux
# venv\Scripts\activate               # Windows

# 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3. 환경 변수 설정

```bash
# .env 파일 생성
cp .env.example .env

# 에디터로 .env 편집
nano .env    # 또는 vi, code 등
```

**최소 설정 (Testnet 페이퍼 트레이딩)**:
```
# Testnet 키는 반드시 BINANCE_TESTNET_* 슬롯에 넣는다 (live 슬롯과 분리)
BINANCE_TESTNET_API_KEY=<testnet API 키>
BINANCE_TESTNET_API_SECRET=<testnet API 시크릿>
USE_TESTNET=true
OPENAI_API_KEY=sk-<OpenAI 키>
```

**실거래 설정**:
```
# 실거래 키는 BINANCE_API_KEY/SECRET 슬롯 전용 (Futures only / 출금·이체 비활성 권장)
BINANCE_API_KEY=<live API 키>
BINANCE_API_SECRET=<live API 시크릿>
USE_TESTNET=false
OPENAI_API_KEY=sk-<OpenAI 키>
```

> 키 슬롯 분리(중요): `USE_TESTNET=true` 면 `BINANCE_TESTNET_API_KEY/SECRET` 만,
> `USE_TESTNET=false` 면 `BINANCE_API_KEY/SECRET` 만 사용한다. testnet 키가 없을 때
> live 키로 폴백하지 않으므로(자격증명 교차 방지), 슬롯을 정확히 채워야 한다.

Testnet API 키 발급: https://testnet.binancefuture.com/en/futures/BTCUSDT (가입 후 API Key 메뉴)

### Step 4. Git 초기화 (선택)

```bash
git init
git add .
git commit -m "Initial commit: v3.1 starter kit"
# 단, .env가 .gitignore에 포함되어 있는지 확인
git status   # .env가 보이면 안 됨
```

---

## Claude Code 실행

### Step 1. Claude Code 시작

```bash
# 프로젝트 루트에서
claude
```

### Step 2. 첫 세션 — 환경 검증

`docs/SESSION_PROMPTS.md`의 **세션 0**을 그대로 복사해서 Claude Code에 붙여넣기:

```
docs/SPEC_v3.1.md과 CLAUDE.md를 view하고, 다음을 확인해줘:
... (전체 프롬프트는 SESSION_PROMPTS.md 참조)
```

Claude Code가 환경을 검증하고 OK 사인을 주면 다음 단계로.

### Step 3. 모듈별 작업 (총 12개 세션)

```
세션 1: DB 스키마
세션 2: Config
세션 3: CostGuard
세션 4: DynamicPositionSizer
세션 5: RegimeDetector
세션 6: MacroEventAnalyzer
세션 7: SystemHealthMonitor
세션 8: PairWhitelist
세션 9: RiskManager
세션 10: WeeklyGPTAnalyst
세션 11: BacktestEngine
세션 12: main_7590.py 통합
```

각 세션 후 반드시:
1. ✅ `pytest tests/test_<module>.py` 통과 확인
2. ✅ `docs/MODULE_CHECKLIST.md` 해당 항목 체크
3. ✅ `/clear` 입력으로 컨텍스트 초기화
4. ✅ 다음 세션 프롬프트로 새로 시작

### Step 4. 통합 검증

12개 세션 완료 후:

```bash
# 전체 테스트
pytest tests/ -v

# Dry-run (실제 거래 없이)
USE_TESTNET=true python main_7590.py --dry-run --duration 60
```

### Step 5. Phase 0 — 백테스트 검증

명세서 §10에 따라 24~36개월 데이터로 walk-forward 백테스트 실행.

```bash
# 데이터 다운로드 (별도 스크립트 작성 필요)
python -m backtesting.download_data --pair BTCUSDT --years 3

# Walk-forward 실행
python -m backtesting.walk_forward --config backtests/config_v3.1.json
```

**합격 기준** (명세서 §10-4):
- OOS Sharpe ≥ 1.0
- OOS Profit Factor ≥ 1.2
- 최악 윈도우 MDD < 20%
- 거래 200건 이상

### Step 6. 페이퍼 8주

Phase 0 통과 시, Testnet에서 8주 페이퍼 트레이딩 (명세서 §11).
실전 전환 5대 기준 충족 시에만 실전 시작.

---

## 트러블슈팅

### Claude Code가 명세서를 참조하지 않음
프롬프트 첫 줄에 `docs/SPEC_v3.1.md §X-X를 먼저 view해줘`를 명시.

### `pytest`가 모듈을 찾지 못함
```bash
# 프로젝트 루트에 conftest.py가 없으면 추가
cat > conftest.py << 'EOF'
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
EOF
```

### Binance API 에러 "Invalid API-key, IP, or permissions"
- API 키 권한 확인 (Futures 활성화)
- IP 화이트리스트 확인
- Testnet과 Mainnet 키 혼용 확인

### OpenAI API 에러
- API 키 만료 또는 크레딧 부족 확인
- WeeklyGPTAnalyst는 폴백 모드로 작동하므로 봇은 계속 작동

---

## 핵심 안전 룰 (반복)

1. **명세서 §11 페이퍼 8주 + 5대 기준 충족 전 실전 자본 투입 금지**
2. **명세서 §9 RISK_RULES는 절대 임의 완화 금지**
3. **API 키 출금 권한 비활성화**
4. **자본 50%+ 콜드월렛 분리 (명세서 §12-3)**
5. **수동 개입 금지 (명세서 §12-2)**

---

## 추가 자료

- **명세서**: `docs/SPEC_v3.1.md` — 모든 설계 결정의 단일 진실 출처
- **세션 가이드**: `docs/SESSION_PROMPTS.md` — Claude Code 작업 프롬프트 12개
- **검증 체크리스트**: `docs/MODULE_CHECKLIST.md` — 모듈별 완료 검증
- **Claude Code 가이드**: `CLAUDE.md` — Claude Code가 자동 로드

---

## 라이선스

이 코드는 개인 운영 목적입니다. 재배포·상업적 이용·복제 금지.

## Disclaimer

본 시스템은 자동매매 봇으로, 사용으로 인한 모든 손실은 운영자 책임입니다. 사용 전 명세서 §1-1, §16을 반드시 숙지하세요.
