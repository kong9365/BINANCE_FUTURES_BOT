"""
scripts/ws_health_report.py
=====================================================================
Microstructure WS daemon 24h heartbeat / 품질 점검 리포트 (운영자 9-지표).

사용:
  # 24h 리포트 (default)
  python scripts/ws_health_report.py

  # 7일 누적 점검
  python scripts/ws_health_report.py --window-hours 168

  # Telegram 발송 (운영 daemon 등록 시 권장)
  python scripts/ws_health_report.py --telegram

운영자 명시 9 지표:
  1. stream_count          (universe snapshot 최근 적재 기준 추정)
  2. l2_book_snapshots_24h
  3. agg_trades_24h (or raw_trades_24h, source_stream 별 분리)
  4. liquidations_24h
  5. avg_latency_ms / p95_latency_ms (테이블별)
  6. reconnect_count       (collector 메모리 — 별도 모니터링; SQL 미존재)
  7. queue_overflow_count  (동일)
  8. last_message_age_sec  (테이블별 MAX(received_ts) 기준)
  9. collection_universe_snapshot_count

알파/ML/R0/진입 로직 *없음* — Supabase read-only.
=====================================================================
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ws_health")


def _build_client():
    """Inlined Supabase client (운영 클론은 analytics 모듈 미동기화)."""
    import os
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:  # noqa: BLE001
        return None


def _fetch_table_stats(client, table: str, window_hours: int) -> dict:
    """단일 테이블 row count 조회 (PostgREST는 ISO 시각 필터만 — 'now()-interval' 직접 안 됨)."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    res = (
        client.table(table)
        .select("*", count="exact", head=True)
        .gt("ts", cutoff)
        .execute()
    )
    n = getattr(res, "count", None) or 0
    return {"rows": int(n)}


def _telegram_send(text: str) -> bool:
    """Telegram heartbeat 발송 (CRITICAL/INFO 패턴 — main_7590 와 동일 toggle)."""
    import os
    import urllib.request
    import urllib.parse

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=int, default=24,
                    help="조회 윈도(시간, default 24)")
    ap.add_argument("--telegram", action="store_true",
                    help="Telegram heartbeat 발송")
    args = ap.parse_args()

    client = _build_client()
    if client is None:
        print("ERROR: Supabase client unavailable (SUPABASE_URL/KEY 확인)")
        return 2

    h = args.window_hours
    # 1~4: row counts + Phase 2-E agg_1m
    l2 = _fetch_table_stats(client, "l2_book_snapshots", h)
    agg = _fetch_table_stats(client, "agg_trades", h)
    agg_1m = _fetch_table_stats(client, "agg_trades_1m", h)
    liq = _fetch_table_stats(client, "liquidations", h)
    snap = _fetch_table_stats(client, "collection_universe_snapshots", h)

    # 5/8: latency + last_message_age (단일 row 조회 — 최신값)
    # Supabase REST 는 aggregate 직접 어려움 — order/limit 로 단일 row 만 가져오는 패턴.
    def _last_msg(table: str):
        try:
            res = (
                client.table(table)
                .select("received_ts,latency_ms")
                .order("received_ts", desc=True)
                .limit(1)
                .execute()
            )
            data = res.data or []
            return data[0] if data else None
        except Exception:  # noqa: BLE001
            return None

    last_l2 = _last_msg("l2_book_snapshots")
    last_agg = _last_msg("agg_trades")
    last_liq = _last_msg("liquidations")

    # stream_count: 가장 최근 snapshot 적재 기준
    stream_count = "N/A"
    try:
        res = (
            client.table("collection_universe_snapshots")
            .select("ts", count="exact", head=False)
            .order("ts", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            last_ts = res.data[0]["ts"]
            r2 = (
                client.table("collection_universe_snapshots")
                .select("stream_types")
                .eq("ts", last_ts)
                .execute()
            )
            if r2.data:
                total = 0
                has_force = False
                for r in r2.data:
                    st = r.get("stream_types", [])
                    for s in st:
                        if s == "forceOrder":
                            has_force = True
                        else:
                            total += 1
                if has_force:
                    total += 1   # forceOrder 단일 multiplex stream
                stream_count = total
    except Exception:  # noqa: BLE001
        pass

    # 출력
    lines = []
    lines.append(f"== Microstructure WS Health ({h}h) ==")
    lines.append(f"1. stream_count             : {stream_count}")
    lines.append(f"2. l2_book_snapshots_{h}h    : {l2['rows']:>10,}")
    lines.append(f"3. agg_trades_{h}h (raw)     : {agg['rows']:>10,}   (TTL 1h)")
    lines.append(f"3b. agg_trades_1m_{h}h        : {agg_1m['rows']:>10,}   (영구 보존)")
    lines.append(f"4. liquidations_{h}h         : {liq['rows']:>10,}")
    lines.append(f"5. avg/p95 latency          : (collector log 참조 — runtime memory)")
    lines.append(f"6. reconnect_count          : (collector log 참조)")
    lines.append(f"7. queue_overflow_count     : (collector log 참조)")
    if last_l2:
        lines.append(f"8a. last_l2_msg_at          : {last_l2.get('received_ts')}")
        lines.append(f"    last_l2_latency_ms      : {last_l2.get('latency_ms')}")
    if last_agg:
        lines.append(f"8b. last_agg_msg_at         : {last_agg.get('received_ts')}")
        lines.append(f"    last_agg_latency_ms     : {last_agg.get('latency_ms')}")
    if last_liq:
        lines.append(f"8c. last_liq_msg_at         : {last_liq.get('received_ts')}")
    lines.append(f"9. collection_universe_snap : {snap['rows']:>10,}")

    text = "\n".join(lines)
    print(text)

    if args.telegram:
        ok = _telegram_send("```\n" + text + "\n```")
        print(f"\n[Telegram] {'sent' if ok else 'failed'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
