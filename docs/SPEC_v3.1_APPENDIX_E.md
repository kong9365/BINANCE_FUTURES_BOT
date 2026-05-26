# 바이낸스 선물 단타 봇 v3.1 — 부록 E: 보호 자산 정책 (v3.1.1 보완)

> **상태**: SPEC_v3.1.md의 누락 사항 보완. 본 문서는 SPEC_v3.1.md의 일부로 간주.
> **작성 사유**: v3.1 명세서에 다음이 누락됨 — (1) 운영자 수동 거래 자산 보호, (2) Spot/Futures 자산 분리, (3) Futures 마진 락과 사용 가능 잔고 구분.
> **변경 버전**: v3.1.1
> **적용 대상**: SPEC_v3.1.md를 참조하는 모든 모듈 (특히 PairWhitelist, RiskManager, DynamicPositionSizer, main_7590.py)

---

## 목차

- [E-1. 보호 자산 관리 정책 (개요)](#e-1-보호-자산-관리-정책-개요)
- [E-2. CapitalManager 모듈 상세 명세](#e-2-capitalmanager-모듈-상세-명세)
- [E-3. PairWhitelist 보호 종목 통합 (§8-7 변경)](#e-3-pairwhitelist-보호-종목-통합-8-7-변경)
- [E-4. RiskManager 자본 기준 분리 (§9 변경)](#e-4-riskmanager-자본-기준-분리-9-변경)
- [E-5. main_7590.py 통합 변경 (§8-8 변경)](#e-5-main_7590py-통합-변경-8-8-변경)
- [E-6. Config 변경 (§14 변경)](#e-6-config-변경-14-변경)
- [E-7. DB 스키마 변경 (§13 변경)](#e-7-db-스키마-변경-13-변경)
- [E-8. API 키 권한 정책 강화 (§14-3 변경)](#e-8-api-키-권한-정책-강화-14-3-변경)
- [E-9. 마이그레이션 절차](#e-9-마이그레이션-절차)

---

## E-1. 보호 자산 관리 정책 (개요)

### E-1-1. 정책 목적

운영자가 수동으로 거래·보유하는 자산(예: Spot 지갑의 BTC/ETH/HOLO)을 봇으로부터 완전히 격리하여 다음을 방지:

1. **포지션 충돌**: 운영자 Spot LONG + 봇 Futures SHORT = 의도치 않은 헤지
2. **자산 표시 혼선**: Spot 가격 변동이 봇 자본으로 잘못 인식
3. **마진 부족 청산**: Futures 마진 락을 무시한 사이즈 계산으로 강제 청산

### E-1-2. 자산 분리 원칙 (3단계)

```
┌──────────────────────────────────────────────────────┐
│   사용자 Binance 전체 계정                            │
│                                                       │
│   ┌────────────────────────────┐                     │
│   │ Spot Wallet                │ ← 봇이 절대 접근 못함 │
│   │  · BTC, ETH, HOLO 등       │   (API 키 권한 분리)  │
│   │  · 운영자 수동 거래 영역   │                     │
│   └────────────────────────────┘                     │
│                                                       │
│   ┌────────────────────────────┐                     │
│   │ USDT-M Futures Wallet      │ ← 봇 운용 영역       │
│   │  · 전체 잔고 ($1,000)      │                     │
│   │  · 마진 잔고 ($1,030)      │                     │
│   │  · 미실현 손익 ($+30)      │                     │
│   │  · 사용 중 마진 ($30)      │                     │
│   │  · 사용 가능 잔고 ($970)   │ ← 봇이 신규 진입할 때 │
│   │                            │   이 값을 사용       │
│   │  ┌──────────────────────┐  │                     │
│   │  │ 거래 가능 페어         │  │                     │
│   │  │ - SOLUSDT (Tier 2)   │  │ ← 봇 허용            │
│   │  │ - BNBUSDT (Tier 2)   │  │                     │
│   │  │ - XRPUSDT (Tier 2)   │  │                     │
│   │  │                      │  │                     │
│   │  │ 보호 페어              │  │ ← 봇 영구 차단       │
│   │  │ - BTCUSDT            │  │   (protected_symbols)│
│   │  │ - ETHUSDT            │  │                     │
│   │  │ - HOLOUSDT (있다면)  │  │                     │
│   │  │ - LYNUSDT           │  │                     │
│   │  └──────────────────────┘  │                     │
│   └────────────────────────────┘                     │
└──────────────────────────────────────────────────────┘
```

### E-1-3. 자본 기준 표 (어떤 값을 어디에 쓰는지)

| 용도 | 사용할 값 | 명세서 §위치 |
|---|---|---|
| `DynamicPositionSizer` 사이즈 계산 | `available_balance` | §8-3 변경 |
| `RiskManager` 일일 손실 한도 (1.5%) | `wallet_balance` (당일 시작 값) | §9 변경 |
| `RiskManager` 전체 MDD 한도 (15%) | `wallet_balance` (최초 값) | §9 변경 |
| `CostGuard` position_usdt | `available_balance` | §8-2 (기존 유지) |
| 자산 보고 (Telegram, 보고서) | `wallet_balance` + Spot 표시 (옵션) | §12 변경 |

### E-1-4. 4겹 안전망

보호 자산을 4단계로 보호:

1. **API 키 권한**: Binance API 키에 Spot 권한 비활성화 → 코드 버그 있어도 거래소가 거부
2. **PairWhitelist.protected_symbols**: 봇 코드 레벨에서 페어 차단
3. **CapitalManager**: Spot 자산을 봇 자본 계산에서 제외
4. **CLAUDE.md TIER 3 룰**: 향후 Claude Code 작업 시 보호 종목 코드 변경 금지

---

## E-2. CapitalManager 모듈 상세 명세

### E-2-1. 책임

- Binance Futures 지갑의 정확한 자본 정보 조회
- `wallet_balance` / `margin_balance` / `available_balance` / `locked_margin` 명확히 구분
- 캐시로 API 호출 빈도 제한 (10초)
- Spot 자산은 호출 자체를 하지 않음 (API 키 권한 분리 + 명시적 금지)

### E-2-2. 파일 위치

`data/capital_manager.py`

### E-2-3. 전체 코드

```python
"""
data/capital_manager.py
=====================================================================
CapitalManager — Futures 지갑 자본 정확 조회

v3.1.1 신규 모듈. v3.1의 _fetch_account_balance() 단순 호출 대체.

주요 차이:
  - wallet_balance vs margin_balance vs available_balance 명확 구분
  - Spot 잔고 호출 절대 금지 (API 권한 분리 + 코드 차단)
  - 캐시로 API 호출 제한
=====================================================================
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CapitalSnapshot:
    """봇 자본 상태 스냅샷."""
    wallet_balance: float           # Futures 지갑 전체 (미실현 손익 제외)
    margin_balance: float           # 마진 잔고 (미실현 손익 포함)
    available_balance: float        # 사용 가능 (마진 락 제외) — 신규 진입에 사용
    locked_margin: float            # 사용 중 마진
    unrealized_pnl: float           # 미실현 손익
    timestamp: float                # 조회 시각 (Unix time)

    @property
    def margin_utilization_pct(self) -> float:
        """마진 사용률 (%). wallet_balance 기준."""
        if self.wallet_balance <= 0:
            return 0.0
        return self.locked_margin / self.wallet_balance * 100

    def to_dict(self) -> dict:
        return {
            "wallet_balance": round(self.wallet_balance, 4),
            "margin_balance": round(self.margin_balance, 4),
            "available_balance": round(self.available_balance, 4),
            "locked_margin": round(self.locked_margin, 4),
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "margin_utilization_pct": round(self.margin_utilization_pct, 2),
            "timestamp": self.timestamp,
        }


class CapitalManager:
    """
    Futures 지갑 자본 조회 + 캐싱.

    사용:
        cm = CapitalManager(binance_client)
        snapshot = await cm.get_snapshot()
        position_size = snapshot.available_balance * 0.05    # 5% 사이즈
        
        # 초기 자본 (MDD 계산용)
        initial = cm.get_initial_capital()
    """

    def __init__(
        self,
        binance_client,
        cache_ttl_seconds: float = 10.0,        # 10초 캐시
        forbid_spot_access: bool = True,        # Spot 호출 금지 (방어)
    ):
        self.binance = binance_client
        self.cache_ttl = cache_ttl_seconds
        self.forbid_spot_access = forbid_spot_access

        self._cached_snapshot: Optional[CapitalSnapshot] = None
        self._initial_wallet_balance: Optional[float] = None    # 최초 가동 시 기록
        self._daily_start_wallet_balance: Optional[float] = None  # 매일 00:00 UTC 기록
        self._daily_start_date: Optional[str] = None

    async def get_snapshot(self, force_refresh: bool = False) -> CapitalSnapshot:
        """
        Futures 자본 스냅샷 조회.
        
        Args:
            force_refresh: True면 캐시 무시하고 새로 조회
        Returns:
            CapitalSnapshot
        """
        now = time.time()
        if (not force_refresh
                and self._cached_snapshot is not None
                and (now - self._cached_snapshot.timestamp) < self.cache_ttl):
            return self._cached_snapshot

        try:
            account = await self._call_futures_account()
            snapshot = self._parse_account(account, now)
            self._cached_snapshot = snapshot

            # 최초 가동 시 initial 기록
            if self._initial_wallet_balance is None:
                self._initial_wallet_balance = snapshot.wallet_balance
                logger.info(f"[Capital] 초기 자본 기록: ${snapshot.wallet_balance:.2f}")

            # 일일 시작 잔고 갱신
            self._update_daily_start(snapshot)

            return snapshot
        except Exception as e:
            logger.error(f"[Capital] 스냅샷 조회 실패: {e}")
            # 캐시 있으면 폴백
            if self._cached_snapshot is not None:
                logger.warning("[Capital] 캐시된 스냅샷 반환 (stale)")
                return self._cached_snapshot
            raise

    async def _call_futures_account(self) -> dict:
        """
        Binance Futures Account 조회.
        GET /fapi/v2/account
        """
        # python-binance 라이브러리는 동기, asyncio.to_thread로 래핑
        import asyncio
        return await asyncio.to_thread(self.binance.futures_account)

    def _parse_account(self, account: dict, ts: float) -> CapitalSnapshot:
        """Binance API 응답에서 자본 필드 파싱."""
        try:
            wallet = float(account.get("totalWalletBalance", 0))
            margin = float(account.get("totalMarginBalance", 0))
            available = float(account.get("availableBalance", 0))
            locked = float(account.get("totalPositionInitialMargin", 0))
            unrealized = float(account.get("totalUnrealizedProfit", 0))

            return CapitalSnapshot(
                wallet_balance=wallet,
                margin_balance=margin,
                available_balance=available,
                locked_margin=locked,
                unrealized_pnl=unrealized,
                timestamp=ts,
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"[Capital] 응답 파싱 실패: {e}, raw={account}")
            raise

    def _update_daily_start(self, snapshot: CapitalSnapshot) -> None:
        """일일 시작 잔고 갱신 (UTC 00:00에 한 번)."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        if self._daily_start_date != today:
            self._daily_start_wallet_balance = snapshot.wallet_balance
            self._daily_start_date = today
            logger.info(f"[Capital] 일일 시작 잔고 갱신: ${snapshot.wallet_balance:.2f}")

    # ── 외부 API ──
    def get_initial_capital(self) -> Optional[float]:
        """최초 가동 시점의 wallet_balance (MDD 계산 기준)."""
        return self._initial_wallet_balance

    def get_daily_start_capital(self) -> Optional[float]:
        """오늘 00:00 UTC 시점의 wallet_balance (일일 손실 기준)."""
        return self._daily_start_wallet_balance

    def set_initial_capital(self, value: float) -> None:
        """
        운영자가 명시적으로 초기 자본 설정 (재시작 시).
        DB에 persist된 값을 복원할 때 사용.
        """
        self._initial_wallet_balance = value
        logger.info(f"[Capital] 초기 자본 수동 설정: ${value:.2f}")

    def get_cached_snapshot(self) -> Optional[CapitalSnapshot]:
        """캐시된 스냅샷 (없으면 None). 동기 호출용."""
        return self._cached_snapshot

    # ── Spot 접근 금지 가드 (코드 레벨) ──
    def get_spot_balance(self, asset: str = "BTC") -> None:
        """
        Spot 잔고 조회 — 호출 시 ValueError 발생.
        Spot 자산은 봇 자본에 절대 포함하지 않습니다.
        """
        if self.forbid_spot_access:
            raise ValueError(
                "Spot 잔고 조회 금지. 봇은 Futures 지갑만 사용합니다. "
                "운영자 수동 거래 자산은 봇이 접근하지 않습니다. "
                "(부록 E-1 보호 자산 정책 참조)"
            )
```

### E-2-4. 단위 테스트 시나리오

```python
# tests/test_capital_manager.py 필수 시나리오 8개

# 1. 정상 응답 → 모든 필드 파싱 OK
#    {"totalWalletBalance": "1000", "availableBalance": "970", ...}
#    → snapshot.wallet_balance=1000, available_balance=970

# 2. 캐시 동작 (TTL 10초)
#    같은 시점 2번 호출 → API 한 번만 호출됨

# 3. force_refresh=True → 캐시 무시

# 4. 초기 자본 기록 (최초 호출 시)
#    get_snapshot() 후 get_initial_capital() == wallet_balance

# 5. 일일 시작 잔고 갱신 (날짜 바뀌면)
#    날짜 mock → _daily_start_wallet_balance 갱신

# 6. API 실패 + 캐시 있음 → 캐시 반환 (stale 경고)

# 7. API 실패 + 캐시 없음 → raise

# 8. get_spot_balance() → ValueError ("Spot 잔고 조회 금지")
```

### E-2-5. 의존성

- 의존성: `binance_client` (python-binance의 `futures_account()`)
- 외부 호출: `GET /fapi/v2/account` 만 사용
- DB 기록: 없음 (capital은 매번 실시간 조회, 단 wallet_balance를 trades 컬럼에 함께 저장 권장)

---

## E-3. PairWhitelist 보호 종목 통합 (§8-7 변경)

### E-3-1. 변경 요약

기존 `PairWhitelist` 모듈에 다음 추가:

1. `protected_symbols` 파라미터 (config에서 주입)
2. `is_allowed()` 최우선 차단 룰
3. `_pairs` 초기화 시 보호 종목 자동 등록 (tier=0, blocked)

### E-3-2. 변경된 코드 부분

기존 코드(§8-7-2)에서 다음을 변경/추가:

```python
class PairWhitelist:
    def __init__(
        self,
        binance_client,
        cmc_client=None,
        protected_symbols: Optional[List[str]] = None,    # ← v3.1.1 신규
        min_listing_age_days: int = 30,
        min_market_cap_rank: int = 50,
        min_volume_24h_usd: float = 100_000_000,
        max_avg_funding_7d_abs: float = 0.0005,
        tier_3_max_concurrent: int = 1,
    ):
        self.binance = binance_client
        self.cmc = cmc_client
        self.protected_symbols = set(protected_symbols or [])    # ← v3.1.1
        # ... 기존 코드 ...

        # 초기 정적 등록 (Tier 1, 2)
        # ── v3.1.1: protected_symbols 우선 차단 ──
        for s in STATIC_TIER_1:
            if s in self.protected_symbols:
                self._pairs[s] = PairInfo(
                    symbol=s, tier=0,
                    blocked_reasons=["protected_symbol (운영자 수동 거래)"],
                )
            else:
                self._pairs[s] = PairInfo(symbol=s, tier=1)

        for s in STATIC_TIER_2:
            if s in self.protected_symbols:
                self._pairs[s] = PairInfo(
                    symbol=s, tier=0,
                    blocked_reasons=["protected_symbol (운영자 수동 거래)"],
                )
            else:
                self._pairs[s] = PairInfo(symbol=s, tier=2)

    def is_allowed(self, symbol: str, capital: float, regime: str = None) -> bool:
        """페어가 현재 자본·레짐에서 거래 가능한지."""
        # ── v3.1.1: 보호 종목은 최우선 차단 ──
        if symbol in self.protected_symbols:
            return False

        tier = self.get_tier(symbol)
        if tier == 0:
            return False

        # 자본 규모 cap
        if capital < 1000 and tier > 1:
            return False
        if capital < 3000 and tier > 2:
            return False

        # 레짐 cap
        if regime == "RANGING" and tier > 1:
            return False
        if regime == "UNCERTAIN" and tier > 2:
            return False

        return True

    def refresh(self) -> None:
        """일 1회 호출 — 페어 정보 갱신 + Tier 3 후보 검증."""
        logger.info("[PairWhitelist] 페어 정보 갱신 시작")

        # ── v3.1.1: 보호 종목은 검증 스킵, 영구 차단 유지 ──
        for s in self.protected_symbols:
            self._pairs[s] = PairInfo(
                symbol=s, tier=0,
                blocked_reasons=["protected_symbol (운영자 수동 거래)"],
            )

        # Tier 3 후보 검증 (보호 종목 제외)
        for symbol in STATIC_TIER_3_CANDIDATES:
            if symbol in self.protected_symbols:
                continue
            info = self._validate_pair(symbol, target_tier=3)
            self._pairs[symbol] = info

        # Tier 1, 2 재검증 (보호 종목 제외)
        for symbol in list(STATIC_TIER_1):
            if symbol in self.protected_symbols:
                continue
            info = self._validate_pair(symbol, target_tier=1)
            self._pairs[symbol] = info

        for symbol in list(STATIC_TIER_2):
            if symbol in self.protected_symbols:
                continue
            info = self._validate_pair(symbol, target_tier=2)
            self._pairs[symbol] = info

        self._last_refresh = datetime.now()
        active_count = sum(1 for p in self._pairs.values() if p.tier > 0)
        protected_count = len(self.protected_symbols)
        blocked_count = sum(1 for p in self._pairs.values() if p.tier == 0) - protected_count
        logger.info(
            f"[PairWhitelist] 갱신 완료: 활성 {active_count}, "
            f"차단 {blocked_count}, 보호 {protected_count}"
        )

    def manual_unblock(self, symbol: str, target_tier: int = 2) -> None:
        """운영자 수동 차단 해제. 단 protected_symbols은 해제 거부."""
        # ── v3.1.1: 보호 종목 unblock 거부 ──
        if symbol in self.protected_symbols:
            logger.warning(
                f"[PairWhitelist] protected_symbol {symbol}은 unblock 거부. "
                f"config/settings.py의 protected_symbols에서 제거 후 재시작 필요."
            )
            return

        if symbol in self._pairs:
            self._pairs[symbol].tier = target_tier
            self._pairs[symbol].blocked_reasons = []
            logger.info(f"[PairWhitelist] 수동 해제: {symbol} → Tier {target_tier}")
```

### E-3-3. 추가 단위 테스트 (§8-7-3 추가)

```python
# 기존 7개 시나리오에 추가:

# 8. protected_symbols=["BTCUSDT"] → is_allowed("BTCUSDT", capital=100000) == False
# 9. protected_symbols=["BTCUSDT"] → refresh() 후에도 차단 유지
# 10. manual_unblock("BTCUSDT") → 차단 유지 (경고만 로그)
# 11. get_active(capital=10000) → BTCUSDT 제외된 리스트
```

---

## E-4. RiskManager 자본 기준 분리 (§9 변경)

### E-4-1. 변경 요약

기존 `RiskManager`는 `current_capital` 하나만 사용했으나, v3.1.1부터 **3가지 자본 기준을 명확히 구분**:

| 룰 | 사용할 자본 | 이유 |
|---|---|---|
| `max_daily_loss_pct` 검사 | `daily_start_wallet_balance` | "오늘 시작 대비 -1.5%" |
| `max_total_drawdown_pct` 검사 | `initial_wallet_balance` | "최초 자본 대비 -15%" |
| 동시 포지션 한도 (자본별) | `wallet_balance` (현재) | 현재 자본 규모 |
| 사이즈 계산 (`DynamicSizer`) | `available_balance` | 즉시 사용 가능 |

### E-4-2. 변경된 코드 부분

`trading/risk_manager.py`에 다음 추가:

```python
class RiskManager:
    def __init__(
        self,
        db_path: str,
        capital_manager,                       # ← v3.1.1: CapitalManager 주입
    ):
        self.db_path = db_path
        self.capital_manager = capital_manager
        # ... 기존 코드 ...

    async def check_all(self) -> bool:
        """
        모든 리스크 체크. v3.1.1: 자본은 capital_manager에서 자동 조회.
        """
        snapshot = await self.capital_manager.get_snapshot()
        initial = self.capital_manager.get_initial_capital()
        daily_start = self.capital_manager.get_daily_start_capital()

        if snapshot is None or initial is None or daily_start is None:
            logger.warning("[Risk] 자본 정보 미준비 → 차단")
            return False

        checks = [
            self._check_daily_loss(snapshot.wallet_balance, daily_start),
            self._check_total_drawdown(snapshot.wallet_balance, initial),
            self._check_consecutive_losses(),
            self._check_monthly_drawdown(snapshot.wallet_balance),
            self._check_concurrent_positions(snapshot.wallet_balance),
            self._check_min_balance(snapshot.available_balance),
            self._check_cooldown(),
        ]
        return all(c.passed for c in checks)

    def _check_daily_loss(self, current: float, daily_start: float) -> CheckResult:
        """일일 손실 한도 (1.5%)."""
        if daily_start <= 0:
            return CheckResult(passed=True, reason="daily_start 미준비")
        pct = (current - daily_start) / daily_start
        if pct <= -RISK_RULES.max_daily_loss_pct:
            return CheckResult(
                passed=False,
                reason=f"일일 손실 {pct*100:.2f}% (한도 -{RISK_RULES.max_daily_loss_pct*100:.1f}%)"
            )
        return CheckResult(passed=True, reason=f"일일 손익 {pct*100:.2f}%")

    def _check_total_drawdown(self, current: float, initial: float) -> CheckResult:
        """전체 MDD 한도 (15%, 최초 자본 기준)."""
        if initial <= 0:
            return CheckResult(passed=True, reason="initial 미준비")
        pct = (current - initial) / initial
        if pct <= -RISK_RULES.max_total_drawdown_pct:
            return CheckResult(
                passed=False,
                reason=f"전체 MDD {pct*100:.2f}% (한도 -{RISK_RULES.max_total_drawdown_pct*100:.1f}%)"
            )
        return CheckResult(passed=True, reason=f"전체 손익 {pct*100:.2f}%")

    def _check_min_balance(self, available: float) -> CheckResult:
        """최소 잔고 ($100, available 기준)."""
        if available < RISK_RULES.min_balance_for_trade_usdt:
            return CheckResult(
                passed=False,
                reason=f"available_balance ${available:.2f} < ${RISK_RULES.min_balance_for_trade_usdt}"
            )
        return CheckResult(passed=True, reason="잔고 충분")
```

### E-4-3. 추가 단위 테스트

```python
# 기존 10개 시나리오에 추가:

# 11. wallet_balance=$950, daily_start=$1000 → -5% → 차단 (한도 -1.5%)
# 12. wallet_balance=$840, initial=$1000 → -16% → 차단 (한도 -15%)
# 13. available_balance=$80 → _check_min_balance 차단
# 14. capital_manager.get_initial_capital() == None → check_all 차단 (안전 우선)
```

---

## E-5. main_7590.py 통합 변경 (§8-8 변경)

### E-5-1. 변경 요약

`MainBot.__init__`에 `CapitalManager` 추가하고, 모든 자본 조회를 이로 교체.

### E-5-2. 변경된 코드 부분

```python
# main_7590.py 상단 import 추가
from data.capital_manager import CapitalManager

# config import에 추가
from config.settings import (
    SYSTEM_CONFIG, REGIME_CONFIG, COST_GUARD_CONFIG,
    SIZING_CONFIG, WEEKLY_ANALYST_CONFIG, MACRO_EVENT_CONFIG,
    HEALTH_MONITOR_CONFIG, PAIR_WHITELIST_CONFIG,
    CAPITAL_MANAGER_CONFIG,           # ← v3.1.1 신규
    REGIME_TRADING_PARAMS, RISK_RULES,
)


class MainBot:
    def __init__(self):
        # ... 기존 컴포넌트 (collector, oi_scanner, etc.) ...

        # ── v3.1.1: CapitalManager 신규 ──
        self.capital_manager = CapitalManager(
            binance_client=self.binance,
            cache_ttl_seconds=CAPITAL_MANAGER_CONFIG.cache_ttl_seconds,
            forbid_spot_access=CAPITAL_MANAGER_CONFIG.forbid_spot_access,
        )

        # ── v3.1.1: PairWhitelist에 protected_symbols 주입 ──
        self.pair_wl = PairWhitelist(
            binance_client=self.binance,
            cmc_client=None,
            protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols,    # ← 신규
            min_volume_24h_usd=PAIR_WHITELIST_CONFIG.min_volume_24h_usd,
        )

        # ── v3.1.1: RiskManager에 capital_manager 주입 ──
        self.risk_manager = RiskManager(
            db_path=SYSTEM_CONFIG.db_path,
            capital_manager=self.capital_manager,
        )

        # ── 상태 (기존 _current_capital 제거, capital_manager로 일원화) ──
        self._last_weekly_run: datetime = None
        self._last_pair_refresh: datetime = None
        self._current_regime_state = None


    async def _iter(self):
        # ─── Layer 0: 시스템 건강 체크 (기존 유지) ───
        health = self.health.check()
        if not health.healthy:
            # ... 기존 코드 ...
            return

        # ─── v3.1.1: 자본 스냅샷 (기존 _fetch_account_balance 대체) ───
        try:
            capital_snapshot = await self.capital_manager.get_snapshot()
        except Exception as e:
            logger.error(f"[Main] 자본 조회 실패: {e}")
            return

        # ─── 신규 진입 가능 여부 — capital_manager 기준 ───
        # (기존 self._current_capital 사용 부분을 모두 capital_snapshot으로 교체)

        # 이후 신호 처리에서:
        for candidate in candidates:
            await self._handle_signal(candidate, regime_state, capital_snapshot)


    async def _handle_signal(self, candidate, regime_state, capital_snapshot):
        """v3.1.1: capital_snapshot 인자 추가."""
        symbol = candidate.symbol

        # 1) 페어 화이트리스트 (protected_symbols 자동 차단)
        if not self.pair_wl.is_allowed(
            symbol,
            capital=capital_snapshot.wallet_balance,    # ← wallet 기준 자본 cap
            regime=regime_state.regime,
        ):
            return

        # 2) OI 필터, 3) QualityGate (기존 유지)
        # ...

        # 4) 리스크 (capital_manager 자동 조회)
        if not await self.risk_manager.check_all():
            return

        # 5) 사이징 — available_balance 기준!
        win_rate, sample_count = self.expectancy.get_win_rate(...)
        avg_win, avg_loss = self.expectancy.get_avg_R(...)

        sizing = self.sizer.calculate(
            capital=capital_snapshot.available_balance,    # ← v3.1.1: available 기준
            win_rate=win_rate, avg_win_R=avg_win, avg_loss_R=avg_loss,
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            sample_count=sample_count,
        )

        # 마진 부족 추가 검증
        if sizing.size_usdt > capital_snapshot.available_balance:
            logger.warning(
                f"[Sizer] {symbol} 사이즈 ${sizing.size_usdt:.2f} > "
                f"available ${capital_snapshot.available_balance:.2f} → 차단"
            )
            return

        # 6) CostGuard (기존 유지)
        pair_tier = self.pair_wl.get_tier(symbol)
        cost_check = self.cost_guard.check(
            setup_tag=quality.setup_tag,
            entry_price=quality.entry_price,
            tp_price=quality.tp,
            sl_price=quality.sl,
            position_usdt=sizing.size_usdt,
            action=quality.action,
            pair_tier=pair_tier,
        )
        if not cost_check.passed:
            return

        # 7) 실행 (기존 유지, 단 decision에 capital 메타 추가)
        decision = {
            # ... 기존 필드 ...
            "wallet_balance_at_entry": capital_snapshot.wallet_balance,    # ← 신규
            "available_at_entry": capital_snapshot.available_balance,      # ← 신규
        }
        await self.executor.enter_trade(decision)
```

### E-5-3. 시작 시퀀스 변경

```python
async def start(self):
    """봇 시작 시퀀스."""
    # ── v3.1.1: 초기 자본 조회 (initial_balance 기록) ──
    initial_snapshot = await self.capital_manager.get_snapshot(force_refresh=True)
    logger.info(
        f"[Start] 초기 자본: "
        f"wallet=${initial_snapshot.wallet_balance:.2f}, "
        f"available=${initial_snapshot.available_balance:.2f}, "
        f"locked_margin=${initial_snapshot.locked_margin:.2f}"
    )

    # 보호 종목 확인 + Telegram 알림
    if self.pair_wl.protected_symbols:
        await self.telegram.send(
            f"🛡️ 보호 종목 활성:\n"
            + "\n".join(f"- {s}" for s in self.pair_wl.protected_symbols)
            + "\n이 페어들은 봇이 절대 거래하지 않습니다."
        )

    # 이후 메인 루프 시작
    await self._main_loop()
```

---

## E-6. Config 변경 (§14 변경)

### E-6-1. 추가될 dataclass

`config/settings.py`에 다음 추가:

```python
# ─────────────────────────────────────────────────────
# CapitalManager (v3.1.1 신규)
# ─────────────────────────────────────────────────────
@dataclass
class CapitalManagerConfig:
    cache_ttl_seconds: float = 10.0
    forbid_spot_access: bool = True            # Spot 호출 시 ValueError
    persist_initial_capital: bool = True       # DB에 초기 자본 저장 (재시작 시 복원)

CAPITAL_MANAGER_CONFIG = CapitalManagerConfig()
```

### E-6-2. 기존 PairWhitelistConfig 수정

```python
@dataclass
class PairWhitelistConfig:
    min_listing_age_days: int = 30
    min_market_cap_rank: int = 50
    min_volume_24h_usd: float = 100_000_000
    max_avg_funding_7d_abs: float = 0.0005
    tier_3_max_concurrent: int = 1
    refresh_interval_hours: int = 24

    # v3.1.1 신규: 보호 종목 (운영자 수동 거래 자산)
    # ※ 실제 구현은 환경변수 PROTECTED_SYMBOLS 우선 + 아래 기본값 (E-6-3 방식 채택).
    protected_symbols: list = field(default_factory=lambda: [
        "BTCUSDT",
        "ETHUSDT",
        "HOLOUSDT",     # Binance USDT-M Futures에 상장되지 않았으면 무의미하지만
                        # 향후 상장 대비 + 명시적 의도 표시
        "LYNUSDT",      # 운영 중 교체 (운영자 수동 거래 자산)
    ])

PAIR_WHITELIST_CONFIG = PairWhitelistConfig()
```

### E-6-3. .env 추가 (선택)

```bash
# .env.example 추가 (선택, hardcoded config 대안)

# 보호 종목 (콤마 구분, 비워두면 config/settings.py의 기본값 사용)
PROTECTED_SYMBOLS=BTCUSDT,ETHUSDT,HOLOUSDT,LYNUSDT
```

config/settings.py에서 환경변수 로드 (구현 채택 방식):

```python
import os

@dataclass
class PairWhitelistConfig:
    # ... 기본값 ...
    protected_symbols: list = field(default_factory=lambda: (
        os.environ.get("PROTECTED_SYMBOLS", "BTCUSDT,ETHUSDT,HOLOUSDT,LYNUSDT")
        .replace(" ", "")
        .split(",")
    ))
```

---

## E-7. DB 스키마 변경 (§13 변경)

### E-7-1. trades 테이블에 컬럼 추가

```sql
-- v3.1.1: 진입 시점의 자본 상태 기록
ALTER TABLE trades ADD COLUMN wallet_balance_at_entry REAL;
ALTER TABLE trades ADD COLUMN available_at_entry REAL;
ALTER TABLE trades ADD COLUMN locked_margin_at_entry REAL;
```

### E-7-2. 신규 테이블 — 초기 자본 영속화

```sql
-- v3.1.1: 초기 자본 영속화 (재시작 시 복원)
CREATE TABLE IF NOT EXISTS capital_initial (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL,
    initial_wallet_balance REAL NOT NULL,
    note            TEXT,                            -- 운영자 메모 (예: "20260514 가동 시작")
    is_active       INTEGER DEFAULT 1                -- 현재 활성 1, 과거 기록 0
);

CREATE INDEX IF NOT EXISTS idx_capital_initial_active ON capital_initial (is_active);
```

### E-7-3. 일일 자본 스냅샷 테이블

```sql
-- v3.1.1: 일일 시작 잔고 기록 (운영 추적)
CREATE TABLE IF NOT EXISTS capital_daily_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT    UNIQUE NOT NULL,         -- YYYY-MM-DD
    wallet_balance  REAL    NOT NULL,
    available_balance REAL,
    locked_margin   REAL,
    unrealized_pnl  REAL,
    recorded_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capital_daily_date ON capital_daily_snapshot (snapshot_date);
```

### E-7-4. 마이그레이션 SQL

```sql
-- migrations/v3_1_to_v3_1_1.sql

ALTER TABLE trades ADD COLUMN wallet_balance_at_entry REAL;
ALTER TABLE trades ADD COLUMN available_at_entry REAL;
ALTER TABLE trades ADD COLUMN locked_margin_at_entry REAL;

CREATE TABLE IF NOT EXISTS capital_initial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    initial_wallet_balance REAL NOT NULL,
    note TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_capital_initial_active ON capital_initial (is_active);

CREATE TABLE IF NOT EXISTS capital_daily_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT UNIQUE NOT NULL,
    wallet_balance REAL NOT NULL,
    available_balance REAL,
    locked_margin REAL,
    unrealized_pnl REAL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capital_daily_date ON capital_daily_snapshot (snapshot_date);

INSERT INTO schema_migrations VALUES ('v3.1.1', datetime('now'));
```

---

## E-8. API 키 권한 정책 강화 (§14-3 변경)

### E-8-1. 변경된 권한 매트릭스

```
[Binance API 키 — 봇 전용 (v3.1.1 강화)]

✅ Enable Futures              ← 필수
✅ Enable IP Restriction       ← 운영 서버 IP만
❌ Enable Spot & Margin Trading ← 비활성화 ★ 보호 자산 분리
❌ Enable Withdrawals          ← 비활성화 ★ 해킹 대비
❌ Permits Universal Transfer  ← 비활성화 ★ Spot↔Futures 이체 방지
❌ Enable Margin Loan, Repay   ← 비활성화
```

### E-8-2. 권한 검증 절차

봇 시작 시 자체 검증 (선택):

```python
# main_7590.py 시작 시퀀스에 추가

async def _verify_api_key_permissions(self):
    """API 키 권한 검증. Spot 권한 있으면 경고."""
    try:
        # Spot account 조회 시도 → 403이면 권한 없음 (정상)
        await asyncio.to_thread(self.binance.get_account)
        # 여기 오면 Spot 권한 있음 → 경고
        await self.telegram.send(
            "⚠️ API 키에 Spot 권한이 활성화되어 있습니다. "
            "보안 강화를 위해 비활성화 권장 (부록 E-8)."
        )
        logger.warning("[Security] Spot 권한 활성화 감지")
    except Exception as e:
        if "403" in str(e) or "Invalid" in str(e):
            logger.info("[Security] Spot 권한 없음 (정상)")
        else:
            logger.warning(f"[Security] 권한 확인 불가: {e}")
```

---

## E-9. 마이그레이션 절차

### E-9-1. 기존 v3.1 코드에서 v3.1.1로 마이그레이션

```
[Step 1] Binance API 키 권한 재설정
□ Binance UI → API Management
□ Spot & Margin Trading 비활성화
□ Permits Universal Transfer 비활성화
□ 변경 후 봇 재시작

[Step 2] config/settings.py 갱신
□ CapitalManagerConfig 추가
□ PairWhitelistConfig에 protected_symbols 추가
□ CAPITAL_MANAGER_CONFIG 인스턴스 export

[Step 3] DB 마이그레이션
□ python -c "import sqlite3; conn = sqlite3.connect('data/bot.db'); \
   conn.executescript(open('db/migrations/v3_1_to_v3_1_1.sql').read()); conn.commit()"
□ schema_migrations에 'v3.1.1' 행 확인

[Step 4] 신규 모듈 추가
□ data/capital_manager.py 작성 (E-2)
□ tests/test_capital_manager.py 작성 (E-2-4)
□ pytest tests/test_capital_manager.py 통과

[Step 5] 기존 모듈 수정
□ strategy/pair_whitelist.py에 protected_symbols 통합 (E-3)
□ trading/risk_manager.py에 capital_manager 의존성 추가 (E-4)
□ main_7590.py 통합 변경 (E-5)
□ tests/ 해당 테스트 추가/수정

[Step 6] 통합 검증
□ python main_7590.py --dry-run 으로 Testnet 1시간 실행
□ Telegram에서 "🛡️ 보호 종목 활성" 알림 수신 확인
□ BTCUSDT, ETHUSDT 시그널이 차단되는지 로그 확인
□ wallet_balance vs available_balance 값이 다른지 확인 (포지션 1개 진입 후)

[Step 7] 운영자 확인
□ Spot 지갑 BTC, ETH, HOLO 잔고 그대로 유지되는지
□ Futures 지갑 USDT 잔고가 봇 거래와 일치하는지
□ Spot 거래를 운영자가 수행해도 봇이 영향받지 않는지 (예: BTC Spot 매수)
```

### E-9-2. 자체 점검 체크리스트

봇 가동 후 1일 내 다음을 모두 확인:

```
□ 모든 거래에 wallet_balance_at_entry, available_at_entry 기록됨
□ 일일 자본 스냅샷 capital_daily_snapshot 테이블에 기록됨
□ 봇이 BTCUSDT 시그널을 받아도 진입하지 않음
   (logs에 "is_allowed=False, protected_symbol" 메시지)
□ available_balance 부족 시 사이즈 자동 축소 또는 차단
□ Telegram 알림에 보호 종목 목록 명시
□ Spot 잔고 변화가 봇 자본 표시에 반영되지 않음
```

---

## E-10. 2-tier Risk Hierarchy (v3.2.0 신규, 청사진 §7.4 정합)

**근거**:
- 청사진 v1.0 §7.4 — 3-tier hierarchy (warn / soft / hard) + emergency 백스톱 명시
- 운영자 결정 2026-05-26 — **2-tier 단순화** (soft 제거, 초기 운영 단순성 우선)
- SPEC v3.1.1 §9 max_daily_loss_pct = 0.015 (1.5%) **유지** (TIER 3 임계 변경 금지 정합)

### E-10-1. 일일 손실 2-tier

| Tier | 임계 | 동작 | 도달 가능 |
|---|---|---|---|
| **warn** | -1.0% | Slack/Telegram 경고만, 거래 계속 | ✓ |
| **hard** | -1.5% (SPEC v3.1.1) | KillSwitch 자동 활성화 (`source="daily_loss_hard"`) | ✓ |

> 청사진 §7.4 의 `daily_soft_pct: -2.0` 는 *제거* — hard -1.5% 에서 이미 차단되어 도달 불가하기 때문.
> 청사진 -3.0% / -5.0% (emergency) 는 *최종 백스톱 Stage 3* 로만 명시 (실제로는 -1.5% 에서 차단).

### E-10-2. MDD 2-tier (선택 도입)

| Tier | 임계 | 동작 |
|---|---|---|
| warn | -10.0% | Slack 경고 |
| hard | -15.0% (SPEC v3.1.1) | KillSwitch 자동 활성화 |

- 청사진 -25% / -30% 는 *최종 백스톱 Stage 3* (90d rolling 또는 -25% MDD) 로만 작동
- SPEC v3.1.1 의 `max_total_drawdown_pct = 0.15` 그대로 유지

### E-10-3. 구현 위치

- `governance/risk_hierarchy.py` (M4 신규) — `RiskHierarchyConfig` dataclass + `check_warn / check_hard` 헬퍼
- 기존 `trading/risk_manager.py` `_check_daily_loss` / `_check_total_drawdown` 은 *그대로 작동* (hard 임계)
- warn 임계는 `ops/system_health_monitor.py` 또는 신규 governance 모듈에서 5분 주기 체크

### E-10-4. 명세

```python
@dataclass
class RiskHierarchyConfig:
    """2-tier risk hierarchy (운영자 결정 2026-05-26)."""

    # Daily loss
    daily_warn_pct: float = 0.010   # -1.0% → Slack 경고
    daily_hard_pct: float = 0.015   # -1.5% → KillSwitch (SPEC v3.1.1 유지)

    # MDD
    mdd_warn_pct: float = 0.10      # -10% → Slack 경고
    mdd_hard_pct: float = 0.15      # -15% → KillSwitch (SPEC v3.1.1 유지)

    # Stage 3 백스톱 (청사진 §7.5)
    stage3_rolling_90d_pct: float = 0.10   # 90d 누적 -10% → KillSwitch
    stage3_mdd_pct: float = 0.25           # 90d MDD -25% → KillSwitch
```

### E-10-5. 검증

- `pytest tests/test_risk_hierarchy_2tier.py`:
  - daily PnL -0.9% → no action
  - daily PnL -1.0% → Slack 경고만 (거래 계속)
  - daily PnL -1.4% → Slack 경고만
  - daily PnL -1.5% → KillSwitch 자동 활성화 + `audit_log` row `KILL_SWITCH_ACTIVATED`
  - daily PnL -2.0% → *이미 차단된 상태* (도달 X, 정상 시나리오)

---

## 부록 E 끝

본 부록은 SPEC_v3.1.md의 일부로 통합되어야 하며, 운영 시 다음을 함께 참조:

- 부록 A (변경 매트릭스) — 향후 v3.1.1 + v3.2.0 항목 추가 필요
- 부록 C (운영 체크리스트) — Step 7 운영자 확인 절차 + M0~M6 단계별 검증 추가
- 부록 D (모듈 의존성 그래프) — CapitalManager + KillSwitch + 5-Agent 의존성 추가

**다음 단계 권장**: 코드 작성 시 새로운 세션 `세션 2.5: CapitalManager + PairWhitelist 보호 종목` 진행 (SESSION_PROMPTS.md 참조).
