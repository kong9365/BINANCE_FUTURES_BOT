"""
data/ws_collector.py
=====================================================================
Microstructure WebSocket Collector — Phase 1 (데이터 레일).

설계 (운영자 critique 반영):
  - depth raw 100ms 는 *메모리 캐시*에만, 1초 bucket 만 Supabase 적재
    (520M rows/60일 폭주 방지)
  - aggTrades 는 모든 tick 적재 (Top-10 심볼 한정, agg_trade_id 로 dedup)
  - forceOrder@arr 는 1초당 심볼별 최대 1건 (Binance throttle) — 모두 적재
  - 모든 row 에 received_ts + latency_ms (실거래 시점 진단 위해)
  - WS 재연결 시 중복 방지: 모든 테이블 UNIQUE 제약 + upsert 사용

설계 원칙 (절대):
  - *알파/ML/R0/진입 로직 없음* — 데이터 적재만
  - Async (asyncio + python-binance.AsyncClient / websockets)
  - Batch insert (1000 rows 또는 5초 단위)
  - Auto-reconnect (지수 backoff)
  - Healthcheck (마지막 메시지 수신 시각 추적, Telegram alarm)
=====================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

# 빠른 json 파싱 (외부 의존 없이 stdlib 만)
def _json_loads(data):
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return json.loads(data)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ── 상수 (테이블/스트림 매핑) ──────────────────────────────────────
TABLE_L2 = "l2_book_snapshots"
TABLE_AGG = "agg_trades"
TABLE_LIQ = "liquidations"

DEPTH_LEVELS = 10                    # top-10 호가
DEPTH_BUCKET_SECONDS = 1             # 1초당 1 snapshot (downsample)
BATCH_MAX_ROWS = 1000                # 배치 한 번에 최대
BATCH_MAX_AGE_SECONDS = 5.0          # 마지막 적재 후 N초 지나면 강제 flush
RECONNECT_BACKOFF = (1, 2, 5, 10, 30)  # 초 (지수 backoff)
HEALTHCHECK_STALE_SECONDS = 60       # 메시지 60초 없으면 stale
# Binance WS 는 24시간 연결 제한이 있으므로 23h 시점에 선제 재연결 (정상 운영 루틴).
# 24h disconnect 를 장애 대응 아닌 *예측된 cycle* 로 처리.
PREEMPTIVE_RECONNECT_SECONDS = 23 * 3600     # 23시간


@dataclass
class WSCollectorConfig:
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    depth_levels: int = DEPTH_LEVELS
    depth_bucket_seconds: int = DEPTH_BUCKET_SECONDS
    batch_max_rows: int = BATCH_MAX_ROWS
    batch_max_age_seconds: float = BATCH_MAX_AGE_SECONDS
    healthcheck_stale_seconds: int = HEALTHCHECK_STALE_SECONDS
    use_testnet: bool = False
    duration_seconds: Optional[int] = None  # None = 무한, 숫자 = 자동 종료

    # ★ Phase 2 (Tiered Universe) — 심볼별 stream 종류 override
    # None (default) = 모든 symbols 에 (depth + aggTrade + forceOrder) — backward compat
    # 제공 시 (예: Tier 1 → 3종, Tier 2 → 2종) 해당 symbol 에 지정 stream 만 spawn
    # Tier 2 (light) 는 depth 미수집 → 네트워크/DB 부하 감소
    symbol_streams: Optional[Dict[str, tuple]] = None


@dataclass
class CollectorStats:
    """수집 통계 (healthcheck/heartbeat용)."""

    l2_rows_total: int = 0
    agg_rows_total: int = 0
    liq_rows_total: int = 0
    last_l2_msg_at: Optional[datetime] = None
    last_agg_msg_at: Optional[datetime] = None
    last_liq_msg_at: Optional[datetime] = None
    reconnects: int = 0
    preemptive_reconnects: int = 0       # 23h cycle 선제 재연결 (정상)
    queue_overflow_count: int = 0        # 운영자 요청 — 9지표 중 7번
    latency_ms_sum: float = 0.0
    latency_ms_count: int = 0
    latency_samples: List[float] = field(default_factory=list)   # p95 산출용 (마지막 N개)

    @property
    def avg_latency_ms(self) -> float:
        return self.latency_ms_sum / self.latency_ms_count if self.latency_ms_count else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        s = sorted(self.latency_samples)
        idx = int(len(s) * 0.95)
        return float(s[min(idx, len(s) - 1)])

    def record_latency(self, ms: float, max_samples: int = 10000) -> None:
        self.latency_ms_sum += ms
        self.latency_ms_count += 1
        self.latency_samples.append(ms)
        if len(self.latency_samples) > max_samples:
            # 단순 truncate (최근 N개만 — p95 의미 유지하면서 메모리 cap)
            self.latency_samples = self.latency_samples[-max_samples:]


# ── 파싱 (Binance WS payload → row dict) ──────────────────────────
def parse_depth_message(msg: Dict[str, Any], symbol: str,
                          received_ts: datetime,
                          bucket_seconds: int = 1,
                          levels: int = 10) -> Optional[Dict[str, Any]]:
    """diffBookDepth / partialBookDepth payload → 1초 bucket row.

    Args:
        msg: Binance diff depth payload (with 'b', 'a', 'E' fields) OR
             partialBookDepth payload (with 'bids', 'asks', 'E').
        symbol: 심볼.
        received_ts: 로컬 수신 시각 (tz-aware UTC).
        bucket_seconds: 1초 bucket 으로 truncate.
        levels: top-N depth.

    Returns:
        row dict OR None (불완전 payload).
    """
    bids_raw = msg.get("b") or msg.get("bids")
    asks_raw = msg.get("a") or msg.get("asks")
    exchange_ms = msg.get("E") or msg.get("T")
    if not bids_raw or not asks_raw or exchange_ms is None:
        return None
    try:
        bids = [[float(p), float(q)] for p, q in bids_raw[:levels]]
        asks = [[float(p), float(q)] for p, q in asks_raw[:levels]]
    except (ValueError, TypeError):
        return None
    if not bids or not asks:
        return None
    # 1초 bucket
    bucket_ts = received_ts.replace(microsecond=0)
    exchange_ts = datetime.fromtimestamp(int(exchange_ms) / 1000.0, tz=timezone.utc)
    mid = (bids[0][0] + asks[0][0]) / 2.0
    return {
        "ts": bucket_ts.isoformat(),
        "exchange_ts": exchange_ts.isoformat(),
        "received_ts": received_ts.isoformat(),
        "symbol": symbol,
        "bids": bids,
        "asks": asks,
        "mid_price": mid,
    }


def parse_agg_trade_message(msg: Dict[str, Any],
                              received_ts: datetime) -> Optional[Dict[str, Any]]:
    """aggTrade / trade payload → row dict.

    `@aggTrade` 와 `@trade` 둘 다 지원:
      - aggTrade: `a` = aggTradeId, `e`="aggTrade"
      - trade   : `t` = tradeId,    `e`="trade"
    Binance USDT-M `@aggTrade` 가 현재 0건 반환 → `@trade` 폴백.
    agg_trade_id 컬럼은 둘의 ID 공통 채움; `source_stream` 필드로 lineage 명시.
    """
    try:
        symbol = msg["s"]
        if "a" in msg:
            trade_id = msg["a"]
            source_stream = "aggTrade"
        elif "t" in msg:
            trade_id = msg["t"]
            source_stream = "trade"
        else:
            return None
        agg_id = int(trade_id)
        price = float(msg["p"])
        qty = float(msg["q"])
        trade_ms = int(msg["T"])
        is_buyer_maker = bool(msg["m"])
    except (KeyError, ValueError, TypeError):
        return None
    exchange_ts = datetime.fromtimestamp(trade_ms / 1000.0, tz=timezone.utc)
    return {
        "ts": exchange_ts.isoformat(),
        "exchange_ts": exchange_ts.isoformat(),
        "received_ts": received_ts.isoformat(),
        "symbol": symbol,
        "agg_trade_id": agg_id,
        "price": price,
        "qty": qty,
        "is_buyer_maker": is_buyer_maker,
        "source_stream": source_stream,
    }


def parse_liquidation_message(msg: Dict[str, Any],
                                received_ts: datetime) -> Optional[Dict[str, Any]]:
    """forceOrder@arr payload → row dict.

    Wrapper schema: {"e":"forceOrder","E":...,"o":{...}}
    또는 직접 order 객체.

    Order fields:
      s = symbol, S = side(BUY/SELL), p = price, q = original qty, T = trade time
    """
    o = msg.get("o") or msg
    try:
        symbol = o["s"]
        side = o["S"]
        if side not in ("BUY", "SELL"):
            return None
        price = float(o["p"])
        qty = float(o["q"])
        trade_ms = int(o.get("T") or msg.get("E"))
    except (KeyError, ValueError, TypeError):
        return None
    exchange_ts = datetime.fromtimestamp(trade_ms / 1000.0, tz=timezone.utc)
    return {
        "ts": exchange_ts.isoformat(),
        "exchange_ts": exchange_ts.isoformat(),
        "received_ts": received_ts.isoformat(),
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
    }


# ── Latency helper (지연 측정) ────────────────────────────────────
def compute_latency_ms(exchange_iso: str, received_iso: str) -> int:
    """received_ts - exchange_ts (ms)."""
    e = datetime.fromisoformat(exchange_iso.replace("Z", "+00:00"))
    r = datetime.fromisoformat(received_iso.replace("Z", "+00:00"))
    return int((r - e).total_seconds() * 1000)


# ── BatchBuffer (메모리 누적 + 주기 flush) ────────────────────────
class BatchBuffer:
    """심볼별/테이블별 row buffer. UNIQUE 충돌 자동 dedup (upsert).

    `write_sem` (선택): 모든 BatchBuffer 가 공유하면 Supabase 동시 쓰기 직렬화 →
    WinError 10035 같은 connection pool 포화 회피. WSCollector 가 단일 Semaphore(1)
    을 모든 buffer 에 주입.
    """

    def __init__(self, persist, table: str, conflict_cols: str,
                 max_rows: int = BATCH_MAX_ROWS,
                 max_age_seconds: float = BATCH_MAX_AGE_SECONDS,
                 write_sem: Optional[asyncio.Semaphore] = None):
        self.persist = persist
        self.table = table
        self.conflict_cols = conflict_cols
        self.max_rows = max_rows
        self.max_age_seconds = max_age_seconds
        self._rows: List[Dict[str, Any]] = []
        self._last_flush_ts: float = time.monotonic()
        self._lock = asyncio.Lock()
        self._write_sem = write_sem

    async def add(self, row: Dict[str, Any]) -> None:
        async with self._lock:
            self._rows.append(row)
            if len(self._rows) >= self.max_rows:
                await self._flush_locked()

    async def flush_if_stale(self) -> None:
        async with self._lock:
            if not self._rows:
                self._last_flush_ts = time.monotonic()
                return
            if (time.monotonic() - self._last_flush_ts) >= self.max_age_seconds:
                await self._flush_locked()

    async def flush(self) -> int:
        async with self._lock:
            return await self._flush_locked()

    async def _flush_locked(self) -> int:
        if not self._rows:
            return 0
        batch = self._rows
        self._rows = []
        try:
            # 모든 buffer 가 공유하는 semaphore 로 Supabase 동시 호출 직렬화
            # (Windows 의 WinError 10035 - 비동기 소켓 포화 방지)
            if self._write_sem is not None:
                async with self._write_sem:
                    await asyncio.to_thread(
                        self.persist.upsert_many, self.table, batch,
                        self.conflict_cols,
                    )
            else:
                await asyncio.to_thread(
                    self.persist.upsert_many, self.table, batch,
                    self.conflict_cols,
                )
            self._last_flush_ts = time.monotonic()
            logger.debug("[Batch] %s flushed %d rows", self.table, len(batch))
            return len(batch)
        except Exception as e:  # noqa: BLE001
            logger.exception("[Batch] %s flush 실패: %s — outbox 폴백", self.table, e)
            # persistence.py 가 자체 outbox 폴백 처리
            return 0


# ── 메인 collector ────────────────────────────────────────────────
class WSCollector:
    """3 stream 동시 구독 + 1초 downsample (depth) + batch insert.

    Attributes:
        cfg: 설정.
        persist: SupabasePersistence (data/persistence.py).
        stats: 운영 통계.
    """

    def __init__(self, cfg: WSCollectorConfig, persist):
        self.cfg = cfg
        self.persist = persist
        self.stats = CollectorStats()
        self._depth_cache: Dict[str, Dict[str, Any]] = {}    # symbol → last row
        self._depth_last_bucket: Dict[str, str] = {}          # symbol → last bucket iso
        # 모든 buffer 공유 semaphore — Supabase 동시 호출 직렬화 (WinError 10035 방지)
        self._write_sem = asyncio.Semaphore(1)
        self._buffers: Dict[str, BatchBuffer] = {
            TABLE_L2: BatchBuffer(persist, TABLE_L2, "symbol,ts", cfg.batch_max_rows,
                                    cfg.batch_max_age_seconds, self._write_sem),
            TABLE_AGG: BatchBuffer(persist, TABLE_AGG, "symbol,agg_trade_id",
                                    cfg.batch_max_rows, cfg.batch_max_age_seconds,
                                    self._write_sem),
            TABLE_LIQ: BatchBuffer(persist, TABLE_LIQ, "symbol,exchange_ts,side,price,qty",
                                    cfg.batch_max_rows, cfg.batch_max_age_seconds,
                                    self._write_sem),
        }
        # Phase 2-E: 1m aggregate (raw 1h TTL 후 영구 보존용)
        from data.aggregator_1m import TradeAggregator1m
        self._agg_1m = TradeAggregator1m(persist, write_sem=self._write_sem,
                                           source_stream="trade")
        self._tasks: List[asyncio.Task] = []
        self._running: bool = False
        self._stop_at: Optional[float] = None

    # ── 메시지 핸들러 ────────────────────────────────────────────
    async def handle_depth(self, symbol: str, msg: Dict[str, Any]) -> None:
        """diffBookDepth → 메모리 캐시 → 1초 bucket 만 적재."""
        now = datetime.now(timezone.utc)
        row = parse_depth_message(msg, symbol, now, self.cfg.depth_bucket_seconds,
                                    self.cfg.depth_levels)
        if row is None:
            return
        self.stats.last_l2_msg_at = now
        # latency 통계 (p95 포함)
        lat = compute_latency_ms(row["exchange_ts"], row["received_ts"])
        self.stats.record_latency(lat)
        # 메모리 캐시: symbol 별로 *최신* row 만 유지
        self._depth_cache[symbol] = row
        # 1초 bucket 변경 시 *직전* bucket row 를 batch 에 추가
        cur_bucket = row["ts"]
        prev_bucket = self._depth_last_bucket.get(symbol)
        if prev_bucket and prev_bucket != cur_bucket:
            # 직전 bucket 의 *마지막* 캐시 row 가 이미 prev_bucket 의 ts 였을 것
            # → 새 bucket 진입 시 직전 row 를 한 번 적재
            # 정확히는: prev_bucket 시점의 마지막 캐시 = 이미 적재 안 됐으므로 add
            # 단순화: 새 bucket 의 *첫* row 가 도착하기 직전 직전 캐시를 commit
            # (현재 self._depth_cache[symbol] 은 이미 새 bucket — 직전 bucket 데이터는 잃음)
            # 더 정확: bucket-keyed 캐시 사용. 아래 _emit_pending_depth_buckets 가 처리.
            pass
        self._depth_last_bucket[symbol] = cur_bucket
        # 단순 모델: 매 1초 tick 마다 모든 심볼의 캐시 commit (별도 task)

    async def handle_agg_trade(self, msg: Dict[str, Any]) -> None:
        """aggTrade/trade → raw batch + 1m aggregator 동시 갱신."""
        now = datetime.now(timezone.utc)
        row = parse_agg_trade_message(msg, now)
        if row is None:
            return
        self.stats.last_agg_msg_at = now
        self.stats.agg_rows_total += 1
        await self._buffers[TABLE_AGG].add(row)
        # Phase 2-E: 1m aggregate 동시 누적 (raw 1h TTL 후에도 영구 보존)
        try:
            # exchange_ts (ISO) → datetime
            ex_ts = datetime.fromisoformat(row["exchange_ts"].replace("Z", "+00:00"))
            await self._agg_1m.record_trade(
                symbol=row["symbol"], price=row["price"], qty=row["qty"],
                is_buyer_maker=row["is_buyer_maker"], trade_ts=ex_ts,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[Agg1m] record_trade 실패 — raw 만 보존: %s", e)

    async def handle_liquidation(self, msg: Dict[str, Any]) -> None:
        """forceOrder@arr → 즉시 batch."""
        now = datetime.now(timezone.utc)
        row = parse_liquidation_message(msg, now)
        if row is None:
            return
        self.stats.last_liq_msg_at = now
        self.stats.liq_rows_total += 1
        await self._buffers[TABLE_LIQ].add(row)

    # ── Depth 1초 commit task ────────────────────────────────────
    async def _depth_commit_loop(self) -> None:
        """매 1초마다 self._depth_cache 의 모든 심볼 row 를 batch 에 추가.

        depth 100ms raw → 메모리 캐시 → 1초당 1 snapshot 만 Supabase. Top-10 × 86400/일 ×
        60일 = 51.8M rows (관리 가능).
        """
        last_committed: Dict[str, str] = {}
        while self._running:
            await asyncio.sleep(1.0)
            for symbol, row in list(self._depth_cache.items()):
                ts_key = row["ts"]
                if last_committed.get(symbol) == ts_key:
                    continue   # 같은 bucket 중복 방지(추가 안전망 — UNIQUE 가 최종)
                await self._buffers[TABLE_L2].add(row)
                self.stats.l2_rows_total += 1
                last_committed[symbol] = ts_key

    # ── Batch periodic flush task ────────────────────────────────
    async def _periodic_flush_loop(self) -> None:
        last_agg1m_flush = time.monotonic()
        while self._running:
            await asyncio.sleep(self.cfg.batch_max_age_seconds)
            for buf in self._buffers.values():
                await buf.flush_if_stale()
            # 1m aggregator: 30초마다 완료 bucket flush (cutoff = 현재 분 시작)
            if (time.monotonic() - last_agg1m_flush) >= 30.0:
                try:
                    n = await self._agg_1m.flush_completed_buckets(
                        datetime.now(timezone.utc)
                    )
                    if n > 0:
                        logger.debug("[Agg1m] periodic flush %d rows", n)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[Agg1m] periodic flush 실패: %s", e)
                last_agg1m_flush = time.monotonic()

    # ── Healthcheck ──────────────────────────────────────────────
    def is_healthy(self) -> bool:
        """마지막 메시지 수신 시각 기준 stale 여부."""
        now = datetime.now(timezone.utc)
        thresh = self.cfg.healthcheck_stale_seconds
        for t in (self.stats.last_l2_msg_at, self.stats.last_agg_msg_at,
                  self.stats.last_liq_msg_at):
            if t is not None and (now - t).total_seconds() <= thresh:
                return True
        return False

    # ── 심볼-stream 매핑 결정 (multiplex 빌드용) ────────────────
    def _resolve_symbol_streams(self) -> Dict[str, tuple]:
        """cfg.symbol_streams (Phase 2 Tiered) 또는 backward-compat (모든 symbols → 전 stream)."""
        if self.cfg.symbol_streams is not None:
            return dict(self.cfg.symbol_streams)
        # Backward compat: 모든 symbols 에 전 stream 부여 (Phase 1-C 와 동일)
        return {s: ("depth", "aggTrade", "forceOrder") for s in self.cfg.symbols}

    # ── WS 수신 task (외부 client 주입 가능 — 테스트 hook) ──────
    async def run_ws_streams(self, ws_factory: Optional[Callable] = None) -> None:
        """Multiplex 단일 연결로 모든 stream 동시 구독 (Phase 2).

        Args:
            ws_factory: 테스트용 mock factory. None 이면 실제 Binance 연결.

        설계 (운영자 critique #2 반영 — DNS/TLS handshake 폭증 회피):
          - 다중 stream 을 단일 multiplex socket 으로 묶음 (1 connection)
          - Binance multiplex 응답: {"stream": "btcusdt@aggTrade", "data": {...}}
          - 단일 queue (max_queue_size=1000) — 전체 부하 단일 점검점
        """
        if ws_factory is not None:
            # 테스트 모드: factory 가 직접 메시지 generator 반환
            async for stream_name, msg in ws_factory():
                await self._dispatch(stream_name, msg)
            return

        # 실제 Binance 연결 — raw `websockets` combined stream
        from data.microstructure_universe import build_multiplex_streams

        symbol_streams_dict = self._resolve_symbol_streams()
        multiplex_streams = build_multiplex_streams(symbol_streams_dict)
        if not multiplex_streams:
            logger.warning("[WS] 구독할 stream 없음 — symbols 또는 symbol_streams 확인")
            return

        # 시작 로그 (운영자 요청 — stream count 출력)
        n_depth = sum(1 for s in multiplex_streams if "@depth" in s)
        # 'trade' substring 매치 (@aggTrade 와 @trade 둘 다 포함)
        n_trade = sum(1 for s in multiplex_streams if "@trade" in s or "@aggTrade" in s)
        n_force = sum(1 for s in multiplex_streams if "!forceOrder" in s)
        logger.info(
            "[WS] multiplex stream count: total=%d (depth=%d, trade=%d, forceOrder=%d) "
            "for %d symbols",
            len(multiplex_streams), n_depth, n_trade, n_force, len(symbol_streams_dict),
        )

        # python-binance 1.0.36 의 futures_multiplex_socket 은 depth subscription 을 누락하는
        # 버그 (probe 검증 — agg/force 만 전달, depth 0건). 그래서 raw `websockets` 로 직접
        # combined stream endpoint 연결. 단일 connection 으로 모든 stream type 처리.
        await self._run_raw_multiplex_loop(multiplex_streams)

    async def _run_raw_multiplex_loop(self, streams: List[str]) -> None:
        """Raw `websockets` 로 Binance combined stream 단일 connection 무한 루프.

        - URL: wss://fstream.binance.com/stream?streams=a/b/c (또는 testnet)
        - 23h 시점 *선제 재연결* — Binance 의 24h disconnect 정책을 정상 cycle 처리
        - 비정상 disconnect → 지수 backoff
        - 모든 stream 종류(depth/trade/forceOrder)를 1 connection 으로 처리
        """
        import websockets
        host = "stream.binancefuture.com" if self.cfg.use_testnet else "fstream.binance.com"
        url = f"wss://{host}/stream?streams={'/'.join(streams)}"
        backoff_idx = 0
        while self._running:
            connect_started = time.monotonic()
            try:
                async with websockets.connect(url, max_size=4 * 1024 * 1024,
                                                ping_interval=20, ping_timeout=60) as ws:
                    backoff_idx = 0
                    while self._running:
                        # 23h 시점 선제 재연결 (Binance 24h limit 회피)
                        if (time.monotonic() - connect_started) >= PREEMPTIVE_RECONNECT_SECONDS:
                            self.stats.preemptive_reconnects += 1
                            logger.info("[WS] preemptive reconnect (23h cycle, total=%d)",
                                         self.stats.preemptive_reconnects)
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            # idle — 다음 iteration 에서 PREEMPTIVE 체크 후 계속
                            continue
                        try:
                            msg = _json_loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        await self._dispatch_multiplex(msg)
                # 정상 break (선제 재연결) → 즉시 재연결
                continue
            except Exception as e:  # noqa: BLE001
                self.stats.reconnects += 1
                # queue overflow 패턴 감지 (운영자 9-지표 #7)
                if "queue" in str(e).lower() and "overflow" in str(e).lower():
                    self.stats.queue_overflow_count += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning("[WS] multiplex 끊김: %s — %ds 후 재연결", e, wait)
                backoff_idx += 1
                await asyncio.sleep(wait)

    async def _dispatch_multiplex(self, msg: Dict[str, Any]) -> None:
        """multiplex envelope ({stream, data}) 디스패치.

        stream 이름으로 handler 결정:
          - `{sym}@depth20@100ms` → handle_depth(sym, data)
          - `{sym}@aggTrade` → handle_agg_trade(data)
          - `!forceOrder@arr` → _maybe_handle_liq(data)  (universe 필터 적용)
        """
        stream_name = msg.get("stream", "")
        data = msg.get("data", msg) if isinstance(msg, dict) else msg
        if "@depth" in stream_name:
            sym = stream_name.split("@", 1)[0].upper()
            await self.handle_depth(sym, data)
        elif "@aggTrade" in stream_name or "@trade" in stream_name:
            # `@trade` 도 routing — Binance `@aggTrade` 가 0 건 시 폴백 사용
            await self.handle_agg_trade(data)
        elif "!forceOrder" in stream_name or msg.get("e") == "forceOrder":
            # forceOrder@arr 는 전 마켓 broadcast → universe 필터
            if isinstance(data, list):
                for item in data:
                    await self._maybe_handle_liq(item)
            else:
                await self._maybe_handle_liq(data)

    async def _run_depth_stream(self, bm, symbol: str) -> None:
        """diffBookDepth@100ms — 메모리 캐시 갱신.

        python-binance 1.0.36: `futures_depth_socket(symbol, depth='20')`.
        depth 값은 string 또는 int (라이브러리가 stream URL `@depth20@100ms` 로 빌드).
        """
        backoff_idx = 0
        while self._running:
            try:
                async with bm.futures_depth_socket(symbol.lower(), depth="20") as stream:
                    backoff_idx = 0
                    while self._running:
                        msg = await stream.recv()
                        # multiplex envelope 가능
                        payload = msg.get("data", msg) if isinstance(msg, dict) else msg
                        await self.handle_depth(symbol, payload)
            except Exception as e:  # noqa: BLE001
                self.stats.reconnects += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning("[WS] depth %s 끊김: %s — %ds 후 재연결", symbol, e, wait)
                backoff_idx += 1
                await asyncio.sleep(wait)

    async def _run_agg_stream(self, bm, symbol: str) -> None:
        """python-binance 1.0.36: `aggtrade_futures_socket(symbol)`.
        (구버전 명 `futures_aggtrade_socket` 은 없음)."""
        backoff_idx = 0
        while self._running:
            try:
                async with bm.aggtrade_futures_socket(symbol.lower()) as stream:
                    backoff_idx = 0
                    while self._running:
                        msg = await stream.recv()
                        # multiplex 응답일 수 있음: {"stream":..., "data":{...}}
                        payload = msg.get("data", msg) if isinstance(msg, dict) else msg
                        await self.handle_agg_trade(payload)
            except Exception as e:  # noqa: BLE001
                self.stats.reconnects += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning("[WS] aggTrade %s 끊김: %s — %ds 후 재연결", symbol, e, wait)
                backoff_idx += 1
                await asyncio.sleep(wait)

    async def _run_force_order_stream(self, bm) -> None:
        """!forceOrder@arr — 전 심볼 청산 (Binance throttle 1/sec/symbol).

        python-binance 1.0.36 에는 직접 method 없음 → `futures_multiplex_socket(["!forceOrder@arr"])`.
        """
        backoff_idx = 0
        while self._running:
            try:
                async with bm.futures_multiplex_socket(["!forceOrder@arr"]) as stream:
                    backoff_idx = 0
                    while self._running:
                        msg = await stream.recv()
                        # multiplex envelope: {"stream":"!forceOrder@arr","data":{...}}
                        payload = msg.get("data", msg) if isinstance(msg, dict) else msg
                        if isinstance(payload, list):
                            for item in payload:
                                await self._maybe_handle_liq(item)
                        else:
                            await self._maybe_handle_liq(payload)
            except Exception as e:  # noqa: BLE001
                self.stats.reconnects += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning("[WS] forceOrder 끊김: %s — %ds 후 재연결", e, wait)
                backoff_idx += 1
                await asyncio.sleep(wait)

    async def _maybe_handle_liq(self, msg: Dict[str, Any]) -> None:
        """forceOrder broadcast 에서 universe 심볼만 적재.

        Tiered universe (symbol_streams) 사용 시 그 키셋 우선, 아니면 cfg.symbols.
        """
        o = msg.get("o") or msg
        sym = o.get("s")
        if sym is None:
            return
        if self.cfg.symbol_streams is not None:
            universe = set(self.cfg.symbol_streams.keys())
        else:
            universe = set(self.cfg.symbols)
        if sym in universe:
            await self.handle_liquidation(msg)

    async def _dispatch(self, stream_name: str, msg: Dict[str, Any]) -> None:
        """테스트용 dispatch (실 WS 와 별도)."""
        if stream_name.startswith("depth:"):
            symbol = stream_name.split(":", 1)[1]
            await self.handle_depth(symbol, msg)
        elif stream_name == "aggTrade":
            await self.handle_agg_trade(msg)
        elif stream_name == "forceOrder":
            await self.handle_liquidation(msg)

    # ── 라이프사이클 ─────────────────────────────────────────────
    async def start(self, ws_factory: Optional[Callable] = None) -> None:
        """모든 task 시작. duration_seconds 도달 또는 stop() 까지 대기."""
        self._running = True
        if self.cfg.duration_seconds:
            self._stop_at = time.monotonic() + self.cfg.duration_seconds
        # 백그라운드 tasks
        self._tasks = [
            asyncio.create_task(self._depth_commit_loop()),
            asyncio.create_task(self._periodic_flush_loop()),
            asyncio.create_task(self.run_ws_streams(ws_factory)),
        ]
        if self._stop_at:
            # duration 도달 시 자동 종료
            try:
                await asyncio.sleep(self.cfg.duration_seconds)
                await self.stop()
            except asyncio.CancelledError:
                pass
        else:
            # 무한 대기
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        """모든 task 취소 + 최종 flush (raw buffers + 1m aggregator)."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        # 마지막 flush — raw buffers
        for buf in self._buffers.values():
            await buf.flush()
        # 1m aggregator — 모든 pending bucket 마저 적재
        try:
            n = await self._agg_1m.flush_all()
            logger.info("[Agg1m] final flush: %d rows", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Agg1m] final flush 실패: %s", e)
        logger.info("[WSCollector] stopped. stats=%s", self.stats)
