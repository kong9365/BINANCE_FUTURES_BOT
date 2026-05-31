"""
tests/test_unfilled_signal_analyzer.py
=====================================================================
Track A B-3 통계 계측기 테스트 (순수 + 가격소스 주입, 네트워크 격리).
forward-return / Welch 비교(유의·무·표본부족) / 판정 / measure 통합.
"""

from __future__ import annotations

import pytest

from analytics.unfilled_signal_analyzer import (
    b3_verdict,
    compare,
    forward_return_at,
    measure,
)


def test_forward_return_at_direction_and_guards():
    prices = [(1000, 100.0), (16000, 110.0)]   # +15s(=16000) → 110
    assert forward_return_at(prices, 1000, 100.0, "LONG", 15) == pytest.approx(0.10)
    assert forward_return_at(prices, 1000, 100.0, "SHORT", 15) == pytest.approx(-0.10)
    assert forward_return_at([(1000, 100.0)], 1000, 100.0, "LONG", 15) is None   # target 가격 없음
    assert forward_return_at(prices, 1000, 0.0, "LONG", 15) is None              # ref 0
    assert forward_return_at([], 1000, 100.0, "LONG", 15) is None


def test_compare_significant_unfilled_beats_filled():
    unfilled = [0.05, 0.06, 0.04, 0.05, 0.07, 0.05] * 5     # ~+5%
    filled = [-0.02, -0.01, -0.03, -0.02, 0.0, -0.02] * 5   # ~-1.7%
    r = compare(filled, unfilled)
    assert r["difference"] > 0
    assert r["p_value"] < 0.05
    assert r["ci_low"] > 0           # 95% CI 가 0 배제 = 유의


def test_compare_no_significant_difference():
    a = [0.01, -0.01, 0.0, 0.02, -0.02] * 6
    b = [0.0, 0.01, -0.01, 0.005, -0.005] * 6
    r = compare(a, b)
    assert r["p_value"] > 0.05


def test_compare_insufficient_sample():
    r = compare([0.01], [0.02])      # n<2
    assert r["t_stat"] is None and r["p_value"] is None
    assert r["avg_filled"] == pytest.approx(0.01)   # 평균은 계산


def test_b3_verdict():
    assert b3_verdict({15: {"p_value": 0.01, "difference": 0.05}}) == "PASS"
    assert b3_verdict({15: {"p_value": 0.50, "difference": 0.01}}) == "FAIL"
    assert b3_verdict({15: {"p_value": None, "difference": None}}) == "INSUFFICIENT_SAMPLE"


def test_measure_aggregates_filled_vs_unfilled():
    events = [
        {"symbol": "X", "side": "LONG", "order_time_ms": 1000, "ref_price": 100.0, "filled": True},
        {"symbol": "X", "side": "LONG", "order_time_ms": 2000, "ref_price": 100.0, "filled": True},
        {"symbol": "X", "side": "LONG", "order_time_ms": 3000, "ref_price": 100.0, "filled": False},
        {"symbol": "X", "side": "LONG", "order_time_ms": 4000, "ref_price": 100.0, "filled": False},
    ]

    def fetch(symbol, start, end):
        # 체결(1000/2000) = 되돌아온 패자(-2%), 미체결(3000/4000) = 달아난 승자(+5%)
        if start in (1000, 2000):
            return [(start, 100.0), (start + 15000, 98.0)]
        return [(start, 100.0), (start + 15000, 105.0)]

    hr, verdict = measure(events, fetch, horizons=(15,))
    assert hr[15]["avg_filled"] == pytest.approx(-0.02)
    assert hr[15]["avg_unfilled"] == pytest.approx(0.05)
    assert hr[15]["difference"] == pytest.approx(0.07)     # 미체결 > 체결
