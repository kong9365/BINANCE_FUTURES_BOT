"""
data/aggregator_1m.py
=====================================================================
Trade-tick → 1분 OHLC + Volume + CVD 집계기 (Phase 2-E disk mitigation).

Context: raw `agg_trades` (full tick) 는 무료 Supabase 2GB 디스크에 30분만에
가득 차 Postgres crash 발생. 대안: raw 는 1시간 TTL 단기 보존 + 1m aggregate
영구 보존 → Phase 3 IC 분석 (5-15분 horizon) 에 충분 + 디스크 60일 ~440MB 안전.

설계:
  - 메모리 상태: {symbol: {bucket_ts_iso: {OHLC, vol, buy_vol, sell_vol, n, notional}}}
  - record_trade(): 현재 bucket 업데이트
  - flush_completed_buckets(now): now 의 1분 bucket 보다 이전 모든 bucket → Supabase
  - upsert (symbol+ts UNIQUE 멱등) — 중복 호출 OK
  - 순수 메모리 상태 + 단일 batch flush — Supabase 연결 1회/분
  - WSCollector 의 _write_sem 공유 (Supabase 동시 호출 직렬화)

비용 모델 (운영자 critique 반영):
  - 무료 Supabase 디스크 2GB 캡 안에서 60-90일 누적 가능
  - 51 syms × 1440 min/day × ~100 bytes/row ≈ 7.3 MB/day
  - 60일 → ~440 MB (안전)

알파/ML/R0/진입 로직 *없음*. 데이터 변환만.
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


TABLE_AGG_1M = "agg_trades_1m"


@dataclass
class Bucket1m:
    """단일 (symbol, 1m bucket) 의 집계 상태."""

    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    n_trades: int = 0
    notional_usd: float = 0.0

    def update(self, price: float, qty: float, is_buyer_maker: bool) -> None:
        """단일 trade 반영. price/qty 양수 가정.

        Binance convention: `is_buyer_maker=True` → 매도자가 *taker* (가격 누름);
        `False` → 매수자가 taker (가격 올림). CVD = buy_volume - sell_volume.
        """
        if self.n_trades == 0:
            self.open_price = price
            self.high_price = price
            self.low_price = price
        if price > self.high_price:
            self.high_price = price
        if price < self.low_price:
            self.low_price = price
        self.close_price = price
        self.volume += qty
        if is_buyer_maker:
            # 매도자가 공격적 (taker sell)
            self.sell_volume += qty
        else:
            # 매수자가 공격적 (taker buy)
            self.buy_volume += qty
        self.n_trades += 1
        self.notional_usd += qty * price

    def to_row(self, symbol: str, bucket_ts_iso: str,
                source_stream: str = "trade") -> Dict[str, Any]:
        return {
            "ts": bucket_ts_iso,
            "symbol": symbol,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "volume": self.volume,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "n_trades": self.n_trades,
            "notional_usd": self.notional_usd,
            "cvd_delta": self.buy_volume - self.sell_volume,
            "source_stream": source_stream,
        }


def floor_to_minute(ts: datetime) -> datetime:
    """tz-aware datetime → 같은 분의 시작 (sec=0, usec=0)."""
    return ts.replace(second=0, microsecond=0)


class TradeAggregator1m:
    """1분 OHLC+volume+CVD 집계기 (메모리 상태 + Supabase upsert).

    Args:
        persist: SupabasePersistence (data/persistence.py)
        write_sem: WSCollector 공유 semaphore (Supabase 동시 호출 직렬화)
        source_stream: 'trade' or 'aggTrade' (lineage)
    """

    def __init__(self, persist, write_sem: Optional[asyncio.Semaphore] = None,
                 source_stream: str = "trade"):
        self.persist = persist
        self.write_sem = write_sem
        self.source_stream = source_stream
        # {symbol: {bucket_iso: Bucket1m}}
        self._buckets: Dict[str, Dict[str, Bucket1m]] = {}
        self._lock = asyncio.Lock()
        self._stats_flushed = 0

    async def record_trade(self, symbol: str, price: float, qty: float,
                            is_buyer_maker: bool, trade_ts: datetime) -> None:
        """단일 trade tick 을 적절 1m bucket 에 반영."""
        if price <= 0 or qty <= 0:
            return
        bucket_dt = floor_to_minute(trade_ts)
        bucket_iso = bucket_dt.isoformat()
        async with self._lock:
            sym_buckets = self._buckets.setdefault(symbol, {})
            b = sym_buckets.setdefault(bucket_iso, Bucket1m())
            b.update(price, qty, is_buyer_maker)

    async def flush_completed_buckets(self, now: datetime) -> int:
        """now 의 1m bucket 보다 *이전* 모든 bucket → Supabase upsert.

        예: now=10:23:45 → bucket 10:22 이하 전부 flush, 10:23 은 유지(진행 중).

        Returns:
            flushed row count.
        """
        cutoff = floor_to_minute(now).isoformat()
        async with self._lock:
            to_flush: List[Dict[str, Any]] = []
            for sym, sym_buckets in list(self._buckets.items()):
                completed = [b_iso for b_iso in sym_buckets if b_iso < cutoff]
                for b_iso in completed:
                    bucket = sym_buckets.pop(b_iso)
                    if bucket.n_trades > 0:
                        to_flush.append(bucket.to_row(sym, b_iso, self.source_stream))
                if not sym_buckets:
                    del self._buckets[sym]
            if not to_flush:
                return 0
        # Upsert outside lock (Supabase call may block)
        try:
            if self.write_sem is not None:
                async with self.write_sem:
                    await asyncio.to_thread(
                        self.persist.upsert_many, TABLE_AGG_1M, to_flush, "symbol,ts",
                    )
            else:
                await asyncio.to_thread(
                    self.persist.upsert_many, TABLE_AGG_1M, to_flush, "symbol,ts",
                )
            self._stats_flushed += len(to_flush)
            logger.debug("[Agg1m] flushed %d rows", len(to_flush))
            return len(to_flush)
        except Exception as e:  # noqa: BLE001
            logger.exception("[Agg1m] flush 실패 → outbox 폴백: %s", e)
            return 0

    async def flush_all(self) -> int:
        """모든 bucket 강제 flush (shutdown 시)."""
        # 충분히 미래의 시각으로 floor 부르면 모두 cutoff 이하 됨
        future = datetime.now(timezone.utc) + timedelta(days=1)
        return await self.flush_completed_buckets(future)

    @property
    def n_pending(self) -> int:
        return sum(len(v) for v in self._buckets.values())

    @property
    def n_flushed_total(self) -> int:
        return self._stats_flushed
