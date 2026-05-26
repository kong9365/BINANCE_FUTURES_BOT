"""tests/test_dashboard_charts.py — Plotly chart builder 단위 테스트."""

from __future__ import annotations

import plotly.graph_objects as go
import pytest

from dashboard.charts import (
    agent_verdict_matrix,
    candlestick_chart,
    equity_curve,
    event_type_distribution,
    kill_switch_history,
    setup_registry_table,
    signal_timeline,
)


# 각 chart 가 Figure 반환 + 빈 입력 fail-safe


def test_equity_curve_empty():
    fig = equity_curve([])
    assert isinstance(fig, go.Figure)


def test_equity_curve_with_trades():
    trades = [
        {"timestamp": "2026-05-27T10:00:00", "pnl_usd": 10.0},
        {"timestamp": "2026-05-27T11:00:00", "pnl_usd": -5.0},
        {"timestamp": "2026-05-27T12:00:00", "pnl_usd": 8.0},
    ]
    fig = equity_curve(trades)
    assert isinstance(fig, go.Figure)
    # equity curve 1 trace
    assert len(fig.data) == 1


def test_signal_timeline_empty():
    fig = signal_timeline([])
    assert isinstance(fig, go.Figure)


def test_signal_timeline_long_short_flat():
    signals = [
        {"ts_signal_generated": "2026-05-27T10:00:00", "action": "LONG",
         "confidence": 0.8, "symbol": "SOLUSDT", "setup_id": "test", "ev_estimated": 0.15},
        {"ts_signal_generated": "2026-05-27T11:00:00", "action": "SHORT",
         "confidence": 0.7, "symbol": "AVAXUSDT", "setup_id": "test", "ev_estimated": 0.12},
        {"ts_signal_generated": "2026-05-27T12:00:00", "action": "FLAT",
         "confidence": 0.5, "symbol": "LINKUSDT", "setup_id": "test", "ev_estimated": 0.0},
    ]
    fig = signal_timeline(signals)
    assert isinstance(fig, go.Figure)
    # 3 traces (LONG, SHORT, FLAT)
    assert len(fig.data) == 3


def test_agent_verdict_matrix_empty():
    fig = agent_verdict_matrix([])
    assert isinstance(fig, go.Figure)


def test_agent_verdict_matrix_5_agents():
    reviews = [
        {"agent": "strategy_quant", "verdict": "PASS", "ts": "2026-05-27T10:00:00"},
        {"agent": "risk", "verdict": "APPROVE", "ts": "2026-05-27T10:00:01"},
        {"agent": "execution", "verdict": "EXECUTE", "ts": "2026-05-27T10:00:02"},
        {"agent": "data_backtest", "verdict": "VALID", "ts": "2026-05-27T10:00:03"},
        {"agent": "ops_observability", "verdict": "HEALTHY", "ts": "2026-05-27T10:00:04"},
    ]
    fig = agent_verdict_matrix(reviews)
    assert isinstance(fig, go.Figure)
    # 5 agents → 5 traces
    assert len(fig.data) == 5


def test_event_type_distribution_empty():
    fig = event_type_distribution({})
    assert isinstance(fig, go.Figure)


def test_event_type_distribution_with_data():
    counts = {"SIGNAL_GENERATED": 10, "AGENT_REVIEW": 50, "SYSTEM_START": 1}
    fig = event_type_distribution(counts)
    assert isinstance(fig, go.Figure)


def test_candlestick_chart_empty():
    fig = candlestick_chart([], symbol="BTCUSDT")
    assert isinstance(fig, go.Figure)


def test_candlestick_chart_with_candles():
    # (ts, o, h, l, c, v)
    candles = [
        ("2026-05-27T10:00:00", 100.0, 101.0, 99.5, 100.8, 1000.0),
        ("2026-05-27T10:01:00", 100.8, 102.0, 100.5, 101.5, 1200.0),
    ]
    fig = candlestick_chart(candles, symbol="SOLUSDT")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1


def test_setup_registry_table_empty():
    fig = setup_registry_table([])
    assert isinstance(fig, go.Figure)


def test_setup_registry_table_with_data():
    setups = [
        {
            "setup_id": "1d_tsmom_donchian_long_v1",
            "status": "PAPER_ONLY",
            "last_metric_n": 250,
            "last_metric_pf": 1.35,
            "last_metric_expectancy_r": 0.15,
            "last_metric_avg_win_loss_ratio": 1.8,
            "last_metric_mdd_pct": 20.0,
            "last_metric_single_symbol_max_pct": 18.0,
            "last_metric_top3_excluded_pf": 1.15,
            "last_metric_win_rate": 0.55,
        }
    ]
    fig = setup_registry_table(setups)
    assert isinstance(fig, go.Figure)


def test_kill_switch_history_empty():
    fig = kill_switch_history([])
    assert isinstance(fig, go.Figure)


def test_kill_switch_history_with_events():
    events = [
        {"activated_at": "2026-05-27T10:00:00", "source": "manual",
         "reason": "test", "deactivated_at": "2026-05-27T10:30:00",
         "deactivated_by": "operator"},
        {"activated_at": "2026-05-27T15:00:00", "source": "daily_loss_hard",
         "reason": "-1.5%", "deactivated_at": None},
    ]
    fig = kill_switch_history(events)
    assert isinstance(fig, go.Figure)
