"""
backtesting/backfill_history.py
=====================================================================
과거 OHLCV(+taker_buy_base) / 펀딩 이력 1회 backfill — 페이지네이션 (P4 가속).

배경 (docs/STRATEGY_REALISM_REVIEW.md):
  바이낸스 klines/funding 은 **상장 시점까지 수년치**를 startTime/endTime
  페이지네이션으로 받을 수 있다(OI 이력만 ~30일 제한). 따라서 OHLCV 만 쓰는
  돌파 전략의 walk-forward 검증은 forward 수집을 수개월 기다릴 필요 없이,
  과거를 backfill 하면 **지금 즉시** 가능하다.

수집:
  - klines: futures_klines(startTime, limit=1500) 순방향 페이지네이션. taker_buy_base
    (kline[9]) 포함 → Supabase ohlcv 에 멱등 upsert.
  - funding: futures_funding_rate(startTime, limit=1000) 페이지네이션 → funding_history.
  - OI 는 이력 제약(~30일)이라 backfill 대상 아님(forward 수집 collect_oi 가 담당).

실행:
  python -m backtesting.backfill_history [--years 2] [--interval 1h] [--no-supabase]

주의: 공개 market data(read-only, 주문 없음). 멱등(UNIQUE upsert)이라 재실행 안전.
=====================================================================
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    if interval not in _INTERVAL_MS:
        raise ValueError(f"지원하지 않는 interval: {interval}")
    return _INTERVAL_MS[interval]


def _now_ms() -> int:
    return int(time.time() * 1000)


def fetch_klines_history(
    client, symbol: str, interval: str = "1h",
    start_ms: int = 0, end_ms: Optional[int] = None,
    pause: float = 0.15, max_pages: int = 5000,
) -> List[dict]:
    """startTime 순방향 페이지네이션으로 klines 전체를 ohlcv 행 리스트로 반환.

    각 행: {symbol, interval, ts(ISO), open, high, low, close, volume, taker_buy_base}.
    진행 없음/빈 응답 시 종료(무한루프 가드).
    """
    step = interval_ms(interval)
    if end_ms is None:
        end_ms = _now_ms()
    rows: List[dict] = []
    seen: set = set()
    cur = start_ms
    pages = 0
    while cur < end_ms and pages < max_pages:
        raw = client.futures_klines(
            symbol=symbol, interval=interval,
            startTime=cur, endTime=end_ms, limit=1500,
        )
        if not raw:
            break
        for k in raw:
            try:
                ot = int(k[0])
            except (IndexError, ValueError, TypeError):
                continue
            if ot in seen:
                continue
            seen.add(ot)
            ts = datetime.fromtimestamp(ot / 1000.0, tz=timezone.utc)
            rows.append({
                "symbol": symbol, "interval": interval, "ts": ts.isoformat(),
                "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                "close": float(k[4]), "volume": float(k[5]),
                "taker_buy_base": float(k[9]) if len(k) > 9 else None,
            })
        nxt = int(raw[-1][0]) + step
        if nxt <= cur:                 # 진행 없음 → 종료
            break
        cur = nxt
        pages += 1
        if pause:
            time.sleep(pause)
    return rows


def fetch_funding_history(
    client, symbol: str, start_ms: int = 0, end_ms: Optional[int] = None,
    pause: float = 0.15, max_pages: int = 2000,
) -> List[dict]:
    """startTime 페이지네이션으로 펀딩비 이력을 funding_history 행 리스트로 반환."""
    if end_ms is None:
        end_ms = _now_ms()
    rows: List[dict] = []
    seen: set = set()
    cur = start_ms
    pages = 0
    while cur < end_ms and pages < max_pages:
        raw = client.futures_funding_rate(
            symbol=symbol, startTime=cur, endTime=end_ms, limit=1000,
        )
        if not raw:
            break
        for it in raw:
            try:
                ft = int(it["fundingTime"])
            except (KeyError, ValueError, TypeError):
                continue
            if ft in seen:
                continue
            seen.add(ft)
            ts = datetime.fromtimestamp(ft / 1000.0, tz=timezone.utc)
            rows.append({
                "symbol": symbol, "ts": ts.isoformat(),
                "funding_rate": float(it["fundingRate"]),
            })
        nxt = int(raw[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
        pages += 1
        if pause:
            time.sleep(pause)
    return rows


def _push_chunked(persist, table: str, rows: List[dict], chunk: int = 1000) -> int:
    """rows 를 chunk 단위 배치 upsert(멱등). 적재 시도 행수 반환."""
    for i in range(0, len(rows), chunk):
        persist.upsert_many(table, rows[i:i + chunk])
    return len(rows)


def backfill_symbol(
    client, persist, symbol: str, interval: str, start_ms: int,
    push_supabase: bool = True, chunk: int = 1000,
) -> Dict[str, int]:
    """심볼 1종의 klines+funding 을 backfill 하고 Supabase 에 적재. 행수 요약 반환."""
    kl = fetch_klines_history(client, symbol, interval, start_ms)
    fr = fetch_funding_history(client, symbol, start_ms)
    if push_supabase and persist is not None:
        _push_chunked(persist, "ohlcv", kl, chunk)
        _push_chunked(persist, "funding_history", fr, chunk)
    logger.info("[Backfill] %s: ohlcv=%d funding=%d", symbol, len(kl), len(fr))
    return {"ohlcv": len(kl), "funding": len(fr)}


def backfill(
    symbols: Optional[List[str]] = None, years: float = 2.0, interval: str = "1h",
    client=None, persist=None, push_supabase: bool = True,
    include_index: bool = True, chunk: int = 1000,
) -> Dict[str, Dict[str, int]]:
    """유니버스(+BTC/ETH 인덱스) 과거 OHLCV/펀딩 backfill."""
    from backtesting.collect_oi import (
        _DEFAULT_OI_COLLECT_SYMBOLS, _INDEX_SYMBOLS,
        _build_live_client, _build_persistence,
    )
    client = client or _build_live_client()
    if push_supabase and persist is None:
        persist = _build_persistence()

    syms = list(symbols or _DEFAULT_OI_COLLECT_SYMBOLS)
    if include_index:
        syms += [s for s in _INDEX_SYMBOLS if s not in syms]

    start_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=int(years * 365))).timestamp() * 1000
    )
    out: Dict[str, Dict[str, int]] = {}
    for s in syms:
        out[s] = backfill_symbol(client, persist, s, interval, start_ms,
                                 push_supabase=push_supabase, chunk=chunk)
    if push_supabase and persist is not None:
        try:
            persist.flush_outbox()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Backfill] outbox flush 실패(무시): %s", e)
    return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("[Backfill] python-dotenv 미설치 — 환경변수 직접 export 필요")

    ap = argparse.ArgumentParser(description="과거 OHLCV/펀딩 backfill (Supabase)")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--symbols", default="", help="콤마구분(미지정 시 기본 유니버스+인덱스)")
    ap.add_argument("--no-supabase", action="store_true")
    args = ap.parse_args()

    symbols = [s for s in args.symbols.replace(" ", "").split(",") if s] or None
    summary = backfill(
        symbols=symbols, years=args.years, interval=args.interval,
        push_supabase=not args.no_supabase,
    )
    total_ohlcv = sum(v["ohlcv"] for v in summary.values())
    total_funding = sum(v["funding"] for v in summary.values())
    print(f"Backfill done: {len(summary)} symbols, "
          f"ohlcv={total_ohlcv}, funding={total_funding}")


if __name__ == "__main__":
    main()
