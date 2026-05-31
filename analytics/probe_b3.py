"""
analytics/probe_b3.py
=====================================================================
B-3(post_only 15초 역선택) 오프라인 계측 코어 — **비매매, 읽기/계산 + fwd 컬럼만 UPDATE**.

측정 원리: post_only 미체결(=가격이 limit 에서 달아난 *승자*) vs 체결(=가격이
limit 로 되돌아온 *반전 패자*)의 **동일 기준가(요청 limit) 대비 방향 forward-return**
비교. 미체결 fwd > 체결 fwd 면 역선택(B-3) 확정(메커니즘 서명 — 소표본으로도 증명).

순수 함수(directional_fwd_returns) + DB IO(backfill/report). OHLCV fetch 는 주입(fetch_bars)
→ 네트워크 없이 테스트 가능. forward-return 패턴은 lcr_event_labeler(i+1 진입→i+h)와 동일,
단 *방향성*(LONG +/ SHORT −) + 신호 timestamp 기준.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

HORIZONS = (1, 3, 6)   # unfilled_signals.fwd_return_1/3/6bar 와 일치


def _iso_to_ms(ts: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _mean(xs):
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def directional_fwd_returns(bars, signal_ts_ms, signal_price, action, horizons=HORIZONS):
    """신호 이후 방향 forward-return. bars=[(open_time_ms, close)] 오름차순.

    base = open_time > signal_ts 인 첫 봉(신호 다음 봉, 룩어헤드 0). +h bar = base+h-1 봉 종가.
    LONG: (close_h - signal_price)/signal_price, SHORT: 부호 반전. 데이터 부족 horizon → None.
    """
    out = {h: None for h in horizons}
    if signal_price is None or signal_price <= 0 or signal_ts_ms is None or not bars:
        return out
    base = None
    for i, (ot, _c) in enumerate(bars):
        if ot > signal_ts_ms:
            base = i
            break
    if base is None:
        return out
    sign = 1.0 if action == "LONG" else -1.0
    for h in horizons:
        j = base + h - 1
        if 0 <= j < len(bars):
            close_h = bars[j][1]
            out[h] = sign * (close_h - signal_price) / signal_price
    return out


def backfill_unfilled(db_path, fetch_bars: Callable, horizons=HORIZONS) -> int:
    """unfilled_signals 의 NULL forward-return 을 채운다(멱등 — NULL 만). 채운 행수 반환.

    fetch_bars(symbol, start_ms) -> [(open_time_ms, close)] (주입 — 네트워크 격리).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    updated = 0
    try:
        rows = conn.execute(
            "SELECT id, ts, symbol, action, signal_price FROM unfilled_signals "
            "WHERE fwd_return_1bar IS NULL AND signal_price IS NOT NULL"
        ).fetchall()
        for r in rows:
            sig_ms = _iso_to_ms(r["ts"])
            if sig_ms is None:
                continue
            bars = fetch_bars(r["symbol"], sig_ms)
            fwd = directional_fwd_returns(bars, sig_ms, r["signal_price"], r["action"], horizons)
            if fwd.get(1) is None:        # 첫 horizon 도 못 채우면 데이터 부족 → skip(다음 실행 재시도)
                continue
            conn.execute(
                "UPDATE unfilled_signals SET fwd_return_1bar=?, fwd_return_3bar=?, "
                "fwd_return_6bar=?, fwd_filled_at=? WHERE id=?",
                (fwd.get(1), fwd.get(3), fwd.get(6),
                 datetime.now(timezone.utc).isoformat(), r["id"]),
            )
            updated += 1
        conn.commit()
    finally:
        conn.close()
    return updated


def b3_report(db_path, fetch_bars: Callable, setup_prefix="oi_surge",
              horizons=HORIZONS) -> dict:
    """미체결(저장된 fwd) vs 체결(요청 limit 기준 재계산) 방향 forward-return 비교.

    B-3 효과 = mean(미체결 fwd) − mean(체결 fwd) (horizon별). 양(+)이면 역선택 확정
    (미체결=달아난 승자 > 체결=되돌아온 패자). + 체결률·fill 슬리피지.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        unf = conn.execute(
            "SELECT fwd_return_1bar, fwd_return_3bar, fwd_return_6bar FROM unfilled_signals "
            "WHERE setup_tag LIKE ? AND fwd_return_1bar IS NOT NULL", (setup_prefix + "%",),
        ).fetchall()
        trades = conn.execute(
            "SELECT timestamp, symbol, action, entry_limit_price, entry_price FROM trades "
            "WHERE setup_tag LIKE ? AND entry_limit_price IS NOT NULL", (setup_prefix + "%",),
        ).fetchall()
    finally:
        conn.close()

    unf_mean = {h: _mean([r[f"fwd_return_{h}bar"] for r in unf]) for h in horizons}
    filled = {h: [] for h in horizons}
    slippage = []
    for t in trades:
        sig_ms = _iso_to_ms(t["timestamp"])
        fwd = directional_fwd_returns(
            fetch_bars(t["symbol"], sig_ms), sig_ms, t["entry_limit_price"], t["action"], horizons)
        for h in horizons:
            if fwd.get(h) is not None:
                filled[h].append(fwd[h])
        if t["entry_price"] and t["entry_limit_price"]:
            slippage.append(float(t["entry_price"]) - float(t["entry_limit_price"]))
    fil_mean = {h: _mean(filled[h]) for h in horizons}

    n_unf, n_fil = len(unf), len(trades)
    total = n_unf + n_fil
    b3 = {
        h: (unf_mean[h] - fil_mean[h]) if (unf_mean[h] is not None and fil_mean[h] is not None)
        else None
        for h in horizons
    }
    return {
        "n_unfilled": n_unf, "n_filled": n_fil,
        "fill_rate": (n_fil / total) if total else None,
        "unfilled_fwd": unf_mean, "filled_fwd": fil_mean,
        "b3_effect": b3, "mean_slippage": _mean(slippage),
    }
