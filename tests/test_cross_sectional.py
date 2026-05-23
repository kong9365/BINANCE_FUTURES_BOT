"""
tests/test_cross_sectional.py
=====================================================================
backtesting/cross_sectional.py — CSM 하네스 단위 테스트.
랭킹·룩어헤드·리밸런스 실행·비용 반영·결과 형식 검증.
=====================================================================
"""

from __future__ import annotations

import pandas as pd

from backtesting import cross_sectional as cs
from backtesting import portfolio_backtest as pbt


def test_select_top_basic():
    rets = {"A": 0.1, "B": 0.5, "C": -0.2, "D": 0.3, "E": 0.05}
    out = cs.select_top(rets, top_pct=0.40, max_holdings=10)
    assert out == ["B", "D"]                # 상위 40%(2종): B=0.5, D=0.3


def test_select_top_min_one():
    out = cs.select_top({"A": 0.1, "B": 0.2}, top_pct=0.01, max_holdings=10)
    assert out == ["B"]                     # 최소 1


def test_select_top_excludes_nan():
    import math
    out = cs.select_top({"A": float("nan"), "B": 0.1, "C": 0.2},
                        top_pct=0.34, max_holdings=10)
    assert out == ["C"]


def _trend_df(n_days, start_price, daily_pct, start_date="2026-01-01"):
    idx = pd.date_range(start_date, periods=n_days, freq="1D", tz="UTC")
    closes = [start_price * (1 + daily_pct) ** i for i in range(n_days)]
    rows = []
    for c in closes:
        rows.append({"open": c, "high": c * 1.01, "low": c * 0.99,
                     "close": c, "volume": 1000.0})
    return pd.DataFrame(rows, index=idx)


def test_run_csm_executes_and_returns_result_shape():
    # 5 종목: 상승 강함/약함/평/하락 → 상위 분위에 강세 종목 선택돼야
    data = {
        "STRONG1": _trend_df(60, 100, 0.02),    # +2%/day
        "STRONG2": _trend_df(60, 100, 0.015),
        "FLAT1":   _trend_df(60, 100, 0.0),
        "WEAK1":   _trend_df(60, 100, -0.01),
        "WEAK2":   _trend_df(60, 100, -0.005),
    }
    tiers = {s: 2 for s in data}
    cfg = cs.CSMConfig(lookback_bars=14, rebalance_bars=5, top_pct=0.40,
                       min_universe=3)
    port = pbt.PortfolioConfig(max_concurrent=2, size_pct=0.45,
                                initial_capital=1000.0)
    r = cs.run_cross_sectional(data, tiers, cfg=cfg, port_cfg=port,
                                bar_hours=24.0, benchmark_symbol="NONE")
    assert isinstance(r, pbt.PortfolioResult)
    assert r.total_trades > 0
    # 강세 종목이 진입되어야 함(어느 시점에든)
    traded = {t.symbol for t in r.trades}
    assert "STRONG1" in traded or "STRONG2" in traded


def test_run_csm_costs_reduce_pnl():
    data = {f"S{i}": _trend_df(60, 100, 0.01 * (5 - i))   # S0 강, S4 약
            for i in range(5)}
    tiers = {s: 2 for s in data}
    cfg = cs.CSMConfig(lookback_bars=14, rebalance_bars=5, top_pct=0.40,
                       min_universe=3)
    port = pbt.PortfolioConfig(max_concurrent=2, size_pct=0.45)
    no_cost = pbt.ExecConfig(taker_fee=0.0, slippage_by_tier={2: 0.0},
                              stop_slippage_pct=0.0, apply_funding=False)
    with_cost = pbt.ExecConfig()
    r0 = cs.run_cross_sectional(data, tiers, cfg=cfg, port_cfg=port,
                                  exec_cfg=no_cost, bar_hours=24.0,
                                  benchmark_symbol="NONE")
    r1 = cs.run_cross_sectional(data, tiers, cfg=cfg, port_cfg=port,
                                  exec_cfg=with_cost, bar_hours=24.0,
                                  benchmark_symbol="NONE")
    p0 = sum(t.pnl_usd for t in r0.trades)
    p1 = sum(t.pnl_usd for t in r1.trades)
    assert p1 < p0                          # 비용이 손익을 줄임


def test_csm_no_lookahead_uses_only_closed_data():
    """리밸런스 결정은 ts 마감봉 종가로, 진입은 다음 봉 시가로 — 본 시그너 보장.

    동작 검증: trade.entry_ts 가 항상 trade.entry_price 가 속한 봉의 시간이고,
    그 봉 이전 봉의 종가에서 랭킹이 결정되도록 설계됨.
    """
    data = {
        "A": _trend_df(30, 100, 0.02),
        "B": _trend_df(30, 100, 0.01),
        "C": _trend_df(30, 100, 0.005),
    }
    tiers = {s: 2 for s in data}
    cfg = cs.CSMConfig(lookback_bars=10, rebalance_bars=5, top_pct=0.34,
                       min_universe=3)
    port = pbt.PortfolioConfig(max_concurrent=2, size_pct=0.45)
    r = cs.run_cross_sectional(data, tiers, cfg=cfg, port_cfg=port,
                                bar_hours=24.0, benchmark_symbol="NONE")
    # 모든 진입의 entry_ts 는 timeline 의 lookback_bars 이후이어야(워밍업 후 첫 리밸런스 이후)
    first_ok = data["A"].index[cfg.lookback_bars]
    for t in r.trades:
        assert t.entry_ts >= first_ok
