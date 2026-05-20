"""
data/capital_manager.py
=====================================================================
CapitalManager — Futures 지갑 자본 정확 조회

v3.1.1 신규 모듈. v3.1의 _fetch_account_balance() 단순 호출 대체.

주요 차이:
  - wallet_balance vs margin_balance vs available_balance 명확 구분
  - Spot 잔고 호출 절대 금지 (API 권한 분리 + 코드 차단)
  - 캐시로 API 호출 제한

근거: docs/SPEC_v3.1_APPENDIX_E.md §E-2
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CapitalSnapshot:
    """
    봇 자본 상태 스냅샷.

    자본 기준 4종 (부록 E-1-3 표 참조):
      - wallet_balance:    Futures 지갑 전체 (미실현 손익 제외) — MDD/일일손실 기준
      - margin_balance:    마진 잔고 (미실현 손익 포함)
      - available_balance: 사용 가능 (마진 락 제외) — 신규 진입 사이즈 계산에 사용
      - locked_margin:     사용 중 마진
    """

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
        """직렬화용 dict (로그·DB·텔레그램 보고용)."""
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

    책임 (부록 E-2-1):
      - Binance Futures 지갑의 정확한 자본 정보 조회
      - wallet/margin/available/locked 명확 구분
      - 캐시로 API 호출 빈도 제한 (기본 10초)
      - Spot 자산은 호출 자체를 하지 않음 (API 키 권한 분리 + 명시적 금지)

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
        if cache_ttl_seconds < 0:
            # 입력 검증 가드 (명세 외 방어): 음수 TTL은 캐시 로직을 무의미하게 만듦
            raise ValueError(
                f"cache_ttl_seconds must be >= 0, got {cache_ttl_seconds}"
            )

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

        캐시가 유효하면(TTL 내) 캐시를 반환하고, 아니면 Binance API를 호출한다.
        API 실패 시 캐시가 있으면 stale 캐시로 폴백, 없으면 예외를 전파한다.

        Args:
            force_refresh: True면 캐시 무시하고 새로 조회

        Returns:
            CapitalSnapshot

        Raises:
            Exception: API 호출 실패 + 폴백할 캐시도 없을 때
        """
        now = time.time()
        if (not force_refresh
                and self._cached_snapshot is not None
                and (now - self._cached_snapshot.timestamp) < self.cache_ttl):
            logger.debug("[Capital] 캐시 적중 (TTL 내)")
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

        python-binance 라이브러리는 동기 호출이므로 asyncio.to_thread로 래핑.
        """
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
        today = datetime.now(timezone.utc).date().isoformat()
        if self._daily_start_date != today:
            self._daily_start_wallet_balance = snapshot.wallet_balance
            self._daily_start_date = today
            logger.info(
                f"[Capital] 일일 시작 잔고 갱신: ${snapshot.wallet_balance:.2f}"
            )

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

        Spot 자산은 봇 자본에 절대 포함하지 않습니다 (부록 E-1 보호 자산 정책).
        운영자 수동 거래 자산이 필요하면 거래소 UI에서 직접 확인하십시오.

        Raises:
            ValueError: forbid_spot_access가 True일 때 (기본값)
        """
        if self.forbid_spot_access:
            raise ValueError(
                "Spot 잔고 조회 금지. 봇은 Futures 지갑만 사용합니다. "
                "운영자 수동 거래 자산은 봇이 접근하지 않습니다. "
                "(부록 E-1 보호 자산 정책 참조)"
            )
