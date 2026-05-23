"""
tests/test_portfolio_backtest.py
=====================================================================
backtesting/portfolio_backtest.py — 신뢰 가능 포트폴리오 백테스트 코어 테스트.

핵심: 지표 시리즈가 strategy/breakout.py 스칼라 함수와 **수학적으로 동일**함을 입증
(검증기 ≠ 프로덕션 괴리 차단). + 신호/실행/포트폴리오/비용 동작 검증.
=====================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy import breakout as bo
from backtesting import portfolio_backtest as pbt


# ── 결정적 시계열 ──
def _series(n=80):
    x = np.arange(n)
    base = 100.0 + x * 0.5 + 5.0 * np.sin(x / 3.0)
    h = base + 1.0
    l = base - 1.0
    c = base
    return h, l, c


# ── 지표 등가성 (프로덕션 정합) ──
def test_ema_series_matches_scalar():
    _, _, c = _series()
    es = pbt.ema_series(c, 10)
    for k in (30, 50, 79):
        assert np.isclose(es[k], bo.ema(list(c[:k + 1]), 10))


def test_atr_series_matches_scalar():
    h, l, c = _series()
    s = pbt.atr_series(h, l, c, 10)
    for k in (30, 50, 79):
        assert np.isclose(s[k], bo.atr(list(h[:k + 1]), list(l[:k + 1]),
                                       list(c[:k + 1]), 10))


def test_adx_series_matches_scalar():
    h, l, c = _series()
    s = pbt.adx_series(h, l, c, 5)
    for k in (30, 50, 79):
        assert np.isclose(s[k], bo.adx(list(h[:k + 1]), list(l[:k + 1]),
                                       list(c[:k + 1]), 5), rtol=1e-6, atol=1e-6)


def test_donchian_series_matches_scalar():
    h, l, c = _series()
    up, lo = pbt.donchian_series(h, l, 10)
    for k in (30, 50, 79):
        eu, el = bo.donchian(list(h[:k + 1]), list(l[:k + 1]), 10)
        assert np.isclose(up[k], eu) and np.isclose(lo[k], el)


# ── 신호 ──
def _uptrend_df(n=70, slope=0.004):
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    price = 100.0
    rows = []
    for _ in range(n):
        o = price
        cl = price * (1 + slope)
        rows.append({"open": o, "high": max(o, cl) * 1.001,
                     "low": min(o, cl) * 0.999, "close": cl, "volume": 1000.0})
        price = cl
    return pd.DataFrame(rows, index=idx)


_CFG = bo.BreakoutConfig(donchian_entry=10, adx_period=5, ema_period=20,
                         atr_period=5, adx_trend_min=20.0)


def test_compute_signals_long():
    sdf = pbt.compute_signals(_uptrend_df(), _CFG)
    assert (sdf["signal"] == 1).any()      # 상승 추세 → LONG 신호 발생
    assert (sdf["atr"] > 0).any()


# ── 포트폴리오 실행 ──
def test_run_portfolio_executes_trade():
    data = {"SOLUSDT": _uptrend_df()}
    r = pbt.run_portfolio(data, tiers={"SOLUSDT": 2}, breakout_cfg=_CFG)
    assert r.total_trades >= 1
    assert len(r.equity_curve) > 0
    assert r.trades[0].symbol == "SOLUSDT"


def test_costs_reduce_pnl():
    data = {"SOLUSDT": _uptrend_df()}
    no_cost = pbt.ExecConfig(taker_fee=0.0, slippage_by_tier={2: 0.0},
                             stop_slippage_pct=0.0, apply_funding=False)
    with_cost = pbt.ExecConfig()
    r0 = pbt.run_portfolio(data, {"SOLUSDT": 2}, breakout_cfg=_CFG, exec_cfg=no_cost)
    r1 = pbt.run_portfolio(data, {"SOLUSDT": 2}, breakout_cfg=_CFG, exec_cfg=with_cost)
    # 동일 신호인데 비용 반영분이 손익을 낮춘다
    p0 = sum(t.pnl_usd for t in r0.trades)
    p1 = sum(t.pnl_usd for t in r1.trades)
    assert p1 < p0


def test_exec_config_tier_costs_include_4_5():
    """광범위 유니버스용 tier 4/5 슬리피지가 기본값에 포함된다(0.30% / 0.60%)."""
    cfg = pbt.ExecConfig()
    assert cfg.slippage_by_tier[1] == 0.00050
    assert cfg.slippage_by_tier[2] == 0.00100
    assert cfg.slippage_by_tier[3] == 0.00150
    assert cfg.slippage_by_tier[4] == 0.00300
    assert cfg.slippage_by_tier[5] == 0.00600


def test_tier5_slippage_reduces_pnl_more(tmp_path=None):
    """동일 신호여도 티어 5(저유동) 비용이 티어 1(BTC급)보다 손익을 더 크게 깎는다."""
    data = {"X": _uptrend_df()}
    r1 = pbt.run_portfolio(data, tiers={"X": 1}, breakout_cfg=_CFG)
    r5 = pbt.run_portfolio(data, tiers={"X": 5}, breakout_cfg=_CFG)
    p1 = sum(t.pnl_usd for t in r1.trades)
    p5 = sum(t.pnl_usd for t in r5.trades)
    assert p5 < p1                          # tier5 슬리피지가 더 커서 pnl 작음


def test_max_concurrent_limit():
    data = {"AAA": _uptrend_df(), "BBB": _uptrend_df(), "CCC": _uptrend_df()}
    tiers = {"AAA": 2, "BBB": 2, "CCC": 2}
    pc = pbt.PortfolioConfig(max_concurrent=1)
    r = pbt.run_portfolio(data, tiers, breakout_cfg=_CFG, port_cfg=pc)
    # 동시보유 1 제한 → 같은 ts 에 2개 이상 진입 불가(체결 시점 중복 없음)
    # 진입 ts 별 카운트가 1을 넘지 않아야 함(슬롯 1)
    from collections import Counter
    starts = Counter(t.entry_ts for t in r.trades)
    assert all(v <= 1 for v in starts.values())
