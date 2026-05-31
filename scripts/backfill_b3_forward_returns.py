"""
scripts/backfill_b3_forward_returns.py
=====================================================================
unfilled_signals 의 forward-return(+1/+3/+6 bar) 을 채운다 — B-3 계측.

★ 공개 시장데이터만(키 없는 futures_klines, read-only). 거래/계좌/주문 0. 오프라인.
  멱등(fwd_return_1bar NULL 인 행만). 운영자가 프로브 데이터 누적 후 실행.

사용: python scripts/backfill_b3_forward_returns.py [--db data/bot.db] [--interval 15m]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analytics.probe_b3 import backfill_unfilled  # noqa: E402


def _make_fetch_bars(client, interval: str):
    def fetch(symbol, start_ms):
        try:
            raw = client.futures_klines(
                symbol=symbol, interval=interval, startTime=start_ms, limit=10)
        except Exception:  # noqa: BLE001 — 심볼별 실패는 빈 결과(해당 행 skip)
            return []
        return [(int(k[0]), float(k[4])) for k in raw]   # (open_time_ms, close)
    return fetch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--interval", default="15m")
    args = ap.parse_args()
    from binance.client import Client   # 공개 엔드포인트(키 없음)
    client = Client()
    n = backfill_unfilled(args.db, _make_fetch_bars(client, args.interval))
    print(f"[B-3 backfill] unfilled_signals forward-return 채움: {n}행 (interval={args.interval})")


if __name__ == "__main__":
    main()
