"""OHLCV backfill outbox → Supabase 재전송 (PGRST002/503 복구 후 실행)."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("flush_ohlcv_outbox")


def main() -> None:
    import os
    if os.environ.get("VERIFICATION_LOCAL_ONLY", "").lower() in ("1", "true", "yes"):
        print("VERIFICATION_LOCAL_ONLY=true — outbox flush skipped (local-only mode)")
        return
    from backtesting.backfill_history import _build_ohlcv_persistence

    persist = _build_ohlcv_persistence()
    pending = persist.pending_count()
    logger.info("outbox pending=%d", pending)
    if pending == 0:
        print("outbox empty")
        return

    total = 0
    rounds = 0
    while persist.pending_count() > 0 and rounds < 500:
        sent = persist.flush_outbox(limit=50)
        total += sent
        rounds += 1
        if sent == 0:
            logger.info("flush stalled — Supabase 아직 불안정, 30s 후 재시도")
            time.sleep(30)
        else:
            logger.info("sent=%d remaining=%d", sent, persist.pending_count())
    print(f"flush done: sent={total} remaining={persist.pending_count()}")


if __name__ == "__main__":
    main()
