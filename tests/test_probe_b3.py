"""
tests/test_probe_b3.py
=====================================================================
B-3 forward-return 계측 코어 테스트 (순수 directional + DB backfill/report).

네트워크 격리: fetch_bars 주입(합성 bars). directional 방향/룩어헤드/결측,
backfill 멱등, report 의 미체결 vs 체결 비교(B-3 효과 Δ) 검증.
"""

from __future__ import annotations

import sqlite3

import pytest

from analytics.probe_b3 import b3_report, backfill_unfilled, directional_fwd_returns
from db.init_db import init_db

# 상승 bars: open_time_ms, close
_UP = [(1000, 100), (2000, 110), (3000, 121), (4000, 130), (5000, 140), (6000, 150), (7000, 160)]


def _fetch(bars):
    return lambda symbol, start_ms: bars


# ── directional_fwd_returns (순수) ──
def test_directional_long_and_short():
    # signal@1500 → base = 첫 open>1500 = 2000(idx1). +1=110(+10%) +3=130(+30%) +6=160(+60%)
    lo = directional_fwd_returns(_UP, 1500, 100.0, "LONG")
    assert lo[1] == pytest.approx(0.10) and lo[3] == pytest.approx(0.30) and lo[6] == pytest.approx(0.60)
    sh = directional_fwd_returns(_UP, 1500, 100.0, "SHORT")
    assert sh[1] == pytest.approx(-0.10) and sh[6] == pytest.approx(-0.60)


def test_directional_base_strictly_after_signal_no_lookahead():
    # signal 이 봉 open(2000)과 동일 → base = open>2000 인 첫 봉 = 3000(idx2, close121)
    r = directional_fwd_returns(_UP, 2000, 121.0, "LONG")   # +1 = 121 → 0%
    assert r[1] == pytest.approx(0.0)


def test_directional_missing_and_guards():
    r = directional_fwd_returns(_UP, 6500, 100.0, "LONG")   # base=idx6(7000), +3/+6 없음
    assert r[1] is not None and r[3] is None and r[6] is None
    assert directional_fwd_returns([], 1000, 100.0, "LONG")[1] is None
    assert directional_fwd_returns(_UP, 1500, 0.0, "LONG")[1] is None       # 가격 0 → None
    assert directional_fwd_returns(_UP, 9999, 100.0, "LONG")[1] is None     # 신호 이후 봉 없음


# ── backfill_unfilled (DB, 멱등) ──
def test_backfill_unfilled_fills_and_idempotent(tmp_path):
    db = str(tmp_path / "b.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO unfilled_signals (ts, symbol, action, setup_tag, signal_price, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("1970-01-01T00:00:01.500000+00:00", "SOLUSDT", "LONG", "oi_surge_long", 100.0, "fill_timeout"),
    )
    conn.commit(); conn.close()

    assert backfill_unfilled(db, _fetch(_UP)) == 1      # 1행 채움
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM unfilled_signals").fetchone()
    assert r["fwd_return_1bar"] == pytest.approx(0.10)
    assert r["fwd_return_6bar"] == pytest.approx(0.60)
    assert r["fwd_filled_at"] is not None
    conn.close()

    assert backfill_unfilled(db, _fetch(_UP)) == 0      # 멱등 — NULL 없으니 0행


# ── b3_report (미체결 vs 체결 비교) ──
def test_b3_report_unfilled_beats_filled(tmp_path):
    db = str(tmp_path / "b.db")
    init_db(db)
    conn = sqlite3.connect(db)
    # 미체결(달아난 승자): fwd 저장 +10/+30/+60%
    conn.execute(
        "INSERT INTO unfilled_signals (ts, symbol, action, setup_tag, signal_price, reason, "
        "fwd_return_1bar, fwd_return_3bar, fwd_return_6bar, fwd_filled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-06-01T00:00:00+00:00", "SOLUSDT", "LONG", "oi_surge_long", 100.0,
         "fill_timeout", 0.10, 0.30, 0.60, "2026-06-01"),
    )
    # 체결(되돌아온 패자): entry_limit_price=100, 체결가=100.05(슬리피지 0.05)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, action, entry_price, quantity, setup_tag, "
        "entry_limit_price, trade_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("1970-01-01T00:00:01.500000+00:00", "SOLUSDT", "LONG", 100.05, 1.0, "oi_surge_long",
         100.0, "OPEN"),
    )
    conn.commit(); conn.close()

    loser = [(1000, 100), (2000, 98), (3000, 97), (4000, 96), (5000, 95), (6000, 94), (7000, 93)]
    rep = b3_report(db, _fetch(loser))
    assert rep["n_unfilled"] == 1 and rep["n_filled"] == 1
    assert rep["fill_rate"] == pytest.approx(0.5)
    assert rep["unfilled_fwd"][1] == pytest.approx(0.10)
    assert rep["filled_fwd"][1] == pytest.approx(-0.02)        # base=98 from 100
    assert rep["b3_effect"][1] == pytest.approx(0.12)          # 미체결 > 체결 = 역선택
    assert rep["mean_slippage"] == pytest.approx(0.05)         # 100.05 - 100.0
