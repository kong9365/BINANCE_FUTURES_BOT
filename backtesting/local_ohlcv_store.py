"""
backtesting/local_ohlcv_store.py
=====================================================================
로컬 sqlite ohlcv 저장소 (M11, Supabase 대체).

근거:
  - 운영자 결정 (2026-05-27): Supabase 폐기 → 로컬 only
  - db/migrations/v3_2_1_to_v3_2_2.sql (ohlcv_local 테이블)
  - REFACTOR_PLAN_v2_BLUEPRINT.md M11

기존 자산 재사용:
  - data/collector.BinanceDataCollector (REST API klines)
  - backtesting/backfill_history.py (페이지네이션 패턴)

사용:
    store = LocalOhlcvStore(db_path="data/bot.db")
    store.upsert_many(
        symbol="SOLUSDT", interval="1d",
        rows=[(ts, o, h, l, c, v, ...), ...]
    )
    candles = store.get_candles("SOLUSDT", "1d", limit=500)
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _to_iso(ts: int | float | str | datetime) -> str:
    """다양한 timestamp 입력을 ISO8601 UTC 로 정규화."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, str):
        return ts
    # int/float — ms 또는 s
    if ts > 1e12:  # ms
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class LocalOhlcvStore:
    """ohlcv_local 테이블 CRUD (M11).

    스키마 (v3.2.2):
        symbol, interval, ts (PK), open, high, low, close, volume,
        quote_volume, trades, taker_buy_base, taker_buy_quote, closed
    """

    def __init__(self, db_path: str | Path) -> None:
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = str(db_path)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def upsert_many(
        self,
        symbol: str,
        interval: str,
        rows: list[tuple | dict],
    ) -> int:
        """단일 symbol+interval 의 캔들 일괄 upsert (멱등).

        Args:
            symbol: "SOLUSDT"
            interval: "1d" / "1h" / "5m" / "1m"
            rows: list of tuples (ts, open, high, low, close, volume[,
                  quote_volume, trades, taker_buy_base, taker_buy_quote])
                  또는 list of dicts (각 키 명시).

        Returns:
            INSERT/REPLACE 된 row 수.
        """
        if not rows:
            return 0
        normalized = []
        for r in rows:
            if isinstance(r, dict):
                normalized.append((
                    symbol, interval, _to_iso(r["ts"]),
                    float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r["volume"]),
                    float(r.get("quote_volume", 0) or 0),
                    int(r.get("trades", 0) or 0),
                    float(r.get("taker_buy_base", 0) or 0),
                    float(r.get("taker_buy_quote", 0) or 0),
                    int(r.get("closed", 1) or 1),
                ))
            else:
                # tuple — (ts, o, h, l, c, v, [qv, n, tbb, tbq, closed])
                ts, o, h, l, c, v = r[0], r[1], r[2], r[3], r[4], r[5]
                qv = r[6] if len(r) > 6 else 0
                trades = r[7] if len(r) > 7 else 0
                tbb = r[8] if len(r) > 8 else 0
                tbq = r[9] if len(r) > 9 else 0
                closed = r[10] if len(r) > 10 else 1
                normalized.append((
                    symbol, interval, _to_iso(ts),
                    float(o), float(h), float(l), float(c), float(v),
                    float(qv), int(trades), float(tbb), float(tbq),
                    int(closed),
                ))

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO ohlcv_local
                    (symbol, interval, ts, open, high, low, close, volume,
                     quote_volume, trades, taker_buy_base, taker_buy_quote, closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                normalized,
            )
            conn.commit()
        return len(normalized)

    def get_candles(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_ts: Optional[str] = None,
        end_ts: Optional[str] = None,
    ) -> list[tuple]:
        """캔들 조회 (시간 오름차순). breakout.evaluate_breakout 입력 형식.

        Returns:
            list of (open, high, low, close, volume, ts) — 시간 ASC.
        """
        with self._connect() as conn:
            where_clauses = ["symbol = ?", "interval = ?", "closed = 1"]
            params: list = [symbol, interval]
            if start_ts:
                where_clauses.append("ts >= ?")
                params.append(start_ts)
            if end_ts:
                where_clauses.append("ts <= ?")
                params.append(end_ts)
            where_sql = " AND ".join(where_clauses)
            rows = conn.execute(
                f"SELECT open, high, low, close, volume, ts "
                f"FROM ohlcv_local WHERE {where_sql} "
                f"ORDER BY ts DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        # 시간 오름차순 정렬
        return [tuple(r) for r in reversed(rows)]

    def count(self, symbol: Optional[str] = None, interval: Optional[str] = None) -> int:
        """저장된 row 수 (symbol+interval 선택적)."""
        with self._connect() as conn:
            if symbol and interval:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_local WHERE symbol = ? AND interval = ?",
                    (symbol, interval),
                ).fetchone()
            elif symbol:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_local WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_local"
                ).fetchone()
        return int(row[0]) if row else 0

    def get_symbols(self) -> list[str]:
        """저장된 distinct symbol 목록."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM ohlcv_local ORDER BY symbol"
            ).fetchall()
        return [r[0] for r in rows]

    def get_date_range(
        self, symbol: str, interval: str,
    ) -> Optional[tuple[str, str]]:
        """저장된 (min_ts, max_ts) — 없으면 None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(ts), MAX(ts) FROM ohlcv_local "
                "WHERE symbol = ? AND interval = ?",
                (symbol, interval),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return (row[0], row[1])
