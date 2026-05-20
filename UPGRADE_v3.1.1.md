# v3.1 → v3.1.1 업그레이드 가이드

> **변경 사유**: 운영자 수동 거래 자산(BTC/ETH/HOLO) 보호, Spot/Futures 자산 분리, 마진 락과 사용 가능 잔고 명확 구분
> **영향**: 신규 모듈 1개 + 기존 모듈 3개 수정 + 신규 DB 테이블 2개
> **소요 시간**: 약 5분 (파일 교체만)

---

## 무엇이 변경되었는가

### 핵심 변경 사항

1. **신규 모듈**: `CapitalManager` (`data/capital_manager.py`)
2. **수정 모듈**: `PairWhitelist`, `RiskManager`, `main_7590.py`
3. **신규 DB 테이블**: `capital_initial`, `capital_daily_snapshot`
4. **신규 trades 컬럼**: `wallet_balance_at_entry`, `available_at_entry`, `locked_margin_at_entry`
5. **신규 Config**: `CapitalManagerConfig`, `PairWhitelistConfig.protected_symbols`

### 무엇이 보호되는가

```
✅ Spot 지갑의 BTC/ETH/HOLO       → 봇이 절대 건드리지 않음
✅ Futures의 BTCUSDT/ETHUSDT 페어 → 봇이 절대 거래하지 않음
✅ Spot 가격 변동                  → 봇 자본 계산에 영향 없음
✅ Futures 마진 락                 → 사이즈 계산에 자동 반영
```

---

## 적용 방법 (4가지 파일 교체)

### Step 1. 기존 v3.1 스타터 패키지가 있다면

```bash
cd binance-bot-starter
```

기존 폴더가 있어야 합니다. 없으면 `binance-bot-starter.zip`을 먼저 풀어주세요.

### Step 2. 4개 파일을 새 버전으로 교체

이 패키지(`binance-bot-starter-v3.1.1-patch.zip`)의 4개 파일을 기존 위치에 덮어쓰기:

| 새 파일 | 덮어쓸 위치 |
|---|---|
| `CLAUDE.md` | `binance-bot-starter/CLAUDE.md` |
| `docs/SESSION_PROMPTS.md` | `binance-bot-starter/docs/SESSION_PROMPTS.md` |
| `docs/MODULE_CHECKLIST.md` | `binance-bot-starter/docs/MODULE_CHECKLIST.md` |
| `docs/SPEC_v3.1_APPENDIX_E.md` | `binance-bot-starter/docs/SPEC_v3.1_APPENDIX_E.md` (신규) |

**Mac/Linux**:
```bash
cd ~/Downloads
unzip binance-bot-starter-v3.1.1-patch.zip
cp -r binance-bot-starter-v3.1.1/* ~/projects/binance-bot-starter/
```

**Windows (PowerShell)**:
```powershell
cd $HOME\Downloads
Expand-Archive -Path binance-bot-starter-v3.1.1-patch.zip -DestinationPath .
Copy-Item -Recurse -Force binance-bot-starter-v3.1.1\* $HOME\projects\binance-bot-starter\
```

### Step 3. 적용 확인

```bash
cd ~/projects/binance-bot-starter

# 4개 파일 모두 새 버전인지 확인
grep "v3.1.1" CLAUDE.md docs/SESSION_PROMPTS.md docs/MODULE_CHECKLIST.md
ls -lh docs/SPEC_v3.1_APPENDIX_E.md
```

다음과 같이 나오면 정상:
```
CLAUDE.md:# Binance Futures Bot v3.1.1 — Claude Code Guide
docs/SESSION_PROMPTS.md:# Claude Code 세션 프롬프트 — v3.1.1 봇 구현
docs/MODULE_CHECKLIST.md:# 모듈 완료 검증 체크리스트 (v3.1.1)
-rw-r--r-- ... 약 40K ... docs/SPEC_v3.1_APPENDIX_E.md
```

### Step 4. 만약 이미 코드 작성을 시작했다면

#### Case A. 아직 세션 0 환경 검증만 했다면 (코드 미작성)

→ 그대로 새 세션 0부터 시작하면 됩니다. 차이 없음.

#### Case B. 세션 1, 2 이미 작성했다면 (DB·Config 코드 있음)

```
[Claude Code에서 다음 명령 실행]

docs/SPEC_v3.1_APPENDIX_E.md §E-6, §E-7을 view해서 
기존 config/settings.py와 db/schema.sql에 v3.1.1 변경 사항을 추가해줘:

- config/settings.py: CapitalManagerConfig 추가, PairWhitelistConfig.protected_symbols 추가
- db/schema.sql: capital_initial, capital_daily_snapshot 테이블 추가
- db/migrations/v3_1_to_v3_1_1.sql 신규 작성
- tests/test_settings.py, tests/test_db_init.py에 v3.1.1 항목 추가

작업 전 plan 보여주고 내 승인 후 진행.
```

이후 새 세션 2.5부터 정상 진행.

#### Case C. 세션 8 (PairWhitelist) 이미 작성했다면

```
[Claude Code에서 다음 명령 실행]

docs/SPEC_v3.1_APPENDIX_E.md §E-3을 view해서 
기존 strategy/pair_whitelist.py에 protected_symbols 룰을 추가해줘:

- __init__에 protected_symbols 파라미터
- is_allowed() 최우선 차단 룰
- refresh() 보호 종목 검증 스킵
- manual_unblock() 보호 종목 거부
- tests/test_pair_whitelist.py에 시나리오 4개 추가

작업 전 plan 보여주고 내 승인 후 진행.
```

