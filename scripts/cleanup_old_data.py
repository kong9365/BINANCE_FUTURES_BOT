"""
scripts/cleanup_old_data.py
=====================================================================
Microstructure raw 데이터 TTL DELETE + VACUUM (Phase 2-E disk mitigation).

Supabase 무료 티어 2GB 디스크 보호 — raw `agg_trades` (1h TTL) +
`l2_book_snapshots` (6h TTL) 만 단기 보존, 1m aggregate 는 영구 보존.

사용:
  python scripts/cleanup_old_data.py
  python scripts/cleanup_old_data.py --agg-hours 1 --l2-hours 6
  python scripts/cleanup_old_data.py --dry-run

Windows Task 등록 (BinanceWSCleanup, 매시간):
  $action = New-ScheduledTaskAction -Execute "C:\bots\BINANCE_FUTURES_BOT\run_cleanup.bat"
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
  ...

알파/ML/R0/진입 로직 *없음*. 데이터 보존 정책 집행만.
=====================================================================
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("cleanup")


def _build_client():
    """Inlined Supabase client (운영 클론은 analytics 미동기화)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:  # noqa: BLE001
        logger.error("supabase client 생성 실패: %s", e)
        return None


def _delete_older_than(client, table: str, hours: int, dry_run: bool) -> int:
    """ts < (now - hours) row 삭제. dry_run 이면 count만."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    # 1. count 먼저
    try:
        cnt = (
            client.table(table)
            .select("*", count="exact", head=True)
            .lt("ts", cutoff)
            .execute()
        )
        n = getattr(cnt, "count", None) or 0
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] count 실패: %s", table, e)
        return 0
    if n == 0:
        logger.info("[%s] cutoff=%s — 삭제 대상 0", table, cutoff)
        return 0
    logger.info("[%s] cutoff=%s — 삭제 대상 %d rows", table, cutoff, n)
    if dry_run:
        return n
    # 2. DELETE (PostgREST 는 큰 DELETE 가 timeout 위험 — 청크 권장)
    # Supabase REST DELETE 는 batch 매우 안 좋아서, 큰 cleanup 은 SQL editor 권장.
    # 여기선 timeout 위험 감수 후 한번에. 실패 시 운영자가 SQL editor 직접.
    try:
        res = client.table(table).delete().lt("ts", cutoff).execute()
        deleted = len(res.data or [])
        logger.info("[%s] DELETE 완료 (응답 row=%d)", table, deleted)
        return n
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] DELETE 실패: %s", table, e)
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg-hours", type=int, default=1,
                    help="agg_trades raw TTL (default 1h)")
    ap.add_argument("--l2-hours", type=int, default=6,
                    help="l2_book_snapshots raw TTL (default 6h)")
    ap.add_argument("--dry-run", action="store_true",
                    help="삭제 안 함, count 만")
    args = ap.parse_args()

    client = _build_client()
    if client is None:
        logger.error("Supabase client 미설정")
        return 2

    logger.info("=== Microstructure Raw Cleanup (dry_run=%s) ===", args.dry_run)
    total_deleted = 0
    total_deleted += _delete_older_than(client, "agg_trades", args.agg_hours, args.dry_run)
    total_deleted += _delete_older_than(client, "l2_book_snapshots", args.l2_hours,
                                          args.dry_run)
    logger.info("=== Total rows %s: %d ===", "to-delete" if args.dry_run else "deleted",
                total_deleted)
    # VACUUM: PostgREST 미지원 (raw SQL 만) — 운영자가 별도로 (필요 시)
    logger.info("Note: VACUUM FULL 은 PostgREST 로 안 됨. 디스크 회수 시 Supabase SQL editor "
                "에서 'VACUUM FULL agg_trades; VACUUM FULL l2_book_snapshots;' 직접 실행.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
