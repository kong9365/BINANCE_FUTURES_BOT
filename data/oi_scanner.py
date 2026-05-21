"""
data/oi_scanner.py
=====================================================================
OIScanner — Open Interest 변화율 기반 급등 후보 스캔

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 3: candidates = oi_scanner.scan(active_pairs))
  - docs/SPEC_v3.1.md D-2 (데이터 흐름: OIScanner.scan() → [candidates])

책임:
  - active_pairs 각 페어의 현재 OI / 시세 / 가격 변화율 조회
  - 직전 스캔 대비 OI 변화율 계산 (내부 캐시)
  - OI 급증 + 가격 급등 동시 충족 페어를 후보(Candidate)로 추림
  - oi_change_pct 내림차순 상위 N개 반환

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지"로만 표기하므로, §8-8 / D-2 의 호출 계약
    기준으로 신규 작성한다.
  - scan() 은 collector 의 async REST 메서드를 호출하므로 async 로 둔다.
  - OI 변화율은 **OI 이력(lookback window)** 으로 계산한다. 이전 구현은 직전
    스캔(메인 루프 30초) 대비로 OI 변화를 측정했는데, "30초 안에 5% OI 급증"은
    사실상 발생하지 않아 진입이 거의 0건이었다(정량 진단). 이를 의미 있는 시간
    창(기본 15분)으로 교정한다 — collector.get_open_interest_history 사용.
  - 개별 페어 조회 실패는 해당 페어만 스킵하고 전체 스캔은 계속한다.
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """OIScanner 가 추린 급등 후보 1건.

    Attributes:
        symbol: 거래 페어 (예: "SOLUSDT").
        price: 현재 시세.
        oi_now: 현재 미결제약정(OI) 총량.
        oi_change_pct: 직전 스캔 대비 OI 변화율 (%).
        price_change_pct: 최근 1H 캔들 종가 변화율 (%).
        volume_24h: 24시간 거래대금 (USDT). 조회 실패 시 0.0.
        detected_at: 후보 감지 시각 (UTC tz-aware).
    """

    symbol: str
    price: float
    oi_now: float
    oi_change_pct: float
    price_change_pct: float
    volume_24h: float = 0.0
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class OIScanner:
    """Open Interest 변화율 기반 급등 후보 스캐너.

    사용:
        scanner = OIScanner(collector)
        candidates = await scanner.scan(["SOLUSDT", "BNBUSDT"])
        for c in candidates:
            handle(c)

    상태:
        직전 스캔의 페어별 OI 를 _prev_oi 에 캐싱한다. 두 번째 스캔부터
        변화율이 산출되어 후보가 나올 수 있다.
    """

    def __init__(
        self,
        collector,
        oi_change_threshold_pct: float = 5.0,
        price_change_threshold_pct: float = 2.0,
        top_n: int = 10,
        oi_lookback_period: str = "15m",
        oi_lookback_count: int = 1,
    ) -> None:
        """스캐너 초기화.

        Args:
            collector: BinanceDataCollector 인스턴스. get_open_interest_history /
                get_ticker_price / get_candles / get_24h_quote_volume (async)
                를 제공해야 한다.
            oi_change_threshold_pct: 후보 편입 OI 변화율 하한 (%). 기본 5%.
            price_change_threshold_pct: 후보 편입 가격 변화율 하한 (절댓값, %).
                기본 2%.
            top_n: 반환할 최대 후보 수 (oi_change_pct 내림차순). 기본 10.
            oi_lookback_period: OI 이력 집계 주기(예: "15m"). 가격 변화율도 동일
                주기 캔들 2개로 측정해 OI 창과 정합한다.
            oi_lookback_count: 현재 OI 를 몇 개 주기 전과 비교할지(기본 1 = 1주기 전).
        """
        if collector is None:
            logger.warning("[OIScanner] collector 가 None — scan() 은 항상 빈 결과")
        self.collector = collector
        self.oi_change_threshold_pct = oi_change_threshold_pct
        self.price_change_threshold_pct = price_change_threshold_pct
        self.top_n = max(1, int(top_n))
        self.oi_lookback_period = oi_lookback_period
        self.oi_lookback_count = max(1, int(oi_lookback_count))

    async def scan(self, active_pairs: list[str]) -> list[Candidate]:
        """active_pairs 를 스캔해 급등 후보 리스트를 반환한다.

        Args:
            active_pairs: PairWhitelist.get_active() 가 반환한 거래 가능 페어.

        Returns:
            OI 급증 + 가격 급등을 동시 충족한 Candidate 리스트
            (oi_change_pct 내림차순, 최대 top_n 개). 첫 스캔이거나
            충족 페어가 없으면 빈 리스트.
        """
        if self.collector is None or not active_pairs:
            return []

        candidates: list[Candidate] = []
        for symbol in active_pairs:
            try:
                candidate = await self._scan_one(symbol)
            except Exception as e:
                logger.error("[OIScanner] %s 스캔 실패: %s — 스킵", symbol, e)
                continue
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.oi_change_pct, reverse=True)
        result = candidates[: self.top_n]
        logger.info(
            "[OIScanner] 스캔 완료: %d 페어 → 후보 %d (반환 %d)",
            len(active_pairs), len(candidates), len(result),
        )
        return result

    async def _scan_one(self, symbol: str) -> Candidate | None:
        """단일 페어를 스캔한다.

        OI 이력(lookback 윈도우) 기준으로 변화율을 계산한다. 이력이 부족하면
        (데이터 < count+1) None 을 반환한다(룩어헤드/허위신호 방지).

        Returns:
            임계를 충족하면 Candidate, 아니면 None.
        """
        need = self.oi_lookback_count + 1
        hist = await self.collector.get_open_interest_history(
            symbol, period=self.oi_lookback_period, limit=need
        )
        if not hist or len(hist) < need:
            logger.debug(
                "[OIScanner] %s OI 이력 부족(%d/%d) — 스킵",
                symbol, len(hist) if hist else 0, need,
            )
            return None

        baseline = hist[0]      # count 주기 전
        oi_now = hist[-1]       # 최신
        if baseline <= 0:
            logger.warning("[OIScanner] %s 기준 OI 비정상(%.2f) — 스킵", symbol, baseline)
            return None

        price = await self.collector.get_ticker_price(symbol)
        if price is None:
            logger.warning("[OIScanner] %s 시세 조회 불가 — 스킵", symbol)
            return None

        oi_change_pct = (oi_now - baseline) / baseline * 100.0
        price_change_pct = await self._price_change_pct(symbol)
        volume_24h = await self.collector.get_24h_quote_volume(symbol)

        # 임계 충족 여부: OI 급증 + 가격 급등(절댓값) 동시 충족
        if (oi_change_pct >= self.oi_change_threshold_pct
                and abs(price_change_pct) >= self.price_change_threshold_pct):
            return Candidate(
                symbol=symbol,
                price=price,
                oi_now=oi_now,
                oi_change_pct=round(oi_change_pct, 4),
                price_change_pct=round(price_change_pct, 4),
                volume_24h=volume_24h if volume_24h is not None else 0.0,
            )
        return None

    async def _price_change_pct(self, symbol: str) -> float:
        """OI 룩백 주기와 동일한 간격 캔들 2개의 종가 변화율(%)을 계산한다.

        OI 창과 가격 창을 정합한다(이전 구현은 OI 30초 vs 가격 1H 로 불일치).

        Returns:
            (최신 종가 - 직전 종가) / 직전 종가 × 100.
            캔들 부족·조회 실패·직전 종가 0 이하면 0.0.
        """
        candles = await self.collector.get_candles(symbol, self.oi_lookback_period, 2)
        if len(candles) < 2:
            return 0.0
        prev_close = candles[-2][3]   # (open,high,low,close,volume,timestamp)
        last_close = candles[-1][3]
        if prev_close <= 0:
            return 0.0
        return (last_close - prev_close) / prev_close * 100.0
