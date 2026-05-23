"""
tests/test_evaluate.py
=====================================================================
backtesting/evaluate.py — 사전확정 기준 평가 단위 테스트.
=====================================================================
"""

from __future__ import annotations

import pandas as pd

from backtesting.evaluate import PreCommittedBar, evaluate, format_report
from backtesting.portfolio_backtest import PortfolioResult, Trade


def _mkres(trades, mdd=5.0, sharpe=1.5):
    return PortfolioResult(
        trades=trades, equity_curve=[], total_trades=len(trades),
        win_rate=0.5, profit_factor=1.4, expectancy_R=0.1,
        total_return_pct=20.0, max_drawdown_pct=mdd, sharpe=sharpe, benchmark={},
    )


def _trade(sym, pnl_usd, pnl_R, entry_ts):
    return Trade(
        symbol=sym, direction=1, entry_ts=pd.Timestamp(entry_ts, tz="UTC"),
        exit_ts=pd.Timestamp(entry_ts, tz="UTC"), entry_price=100.0,
        exit_price=100 + pnl_usd, notional=100.0, pnl_usd=pnl_usd, pnl_R=pnl_R,
        fees_usd=0.0, funding_usd=0.0, exit_reason="X", bars_held=1,
    )


def test_all_pass():
    # 200 trades, 5 symbols, 분산된 양(+), OOS 통과
    trades = []
    for i in range(200):
        sym = ["A", "B", "C", "D", "E"][i % 5]
        pnl = 1.0 if i % 3 != 0 else -0.5     # 양수가 많음
        ts = "2025-12-01" if i < 100 else "2026-04-01"   # 절반 OOS
        trades.append(_trade(sym, pnl, pnl, ts))
    r = _mkres(trades, mdd=5.0, sharpe=1.5)
    rep = evaluate("S", r, oos_cutoff=pd.Timestamp("2026-01-01", tz="UTC"))
    assert rep.passed is True
    assert all(c.passed for c in rep.criteria)


def test_fail_sample_too_small():
    trades = [_trade("A", 1.0, 1.0, "2026-01-01") for _ in range(50)]
    r = _mkres(trades)
    rep = evaluate("S", r, oos_cutoff=pd.Timestamp("2025-01-01", tz="UTC"))
    assert rep.passed is False
    assert any(c.name == "sample_trades" and not c.passed for c in rep.criteria)


def test_fail_single_sym_concentration():
    # 200 trades, 1 symbol에 손익 집중
    trades = []
    for i in range(200):
        sym = "DOMINANT" if i < 100 else "OTHER"
        pnl = 10.0 if sym == "DOMINANT" else 0.05
        ts = "2025-12-01" if i < 100 else "2026-04-01"
        trades.append(_trade(sym, pnl, pnl, ts))
    rep = evaluate("S", _mkres(trades), oos_cutoff=pd.Timestamp("2026-01-01", tz="UTC"))
    crit = next(c for c in rep.criteria if c.name == "single_sym_share")
    assert not crit.passed
    assert rep.passed is False


def test_fail_oos_pf():
    # OOS 영역 손실만
    trades = []
    for i in range(200):
        ts = "2025-12-01" if i < 100 else "2026-04-01"
        pnl = 1.0 if i < 100 else -1.0          # OOS 전 양호, OOS 전부 손실
        trades.append(_trade(f"S{i%10}", pnl, pnl, ts))
    rep = evaluate("S", _mkres(trades), oos_cutoff=pd.Timestamp("2026-01-01", tz="UTC"))
    pf = next(c for c in rep.criteria if c.name == "oos_pf")
    assert not pf.passed


def test_fail_mdd_too_high():
    trades = [_trade(f"S{i%10}", 1.0, 1.0, "2026-01-01") for i in range(200)]
    r = _mkres(trades, mdd=25.0)
    rep = evaluate("S", r, oos_cutoff=pd.Timestamp("2025-01-01", tz="UTC"))
    crit = next(c for c in rep.criteria if c.name == "max_drawdown_pct")
    assert not crit.passed


def test_format_report_runs():
    trades = [_trade("A", 1.0, 1.0, "2026-01-01")]
    rep = evaluate("ORB", _mkres(trades))
    s = format_report(rep)
    assert "ORB" in s and ("PASS" in s or "FAIL" in s)
