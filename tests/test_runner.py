"""
tests/test_runner.py
=====================================================================
monitoring/runner.py 단위테스트 — scan_once(보호종목 제외·STRONG알림/WEAK로그·캡·추적)
+ ★ 읽기전용(executor 부재)·키없음(Client())·실주문 0 강제.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

from config.settings import MONITORING_CONFIG
from monitoring import paper_log, runner
from monitoring.observer import Bar
from monitoring.runner import scan_once, track_open


def _bars(trend="up", vol_mult=3.0, taker=0.70, start="2026-01-01T00:00:00"):
    t0 = datetime.fromisoformat(start)
    bars = []
    for i in range(220):
        c = (100.0 + i * 0.5) if trend == "up" else (100.0 + (0.5 if i % 2 else -0.5))
        ts = (t0 + timedelta(minutes=15 * i)).isoformat()
        bars.append(Bar(c, c + 0.3, c - 0.3, c, 1000.0, 500.0, ts))
    bars[-1].volume = 1000.0 * vol_mult
    bars[-1].taker_buy = bars[-1].volume * taker
    return bars


def test_scan_protected_excluded_strong_alert_weak_log(tmp_path):
    db = str(tmp_path / "p.db"); paper_log.init_db(db)
    oi = (110.0, 100.0)                                       # +10% (강)
    bars_by = {
        "ADAUSDT": _bars("up", 3.0, 0.70),                   # STRONG
        "XRPUSDT": _bars("up", 1.7, 0.57),                   # WEAK
        "BTCUSDT": _bars("up", 3.0, 0.70),                   # STRONG 이나 보호종목 → 제외
    }
    oi_by = {"ADAUSDT": oi, "XRPUSDT": None, "BTCUSDT": oi}
    now = bars_by["ADAUSDT"][-1].ts                          # 신호=마지막 마감봉 → 갓 진입 추적 X
    alerts = scan_once(bars_by, oi_by, db, now, {}, MONITORING_CONFIG)
    assert len(alerts) == 1 and "ADAUSDT" in alerts[0]       # STRONG 1건만 알림
    grades = {r["symbol"]: r["alert_grade"] for r in paper_log.open_positions(db)}
    assert grades == {"ADAUSDT": "STRONG", "XRPUSDT": "WEAK"}   # ★ BTC(보호) 미기록


def test_alert_daily_cap_top_two(tmp_path):
    db = str(tmp_path / "c.db"); paper_log.init_db(db)
    oi = (110.0, 100.0)
    bars_by = {"AAAUSDT": _bars("up", 3.0, 0.70), "BBBUSDT": _bars("up", 2.8, 0.68),
               "CCCUSDT": _bars("up", 2.6, 0.66)}            # 모두 STRONG, 점수차
    oi_by = {k: oi for k in bars_by}
    now = bars_by["AAAUSDT"][-1].ts
    alerts = scan_once(bars_by, oi_by, db, now, {}, MONITORING_CONFIG)
    assert len(alerts) == 2                                  # 일일캡 2 → 점수 상위 2개만


def test_track_open_closes_on_tp(tmp_path):
    db = str(tmp_path / "t.db"); paper_log.init_db(db)
    paper_log.record_signal(db, "2026-01-01T00:00:00", "ADAUSDT", "LONG", "STRONG", 100.0, 1.0)
    bars = [Bar(100, 101, 99.5, 100.5, 1, 1, "2026-01-01T00:15:00"),
            Bar(100.5, 103.5, 100, 103, 1, 1, "2026-01-01T00:30:00")]   # high 103.5>=TP103
    assert track_open({"ADAUSDT": bars}, db, MONITORING_CONFIG) == 1
    assert not paper_log.has_open(db, "ADAUSDT")             # TP 가상청산


def test_runner_read_only_keyless_no_executor():
    """★ executor 미import·미호출, 실주문 0, 공개 keyless Client()."""
    src = inspect.getsource(runner)
    low = src.lower()
    assert "trading.executor" not in low and "import executor" not in low   # executor 미import
    assert "create_order" not in low and "place_order" not in low and "futures_create" not in low
    assert "Client()" in src                                # 키 없는 공개 클라이언트