#### Case D. 이미 main_7590.py까지 완성했다면

추가 작업 가장 많습니다. 다음 순서로:

```
1. config/settings.py 갱신 (위 Case B)
2. db/schema.sql 갱신 + 마이그레이션 실행 (위 Case B)
3. data/capital_manager.py 신규 작성 (Claude Code 세션 2.5 프롬프트 사용)
4. strategy/pair_whitelist.py 갱신 (위 Case C)
5. trading/risk_manager.py 갱신 (Claude Code 세션 9 프롬프트 사용)
6. main_7590.py 갱신 (Claude Code 세션 12 프롬프트 사용)
```

각 단계 후 `pytest` 실행해서 회귀 없는지 확인.

---

## Binance API 키 권한 재확인 (중요)

v3.1.1의 보안 강화를 위해 다음 권한을 **반드시** 비활성화:

```
[Binance UI → API Management → API 키 편집]

✅ Enable Futures              ← 그대로 활성
❌ Enable Spot & Margin Trading ← 비활성화 (★ v3.1.1 추가)
❌ Enable Withdrawals          ← 그대로 비활성
❌ Permits Universal Transfer  ← 비활성화 (★ v3.1.1 추가)
✅ Enable IP Restriction       ← 그대로 활성
```

이렇게 하면 코드 버그가 있어도 거래소가 Spot 호출을 거부합니다.

---

## protected_symbols 커스터마이징

기본값:
```python
protected_symbols = ["BTCUSDT", "ETHUSDT", "HOLOUSDT"]
```

운영자가 직접 추가/제거하려면:

### 방법 1. `config/settings.py` 수정

```python
@dataclass
class PairWhitelistConfig:
    protected_symbols: list = field(default_factory=lambda: [
        "BTCUSDT",
        "ETHUSDT",
        "HOLOUSDT",
        # 운영자가 새로 수동 거래 시작한 페어 추가
        "SOLUSDT",     # 예시
    ])
```

### 방법 2. `.env` 환경변수 (재시작만으로 변경)

```bash
# .env 파일에 추가
PROTECTED_SYMBOLS=BTCUSDT,ETHUSDT,HOLOUSDT,SOLUSDT
```

단, 환경변수 로딩 코드를 `config/settings.py`에 추가해야 함 (부록 E-6-3 참조).

---

## 검증 시나리오 (적용 후 확인)

봇 가동 후 다음을 모두 확인:

### ✅ 시작 시 확인 사항

```
[로그에 다음 메시지 나와야 함]

[Capital] 초기 자본 기록: $1000.00
[Start] 초기 자본: wallet=$1000.00, available=$1000.00, locked_margin=$0.00
🛡️ 보호 종목 활성:
- BTCUSDT
- ETHUSDT
- HOLOUSDT
이 페어들은 봇이 절대 거래하지 않습니다.
[Security] Spot 권한 없음 (정상)
```

### ✅ 보호 종목 차단 확인

```
[로그]

[Filter] BTCUSDT 신호 발견 (OI 급등)
[PairWhitelist] BTCUSDT is_allowed=False (protected_symbol)
[Main] 진입 차단: BTCUSDT
```

### ✅ 자본 분리 확인

운영자가 Spot에서 BTC 추가 매수:
```
[Telegram 봇 알림에 변화 없음]
[capital_daily_snapshot 테이블에 변화 없음]
[trades 테이블 wallet_balance_at_entry 변화 없음]
```

### ✅ 마진 락 확인

봇이 SOLUSDT 진입 후:
```
[로그]

[Capital] snapshot: wallet=$1000, available=$970, locked_margin=$30
[Sizer] 새 신호 사이즈 계산: capital=$970 (available 기준)
```

---

## 문제 발생 시

### Q. Claude Code가 부록 E를 무시하는 것 같다

A. 프롬프트 첫 줄에 "docs/SPEC_v3.1.md §X-X **와** docs/SPEC_v3.1_APPENDIX_E.md §E-X를 함께 view해줘"를 명시.

### Q. is_allowed("BTCUSDT")가 True를 반환

A. PairWhitelist의 `is_allowed()` 첫 줄에서 `if symbol in self.protected_symbols: return False` 체크하는지 확인. 다른 룰보다 우선이어야 함.

### Q. CapitalManager 테스트가 자꾸 실패

A. mock 응답 형식 확인. Binance API는 문자열로 반환:
```python
{"totalWalletBalance": "1000.00", "availableBalance": "970.00", ...}
```
숫자가 아닌 **문자열**.

### Q. 마이그레이션 SQL이 두 번 실행돼서 에러

A. `ALTER TABLE`은 동일 컬럼이 이미 있으면 실패. `db/init_db.py`에서 schema_migrations 체크 후 적용하도록 작성:

```python
applied = conn.execute(
    "SELECT version FROM schema_migrations WHERE version='v3.1.1'"
).fetchone()
if not applied:
    conn.executescript(open('db/migrations/v3_1_to_v3_1_1.sql').read())
    conn.commit()
```

---

## 요약

v3.1.1은 **명세서 누락 보완 패치**입니다:

1. ✅ 운영자 수동 거래 자산을 봇으로부터 격리
2. ✅ Spot/Futures 자산 명확히 구분
3. ✅ 마진 락 반영한 정확한 사이즈 계산

기존 v3.1 코드 작성 진행 중이라면 위 Case A~D 따라 단계적으로 적용하세요. 아직 코드 작성 시작 전이라면 새 v3.1.1 세션 프롬프트로 처음부터 진행하면 됩니다.
