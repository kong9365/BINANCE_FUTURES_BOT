"""
tests/test_core_validation.py
=====================================================================
Track B 검증 코어(analytics/core_strategy_validation) 단위테스트 — 순수 게이트/메트릭.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from analytics.core_strategy_validation import (
    compute_metrics,
    cross_pair_positive_frac,
    evaluate_gates,
    worst_window_mdd,
)

_CUTOFF = datetime(2024, 1, 1)


def _t(symbol, action, day, pnl_usd, pnl_R, gross_pnl_usd, fees, risk=10.0):
    return SimpleNamespace(
        symbol=symbol, action=action, entry_ts=_CUTOFF + timedelta(days=day),
        pnl_usd=pnl_usd, pnl_R=pnl_R, gross_pnl_usd=gross_pnl_usd,
        fees_usd=fees, funding_usd=0.0, risk_usdt=risk, bars_held=5,
    )


# ── evaluate_gates (사전확정 임계) ──
_PASS = dict(n=250, gross_R=0.10, cost_R=0.02, pf=1.5, sharpe=1.2,
             worst_mdd=10.0, cross_pair_positive=0.60)


def test_gates_pass():
    assert evaluate_gates(_PASS)[0] == "PASS"


def test_gates_insufficient_sample():
    v, failed = evaluate_gates({"n": 50})
    assert v == "INSUFFICIENT_SAMPLE"


def test_gates_fail_each_threshold():
    assert evaluate_gates({**_PASS, "gross_R": 0.01})[0] == "FAIL"   # gross ≤ cost
    assert evaluate_gates({**_PASS, "pf": 1.1})[0] == "FAIL"
    assert evaluate_gates({**_PASS, "sharpe": 0.5})[0] == "FAIL"
    assert evaluate_gates({**_PASS, "worst_mdd": 25.0})[0] == "FAIL"
    assert evaluate_gates({**_PASS, "cross_pair_positive": 0.50})[0] == "FAIL"


# ── worst_window_mdd ──
def test_worst_window_mdd():
    # 1000 → 1200 → 900 (peak 1200, trough 900) → MDD 25%
    eq = [(datetime(2024, 1, 1), 1000), (datetime(2024, 1, 2), 1200),
          (datetime(2024, 1, 3), 900)]
    assert worst_window_mdd(eq) == pytest.approx(25.0)
    assert worst_window_mdd([]) == 0.0


# ── cross_pair_positive_frac ──
def test_cross_pair_positive_frac():
    trades = [_t("A", "LONG", 1, 5, 0.5, 6, 1), _t("A", "LONG", 2, -3, -0.3, -2, 1),
              _t("B", "LONG", 1, -5, -0.5, -4, 1), _t("C", "LONG", 1, 2, 0.2, 3, 1)]
    frac, by = cross_pair_positive_frac(trades)
    # A net +0.2(>0), B -0.5(<0), C +0.2(>0) → 2/3
    assert frac == pytest.approx(2 / 3)


# ── compute_metrics (OOS partition + 방향 분리) ──
def test_compute_metrics_insufficient_and_split():
    trades = ([_t("BTCUSDT", "LONG", i, 1.0, 0.1, 1.2, 0.2) for i in range(8)]
              + [_t("ETHUSDT", "SHORT", i, -1.0, -0.1, -0.8, 0.2) for i in range(6)])
    eq = [(_CUTOFF + timedelta(days=i), 1000 + i) for i in range(400)]
    m = compute_metrics(trades, eq, _CUTOFF)
    assert m["n"] == 14
    assert m["long"]["n"] == 8 and m["short"]["n"] == 6
    assert evaluate_gates(m)[0] == "INSUFFICIENT_SAMPLE"   # n<200


def test_compute_metrics_excludes_pre_cutoff():
    # cutoff 이전 거래는 OOS 에서 제외
    trades = [_t("BTCUSDT", "LONG", -10, 1.0, 0.1, 1.2, 0.2),   # cutoff 이전
              _t("BTCUSDT", "LONG", 5, 1.0, 0.1, 1.2, 0.2)]     # cutoff 이후
    eq = [(_CUTOFF + timedelta(days=i), 1000 + i) for i in range(30)]
    m = compute_metrics(trades, eq, _CUTOFF)
    assert m["n"] == 1
