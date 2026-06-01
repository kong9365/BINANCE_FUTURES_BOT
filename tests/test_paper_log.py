"""
tests/test_paper_log.py
=====================================================================
monitoring/paper_log.py 단위테스트 — 손익구조·가상청산·net_R 비용일관·기록/집계.
"""

from __future__ import annotations

from config.settings import MONITORING_CONFIG
from monitoring.paper_log import (
    aggregate, close_signal, compute_levels, compute_net_r, has_open, init_db,
    record_signal, resolve_virtual_exit,
)


def test_compute_levels_long_short():
    lv = compute_levels("LONG", 100.0, 1.0)            # sl 2ATR / tp 3ATR
    assert lv.sl == 98.0 and lv.tp == 103.0
    assert abs(lv.rr - 1.5) < 1e-9                     # 3/2
    assert abs(lv.risk_usdt - MONITORING_CONFIG.risk_pct * MONITORING_CONFIG.budget_usdt) < 1e-9
    assert abs(lv.size_usdt - 50.0) < 1e-6             # risk1·entry100/stop2
    lv2 = compute_levels("SHORT", 100.0, 1.0)
    assert lv2.sl == 102.0 and lv2.tp == 97.0
    assert compute_levels("LONG", 0.0, 1.0) is None    # 가드


def test_resolve_virtual_exit():
    assert resolve_virtual_exit("LONG", 98, 103, [(101, 99.5, 100.5), (103.5, 102, 103)], 32) == (103, "TP", 2)
    assert resolve_virtual_exit("LONG", 98, 103, [(101, 97.5, 98)], 32) == (98, "SL", 1)
    assert resolve_virtual_exit("LONG", 98, 103, [(103.5, 97.5, 100)], 32)[1] == "SL"   # 동시→SL우선
    assert resolve_virtual_exit("LONG", 98, 103, [(100.5, 99.5, 100.0)] * 5, 3) == (100.0, "TIME_STOP", 3)
    assert resolve_virtual_exit("LONG", 98, 103, [(100.5, 99.5, 100.0)], 32) is None     # 미청산
    assert resolve_virtual_exit("SHORT", 102, 97, [(100.5, 99, 99.5), (98, 96.5, 97)], 32) == (97, "TP", 2)


def test_compute_net_r_cost_consistency():
    cfg = MONITORING_CONFIG
    gross, fees, net, net_r = compute_net_r("LONG", 100.0, 103.0, 98.0, 50.0, cfg)
    assert abs(gross - 0.03 * 50.0) < 1e-9                                   # +3%·50
    assert abs(fees - (cfg.maker_fee + cfg.taker_fee + cfg.slippage) * 50.0) < 1e-12
    risk = abs(100.0 - 98.0) / 100.0 * 50.0                                  # =1.0
    assert abs(net_r - net / risk) < 1e-9 and net > 0


def test_db_record_dedup_close_aggregate(tmp_path):
    db = str(tmp_path / "paper.db")
    init_db(db)
    rid = record_signal(db, "2026-01-01T00:00", "ADAUSDT", "LONG", "STRONG", 100.0, 1.0, {"vol": 3})
    assert rid is not None and has_open(db, "ADAUSDT")
    assert record_signal(db, "2026-01-01T00:15", "ADAUSDT", "LONG", "WEAK", 101.0, 1.0) is None  # 동시 1건
    close_signal(db, rid, "2026-01-02T00:00", 103.0, "TP")
    assert not has_open(db, "ADAUSDT")
    agg = aggregate(db)
    assert agg["all"]["n"] == 1 and agg["all"]["win_rate"] == 1.0
    assert agg["STRONG"]["n"] == 1 and agg["WEAK"]["n"] == 0
    assert agg["all"]["gross_r"] > agg["all"]["cost_r"]                      # TP 승
