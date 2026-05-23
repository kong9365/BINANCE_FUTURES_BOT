"""
backtesting/cross_sectional.py
=====================================================================
Cross-Sectional Momentum (CSM) — 횡단면 모멘텀 백테스트 하네스 (S1).

엣지 가설:
  광범위 유니버스에서 trailing N일 수익률로 종목 랭킹 → 상위 분위(top decile) 롱.
  주기적 리밸런스. Han·Kang·Ryu(SSRN 4675565) 보고 Sharpe ~1.5(unoptimized).
  메커니즘 distinct(절대 돌파/펀딩 페이드와 다른 *상대* 랭킹 기반).

설계:
  - 리밸런스 이벤트 기반(portfolio_backtest 의 per-bar per-symbol signal_fn 과 별개).
  - 룩어헤드 차단: 리밸런스 시점 ts 의 마감봉 종가로 랭킹 → 트레이드는 **다음 봉 시가**
    에서 실행(close removed / open new).
  - 메트릭/Trade/EquityCurve 형식은 portfolio_backtest 와 동일(PortfolioResult 재사용).
  - 비용: 진입/청산 모두 테이커 + 티어별 슬리피지. 펀딩 누적 동일.
  - 종목 수가 cfg.min_universe 미만이면 그 리밸런스는 스킵(데이터 부족).
=====================================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from backtesting.portfolio_backtest import (
    ExecConfig, PortfolioConfig, PortfolioResult, Trade, _metrics,
)


@dataclass
class CSMConfig:
    lookback_bars: int = 28      # trailing 수익률 윈도(봉)
    rebalance_bars: int = 5      # N봉마다 리밸런스
    top_pct: float = 0.10        # 상위 분위(%)
    min_universe: int = 10       # 이 미만이면 해당 리밸런스 스킵


class _Pos:
    __slots__ = ("symbol", "entry_ts", "entry_price", "notional",
                 "tier", "funding_acc")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def select_top(returns: Dict[str, float], top_pct: float,
               max_holdings: int) -> List[str]:
    """returns(dict) 에서 상위 top_pct 종목(최대 max_holdings) 반환."""
    items = [(r, s) for s, r in returns.items() if not math.isnan(r)]
    if not items:
        return []
    items.sort(reverse=True)
    n = len(items)
    k = max(1, min(int(round(n * top_pct)), max_holdings, n))
    return [s for _, s in items[:k]]


def run_cross_sectional(
    data: Dict[str, pd.DataFrame],
    tiers: Dict[str, int],
    cfg: Optional[CSMConfig] = None,
    exec_cfg: Optional[ExecConfig] = None,
    port_cfg: Optional[PortfolioConfig] = None,
    bar_hours: float = 24.0,
    benchmark_symbol: str = "BTCUSDT",
) -> PortfolioResult:
    """CSM 백테스트 실행 — 결과는 portfolio_backtest 와 동일 형식(PortfolioResult)."""
    cfg = cfg or CSMConfig()
    exec_cfg = exec_cfg or ExecConfig()
    port_cfg = port_cfg or PortfolioConfig()

    prepared: Dict[str, dict] = {}
    all_ts: set = set()
    for sym, df in data.items():
        df = df.sort_index()
        prepared[sym] = {
            "open": df["open"].to_numpy(float),
            "close": df["close"].to_numpy(float),
            "funding": (df["funding_rate"].to_numpy(float)
                        if "funding_rate" in df.columns else None),
            "pos_of_ts": {t: i for i, t in enumerate(df.index)},
        }
        all_ts.update(df.index)
    timeline = sorted(all_ts)
    if len(timeline) <= cfg.lookback_bars + 1:
        return PortfolioResult(
            trades=[], equity_curve=[], total_trades=0, win_rate=0.0,
            profit_factor=0.0, expectancy_R=0.0, total_return_pct=0.0,
            max_drawdown_pct=0.0, sharpe=0.0, benchmark={},
        )

    fund_periods = bar_hours / exec_cfg.funding_interval_hours
    equity = port_cfg.initial_capital
    open_pos: Dict[str, _Pos] = {}
    trades: List[Trade] = []
    equity_curve: List[Tuple[pd.Timestamp, float]] = []

    rebalance_set = set(range(cfg.lookback_bars,
                              len(timeline), cfg.rebalance_bars))
    pending_targets: Optional[List[str]] = None

    def _exit(p: _Pos, exit_price: float, ts, reason: str = "REBALANCE"):
        nonlocal equity
        slip = exec_cfg.slippage_by_tier.get(p.tier, 0.0015)
        gross = (exit_price - p.entry_price) / p.entry_price
        fees = (2 * exec_cfg.taker_fee + 2 * slip) * p.notional
        pnl = gross * p.notional - fees - p.funding_acc
        equity += pnl
        risk = p.notional * 0.05      # 가상 5% 위험 단위(R 비교용)
        trades.append(Trade(
            symbol=p.symbol, direction=1, entry_ts=p.entry_ts, exit_ts=ts,
            entry_price=p.entry_price, exit_price=exit_price,
            notional=p.notional, pnl_usd=pnl,
            pnl_R=(pnl / risk if risk > 0 else 0.0),
            fees_usd=fees, funding_usd=p.funding_acc,
            exit_reason=reason, bars_held=0,
        ))

    for ti, ts in enumerate(timeline):
        # 1) funding 누적
        for sym, p in open_pos.items():
            d = prepared[sym]
            i = d["pos_of_ts"].get(ts)
            if i is None:
                continue
            if exec_cfg.apply_funding and d["funding"] is not None:
                fr = d["funding"][i]
                if not math.isnan(fr):
                    p.funding_acc += fr * p.notional * fund_periods

        # 2) 직전 리밸런스 결정 → 이번 봉 시가에 실행(룩어헤드 차단)
        if pending_targets is not None:
            new_set = set(pending_targets)
            # 제거: new_set 에 없는 보유 종목 청산
            for sym in list(open_pos.keys()):
                if sym not in new_set:
                    d = prepared[sym]
                    i = d["pos_of_ts"].get(ts)
                    if i is None:
                        continue
                    p = open_pos[sym]
                    slip = exec_cfg.slippage_by_tier.get(p.tier, 0.0015)
                    exit_p = d["open"][i] * (1 - slip)  # long 청산: 더 낮은 호가
                    _exit(p, exit_p, ts)
                    del open_pos[sym]
            # 추가: 신규 종목 개시
            to_open = [s for s in pending_targets if s not in open_pos]
            if to_open:
                per_notional = equity * port_cfg.size_pct
                for sym in to_open:
                    d = prepared[sym]
                    i = d["pos_of_ts"].get(ts)
                    if i is None:
                        continue
                    tier = tiers.get(sym, 3)
                    slip = exec_cfg.slippage_by_tier.get(tier, 0.0015)
                    entry = d["open"][i] * (1 + slip)
                    if entry <= 0 or per_notional <= 0:
                        continue
                    open_pos[sym] = _Pos(
                        symbol=sym, entry_ts=ts, entry_price=entry,
                        notional=per_notional, tier=tier, funding_acc=0.0,
                    )
            pending_targets = None

        # 3) 이번 ts 가 리밸런스 시점이면 → 마감봉 종가로 랭킹, 다음 봉에 실행 큐잉
        if ti in rebalance_set:
            returns: Dict[str, float] = {}
            for sym, d in prepared.items():
                i = d["pos_of_ts"].get(ts)
                if i is None or i < cfg.lookback_bars:
                    continue
                base = d["close"][i - cfg.lookback_bars]
                cur = d["close"][i]
                if base > 0:
                    returns[sym] = (cur - base) / base
            if len(returns) >= cfg.min_universe:
                pending_targets = select_top(
                    returns, cfg.top_pct, port_cfg.max_concurrent
                )

        # 4) MTM 자본곡선
        unreal = 0.0
        for sym, p in open_pos.items():
            i = prepared[sym]["pos_of_ts"].get(ts)
            if i is None:
                continue
            cl = prepared[sym]["close"][i]
            unreal += (cl - p.entry_price) / p.entry_price * p.notional
        equity_curve.append((ts, equity + unreal))

    # 마지막 봉에서 잔여 포지션 청산(데이터 끝)
    last_ts = timeline[-1]
    for sym in list(open_pos.keys()):
        d = prepared[sym]
        i = d["pos_of_ts"].get(last_ts)
        if i is None:
            continue
        _exit(open_pos[sym], d["close"][i], last_ts, reason="DATA_END")
        del open_pos[sym]

    return _metrics(trades, equity_curve, port_cfg, data,
                    benchmark_symbol, bar_hours)
