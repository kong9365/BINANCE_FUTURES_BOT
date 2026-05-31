"""
scripts/run_b3_measurement.py
=====================================================================
Track A — B-3 계측 실행: DB 이벤트(체결 trades + 미체결 unfilled_signals) →
공개 aggTrades 로 forward-return(+15s/+30s/+60s/+5m/+15m) → filled vs unfilled 통계검정.

★ 공개 시장데이터만(키 없는 futures_agg_trades, read-only). 거래/계좌/주문 0. 오프라인.
  실제 측정은 운영자가 테스트넷/소액 라이브로 post_only 이벤트를 누적한 뒤 실행.
  이벤트 0 이면 INSUFFICIENT_SAMPLE(배관만 검증).

사용: python scripts/run_b3_measurement.py [--db data/bot.db] [--setup oi_surge]
        [--out docs/measurement_report.json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analytics.unfilled_signal_analyzer import HORIZONS_SEC, measure  # noqa: E402


def _iso_ms(ts):
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def load_events(db_path, setup_prefix="oi_surge"):
    """DB → events [{symbol, side, order_time_ms, ref_price, filled}]. 미체결+체결(oi_surge)."""
    events = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for r in conn.execute(
            "SELECT COALESCE(order_send_ts, ts) AS ot, symbol, action, signal_price "
            "FROM unfilled_signals WHERE setup_tag LIKE ?", (setup_prefix + "%",),
        ):
            ot = _iso_ms(r["ot"])
            if ot and r["signal_price"]:
                events.append({"symbol": r["symbol"], "side": r["action"],
                               "order_time_ms": ot, "ref_price": r["signal_price"], "filled": False})
        for r in conn.execute(
            "SELECT COALESCE(entry_order_send_ts, timestamp) AS ot, symbol, action, entry_limit_price "
            "FROM trades WHERE setup_tag LIKE ? AND entry_limit_price IS NOT NULL", (setup_prefix + "%",),
        ):
            ot = _iso_ms(r["ot"])
            if ot and r["entry_limit_price"]:
                events.append({"symbol": r["symbol"], "side": r["action"],
                               "order_time_ms": ot, "ref_price": r["entry_limit_price"], "filled": True})
    finally:
        conn.close()
    return events


def _make_fetch_prices(client):
    def fetch(symbol, start_ms, end_ms):
        out, cur, pages = [], start_ms, 0
        while cur < end_ms and pages < 20:
            try:
                raw = client.futures_agg_trades(
                    symbol=symbol, startTime=cur, endTime=end_ms, limit=1000)
            except Exception:  # noqa: BLE001
                break
            if not raw:
                break
            for t in raw:
                out.append((int(t["T"]), float(t["p"])))
            nxt = int(raw[-1]["T"]) + 1
            if nxt <= cur:
                break
            cur, pages = nxt, pages + 1
        return out
    return fetch


def _pct(x):
    return "n/a" if x is None else f"{x*100:+.4f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--setup", default="oi_surge")
    ap.add_argument("--out", default=str(Path(PROJECT_ROOT) / "docs" / "measurement_report.json"))
    args = ap.parse_args()

    events = load_events(args.db, args.setup)
    n_f = sum(1 for e in events if e["filled"])
    n_u = len(events) - n_f
    print(f"[B-3] 이벤트 로드: 체결 {n_f} / 미체결 {n_u} (setup={args.setup})")

    if not events:
        print("이벤트 0 — INSUFFICIENT_SAMPLE (운영자 테스트넷/라이브 누적 후 재실행).")
        payload = {"verdict": "INSUFFICIENT_SAMPLE", "n_filled": 0, "n_unfilled": 0, "horizons": {}}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"결과 저장 → {args.out}")
        return

    from binance.client import Client   # 공개 엔드포인트(키 없음)
    hr, verdict = measure(events, _make_fetch_prices(Client()))

    print(f"\n{'horizon':>8}{'n_f':>5}{'n_u':>5}{'avg_filled':>12}{'avg_unfilled':>14}{'diff(u-f)':>12}{'p':>8}")
    for h in HORIZONS_SEC:
        r = hr[h]
        p = "n/a" if r["p_value"] is None else f"{r['p_value']:.3f}"
        print(f"{f'+{h}s':>8}{r['n_filled']:>5}{r['n_unfilled']:>5}{_pct(r['avg_filled']):>12}"
              f"{_pct(r['avg_unfilled']):>14}{_pct(r['difference']):>12}{p:>8}")
    print(f"\n판정: {verdict}  (PASS=미체결>체결 유의 / FAIL=무 / INSUFFICIENT=표본부족)")

    payload = {"verdict": verdict, "n_filled": n_f, "n_unfilled": n_u,
               "horizons": {str(h): hr[h] for h in HORIZONS_SEC}}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"결과 저장 → {args.out}")


if __name__ == "__main__":
    main()
