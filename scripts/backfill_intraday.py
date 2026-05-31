"""
scripts/backfill_intraday.py
=====================================================================
SMC 인트라데이 백필 — perp 15m OHLCV (공개 futures_klines, 키 없음·계좌/주문 0).

★ 공개 시장데이터만. `Client()` 키 없이 생성. spot/잔고/계좌 API·주문 0.
  저장: backtests/cache/intraday/{sym}_{interval}.parquet (커밋 금지 경로 → 미커밋).
  멱등: 기존 parquet 은 스킵(--force 로 덮어쓰기) → 타임아웃 시 재실행으로 이어받기.

대상: 기존 perp 1d 유니버스 중 *이력 ≥ --min-1d-bars 일* (신규 thin 상장 제외 = mid-liq 후보).
사용: python scripts/backfill_intraday.py [--interval 15m] [--start 2022-01-01]
        [--min-1d-bars 500] [--max-syms 60] [--force]
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtesting.backfill_history import fetch_klines_history  # noqa: E402

INTRA_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "intraday"
PERP_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "verification"
_IV_SEC = {"5m": 300, "15m": 900, "1h": 3600}


def _ms(dt: str) -> int:
    return int(datetime.strptime(dt, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _established(min_1d_bars: int):
    """1d 이력 ≥ min_1d_bars 인 perp 심볼(신규 thin 상장 제외)."""
    out = []
    for p in sorted(glob.glob(str(PERP_DIR / "*_1d.parquet"))):
        sym = os.path.basename(p).replace("_1d.parquet", "")
        try:
            n = len(pd.read_parquet(p, columns=["ts"]))
        except Exception:  # noqa: BLE001
            continue
        if n >= min_1d_bars:
            out.append(sym)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="15m", choices=list(_IV_SEC))
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--min-1d-bars", type=int, default=500)
    ap.add_argument("--max-syms", type=int, default=60)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from binance.client import Client  # 공개 엔드포인트 (키 없음)
    client = Client()
    INTRA_DIR.mkdir(parents=True, exist_ok=True)
    syms = _established(args.min_1d_bars)[:args.max_syms]
    start_ms = _ms(args.start)
    sec = _IV_SEC[args.interval]

    print(f"인트라데이 백필 {args.interval} | 대상 {len(syms)}종목(1d≥{args.min_1d_bars}일) | {args.start}~ | 공개·계좌0")
    done, skipped = 0, 0
    for sym in syms:
        out = INTRA_DIR / f"{sym}_{args.interval}.parquet"
        if out.exists() and not args.force:
            skipped += 1
            print(f"  {sym}: skip (존재)")
            continue
        try:
            rows = fetch_klines_history(client, sym, args.interval, start_ms)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: 실패 {type(e).__name__} {str(e)[:70]}")
            continue
        if not rows:
            print(f"  {sym}: 데이터 없음")
            continue
        df = pd.DataFrame(rows)
        df.to_parquet(out, index=False)
        # 품질: 기대봉수 대비 결손율
        t = pd.to_datetime(df["ts"], format="ISO8601")
        span = (t.iloc[-1] - t.iloc[0]).total_seconds()
        expected = span / sec + 1
        miss = max(0.0, 100.0 * (1 - len(df) / expected)) if expected > 0 else 0.0
        done += 1
        print(f"  {sym}: {len(df):,}봉 {t.iloc[0].date()}~{t.iloc[-1].date()} 결손 {miss:.2f}%")
        time.sleep(0.2)
    print(f"\n완료: 신규 {done} / 스킵 {skipped} / 대상 {len(syms)}. 저장 → {INTRA_DIR}")


if __name__ == "__main__":
    main()
