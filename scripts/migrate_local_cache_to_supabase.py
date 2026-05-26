#!/usr/bin/env python3
"""로컬 backfill CSV → Supabase ohlcv/funding_history upsert (Plan C)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_cache")

CACHE = PROJECT_ROOT / "backtests" / "cache" / "verification"
CHUNK = 1000


def _rows_from_csv(path: Path) -> list:
    import pandas as pd
    df = pd.read_csv(path)
    return df.to_dict("records")


def main() -> None:
    import os
    if os.environ.get("VERIFICATION_LOCAL_ONLY", "").lower() in ("1", "true", "yes"):
        print("VERIFICATION_LOCAL_ONLY=true — Supabase migrate skipped (local-only mode)")
        return
    from backtesting.backfill_history import _build_ohlcv_persistence, _push_chunked

    if not CACHE.is_dir():
        print(f"캐시 없음: {CACHE}")
        sys.exit(1)
    persist = _build_ohlcv_persistence()
    if persist.client is None:
        print("Supabase 클라이언트 없음 — SUPABASE_OHLCV_* 확인")
        sys.exit(1)
    # alive probe
    try:
        persist.client.table("ohlcv").select("symbol").limit(1).execute()
    except Exception as e:  # noqa: BLE001
        print(f"Supabase ohlcv 접근 실패 (DDL 먼저?): {type(e).__name__}")
        sys.exit(1)

    ohlcv_total = 0
    fund_total = 0
    for path in sorted(CACHE.glob("*_1d.csv")):
        sym = path.name.replace("_1d.csv", "")
        rows = _rows_from_csv(path)
        if rows:
            _push_chunked(persist, "ohlcv", rows, CHUNK)
            ohlcv_total += len(rows)
            logger.info("%s ohlcv=%d", sym, len(rows))
    for path in sorted(CACHE.glob("*_funding.csv")):
        sym = path.name.replace("_funding.csv", "")
        rows = _rows_from_csv(path)
        if rows:
            _push_chunked(persist, "funding_history", rows, CHUNK)
            fund_total += len(rows)
            logger.info("%s funding=%d", sym, len(rows))
    persist.flush_outbox()
    print(f"done: ohlcv={ohlcv_total} funding={fund_total}")


if __name__ == "__main__":
    main()
