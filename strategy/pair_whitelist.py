"""
strategy/pair_whitelist.py
=====================================================================
PairWhitelist — 거래 페어 화이트리스트 및 동적 검증

페어 분류:
  Tier 1: BTCUSDT, ETHUSDT (최고 유동성)
  Tier 2: SOLUSDT, BNBUSDT, XRPUSDT (상위 알트)
  Tier 3: 시총 톱 20 + 24h 거래량 > $1B
  blocked: 위 조건 미달 또는 위험 조건 해당 (tier=0)

자본 규모별 활성:
  < $1,000:     Tier 1만
  $1k~$3k:      Tier 1 + Tier 2
  $3k~$10k:     Tier 1 + Tier 2 + Tier 3
  > $10k:       전체, 단 Tier 3은 동시 1개만

v3.1.1 보호 종목 통합 (부록 E-3):
  protected_symbols (운영자 수동 거래 자산)는 봇이 절대 거래하지 않음.
  모든 룰보다 우선하는 최우선 차단. tier=0으로 등록되며 refresh()/
  manual_unblock()으로도 해제 불가.

근거: docs/SPEC_v3.1.md §8-7 + docs/SPEC_v3.1_APPENDIX_E.md §E-3
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# 정적 페어 분류 (수정 필요 시 운영자가 직접 변경)
STATIC_TIER_1 = {"BTCUSDT", "ETHUSDT"}
STATIC_TIER_2 = {"SOLUSDT", "BNBUSDT", "XRPUSDT"}
STATIC_TIER_3_CANDIDATES = {
    "LINKUSDT", "AVAXUSDT", "ADAUSDT", "DOGEUSDT", "MATICUSDT",
    "DOTUSDT", "ATOMUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT", "APTUSDT",
}

# 보호 종목 차단 사유 (부록 E-3) — 일관된 문자열 사용을 위해 상수화
_PROTECTED_REASON = "protected_symbol (운영자 수동 거래)"


@dataclass
class PairInfo:
    """단일 페어의 분류·검증 상태."""

    symbol: str
    tier: int                                  # 1, 2, 3, 0(blocked)
    listed_at: Optional[datetime] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    avg_funding_7d: Optional[float] = None
    blocked_reasons: List[str] = field(default_factory=list)
    last_checked: datetime = field(default_factory=datetime.now)


class PairWhitelist:
    """
    페어 화이트리스트 + 동적 검증.

    사용:
        pw = PairWhitelist(binance_client, protected_symbols=["BTCUSDT", "ETHUSDT"])
        pw.refresh()  # 일 1회
        active = pw.get_active(capital=1000)
        if pw.is_allowed("SOLUSDT", capital=1000):
            ...

    v3.1.1: protected_symbols에 포함된 페어는 어떤 경로로도 거래 불가.
    is_allowed()의 최우선 차단 룰이며 refresh()/manual_unblock()이
    이를 우회하지 못한다 (부록 E-3).
    """

    def __init__(
        self,
        binance_client,
        cmc_client=None,                              # CoinMarketCap (옵션)
        protected_symbols: Optional[List[str]] = None,  # v3.1.1 신규 (부록 E-3)
        min_listing_age_days: int = 30,
        min_market_cap_rank: int = 50,
        min_volume_24h_usd: float = 100_000_000,      # $100M
        max_avg_funding_7d_abs: float = 0.0005,       # 0.05%/8h 평균
        tier_3_max_concurrent: int = 1,
    ):
        self.binance = binance_client
        self.cmc = cmc_client
        self.protected_symbols = set(protected_symbols or [])    # v3.1.1
        self.min_listing_age_days = min_listing_age_days
        self.min_market_cap_rank = min_market_cap_rank
        self.min_volume_24h_usd = min_volume_24h_usd
        self.max_avg_funding_7d_abs = max_avg_funding_7d_abs
        self.tier_3_max_concurrent = tier_3_max_concurrent

        self._pairs: Dict[str, PairInfo] = {}
        self._last_refresh: Optional[datetime] = None

        if self.protected_symbols:
            logger.info(
                f"[PairWhitelist] 보호 종목 {len(self.protected_symbols)}개 영구 차단: "
                f"{sorted(self.protected_symbols)}"
            )

        # 초기 정적 등록 (Tier 1, 2)
        # ── v3.1.1: protected_symbols 우선 차단 (tier=0) ──
        for s in STATIC_TIER_1:
            if s in self.protected_symbols:
                self._pairs[s] = PairInfo(
                    symbol=s, tier=0,
                    blocked_reasons=[_PROTECTED_REASON],
                )
            else:
                self._pairs[s] = PairInfo(symbol=s, tier=1)

        for s in STATIC_TIER_2:
            if s in self.protected_symbols:
                self._pairs[s] = PairInfo(
                    symbol=s, tier=0,
                    blocked_reasons=[_PROTECTED_REASON],
                )
            else:
                self._pairs[s] = PairInfo(symbol=s, tier=2)

        # 보호 종목이 STATIC_TIER_1/2에 없는 경우(예: HOLOUSDT)도 명시 등록
        for s in self.protected_symbols:
            if s not in self._pairs:
                self._pairs[s] = PairInfo(
                    symbol=s, tier=0,
                    blocked_reasons=[_PROTECTED_REASON],
                )

    def refresh(self) -> None:
        """
        일 1회 호출 — 페어 정보 갱신 + Tier 3 후보 검증.

        v3.1.1: 보호 종목은 검증을 스킵하고 영구 차단(tier=0)을 유지한다.
        """
        logger.info("[PairWhitelist] 페어 정보 갱신 시작")

        # ── v3.1.1: 보호 종목은 검증 스킵, 영구 차단 유지 ──
        for s in self.protected_symbols:
            self._pairs[s] = PairInfo(
                symbol=s, tier=0,
                blocked_reasons=[_PROTECTED_REASON],
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

    def _validate_pair(self, symbol: str, target_tier: int) -> PairInfo:
        """단일 페어 검증 (상장일·거래량·시총·펀딩비). 위험 조건 시 tier=0."""
        info = PairInfo(symbol=symbol, tier=target_tier)
        reasons: List[str] = []

        # 1. Listing date 체크 (Binance API)
        try:
            listed_at = self._get_listing_date(symbol)
            info.listed_at = listed_at
            if listed_at and (datetime.now() - listed_at) < timedelta(days=self.min_listing_age_days):
                reasons.append(f"신규 상장 ({(datetime.now() - listed_at).days}일)")
        except Exception as e:
            logger.warning(f"[PairWhitelist] {symbol} listing date 실패: {e}")

        # 2. 24h 거래량 체크
        try:
            volume_24h = self._get_volume_24h(symbol)
            info.volume_24h_usd = volume_24h
            if volume_24h and volume_24h < self.min_volume_24h_usd:
                reasons.append(f"거래량 부족 (${volume_24h/1e6:.0f}M)")
        except Exception as e:
            logger.warning(f"[PairWhitelist] {symbol} 거래량 실패: {e}")

        # 3. 시총 순위 (CMC API, 없으면 스킵)
        if self.cmc and target_tier >= 3:
            try:
                rank = self.cmc.get_market_cap_rank(symbol.replace("USDT", ""))
                if rank and rank > self.min_market_cap_rank:
                    reasons.append(f"시총 {rank}위")
            except Exception as e:
                logger.warning(f"[PairWhitelist] {symbol} CMC 실패: {e}")

        # 4. 7일 평균 펀딩비
        try:
            avg_funding = self._get_avg_funding_7d(symbol)
            info.avg_funding_7d = avg_funding
            if avg_funding is not None and abs(avg_funding) > self.max_avg_funding_7d_abs:
                reasons.append(f"펀딩비 극단 ({avg_funding*100:.4f}%)")
        except Exception as e:
            logger.warning(f"[PairWhitelist] {symbol} 펀딩 실패: {e}")

        info.blocked_reasons = reasons
        if reasons:
            info.tier = 0  # blocked
        return info

    def _get_listing_date(self, symbol: str) -> Optional[datetime]:
        """Binance exchangeInfo에서 onboardDate."""
        try:
            info = self.binance.get_exchange_info(symbol)
            ts_ms = info.get("onboardDate") if isinstance(info, dict) else None
            return datetime.fromtimestamp(ts_ms / 1000) if ts_ms else None
        except Exception:
            return None

    def _get_volume_24h(self, symbol: str) -> Optional[float]:
        """24h quote volume in USDT."""
        try:
            ticker = self.binance.get_ticker_24h(symbol)
            return float(ticker.get("quoteVolume", 0))
        except Exception:
            return None

    def _get_avg_funding_7d(self, symbol: str) -> Optional[float]:
        """7일 평균 펀딩비."""
        try:
            funding_history = self.binance.get_funding_rate_history(symbol, limit=21)  # 3/day × 7
            if not funding_history:
                return None
            rates = [float(f.get("fundingRate", 0)) for f in funding_history]
            return sum(rates) / len(rates) if rates else None
        except Exception:
            return None

    # ── 외부 API ──
    def get_tier(self, symbol: str) -> int:
        """페어의 현재 tier 반환 (0=blocked, 1/2/3)."""
        info = self._pairs.get(symbol)
        return info.tier if info else 0

    def is_allowed(self, symbol: str, capital: float, regime: str = None) -> bool:
        """
        페어가 현재 자본·레짐에서 거래 가능한지.

        v3.1.1: protected_symbols는 모든 룰보다 우선하는 최우선 차단.
        """
        # ── v3.1.1: 보호 종목은 최우선 차단 (다른 모든 룰보다 우선) ──
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

    def get_active(self, capital: float, regime: str = None) -> List[str]:
        """현재 자본·레짐에서 거래 가능한 페어 리스트 (보호 종목 제외)."""
        return [
            s for s, info in self._pairs.items()
            if info.tier > 0 and self.is_allowed(s, capital, regime)
        ]

    def get_info(self, symbol: str) -> Optional[PairInfo]:
        """페어의 PairInfo 반환 (없으면 None)."""
        return self._pairs.get(symbol)

    def manual_block(self, symbol: str, reason: str) -> None:
        """운영자 수동 차단."""
        if symbol in self._pairs:
            self._pairs[symbol].tier = 0
            self._pairs[symbol].blocked_reasons.append(f"manual: {reason}")
            logger.warning(f"[PairWhitelist] 수동 차단: {symbol} ({reason})")

    def manual_unblock(self, symbol: str, target_tier: int = 2) -> None:
        """
        운영자 수동 차단 해제.

        v3.1.1: protected_symbols은 해제 거부 (경고 로그만 남기고 무시).
        보호 종목을 풀려면 config/settings.py의 protected_symbols에서
        제거 후 재시작해야 한다.
        """
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

    def needs_refresh(self, max_age_hours: int = 24) -> bool:
        """마지막 refresh 이후 max_age_hours 경과 여부."""
        if self._last_refresh is None:
            return True
        return (datetime.now() - self._last_refresh).total_seconds() > max_age_hours * 3600
