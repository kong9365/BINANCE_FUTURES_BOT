"""Gate 3 — Cost-aware simple backtest (RT 0.113% 고정)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from strategy import breakout as bo


RT_COST = 0.00113  # 0.113%


@dataclass
class SimpleBTResult:
    n_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    avg_net_pct: float
    symbol_positive_pct: float
    pnls: List[float] = field(default_factory=list)


def _metrics_from_pnls(pnls: List[float], per_sym: Dict[str, List[float]]) -> SimpleBTResult:
    if not pnls:
        return SimpleBTResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (999.0 if wins else 0.0)
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    mdd = float((peak - eq).max() / max(1e-9, peak.max()) * 100.0)
    sym_pos = sum(1 for ps in per_sym.values() if ps and sum(ps) > 0)
    sym_total = len(per_sym)
    return SimpleBTResult(
        len(pnls),
        100.0 * len(wins) / len(pnls),
        float(pf),
        mdd,
        float(np.mean(pnls) * 100.0),
        100.0 * sym_pos / sym_total if sym_total else 0.0,
        list(pnls),
    )


def backtest_1d_breakout(df, symbol: str = "") -> SimpleBTResult:
    if len(df) < 220:
        return SimpleBTResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    n = len(close)
    pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {symbol: []} if symbol else {}
    i = 200
    while i < n - 32:
        c = close[i]
        don_hi = max(high[i - 20:i])
        ema_v = bo.ema(close[: i + 1], 200)
        adx_v = bo.adx(high[: i + 1], low[: i + 1], close[: i + 1], 14)
        if ema_v is None or c <= don_hi or c <= ema_v or adx_v <= 25:
            i += 1
            continue
        entry_i = i + 1
        if entry_i >= n:
            break
        entry = open_[entry_i]
        atr_v = bo.atr(high[: i + 1], low[: i + 1], close[: i + 1], 14)
        if atr_v <= 0:
            i += 1
            continue
        sl = entry - 2.0 * atr_v
        r = entry - sl
        tp1 = entry + 1.5 * r
        tp2 = entry + 3.0 * r
        exit_price = close[min(n - 1, entry_i + 30)]
        for j in range(entry_i, min(n, entry_i + 31)):
            lo, hi = low[j], high[j]
            if lo <= sl:
                exit_price = sl
                break
            if hi >= tp2:
                exit_price = 0.5 * tp1 + 0.3 * tp2 + 0.2 * max(tp2, close[j])
                break
            if hi >= tp1:
                exit_price = 0.5 * tp1 + 0.5 * close[j]
            else:
                exit_price = close[j]
        net = (exit_price / entry) - 1.0 - RT_COST
        pnls.append(net)
        if symbol:
            per_sym.setdefault(symbol, []).append(net)
        i = entry_i + 31
    return _metrics_from_pnls(pnls, per_sym)


def backtest_1d_breakout_universe(data: Dict[str, object]) -> SimpleBTResult:
    all_pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT"):
            continue
        r = backtest_1d_breakout(df, sym)
        all_pnls.extend(r.pnls)
        if r.pnls:
            per_sym[sym] = list(r.pnls)
    return _metrics_from_pnls(all_pnls, per_sym)


def backtest_listing_fade(
    df: pd.DataFrame,
    onboard: pd.Timestamp,
    symbol: str = "",
) -> SimpleBTResult:
    """후보 5 Gate 3 — 상장 1주 +20% 과열 후 7일 숏 페이드.

    트리거(사전확정): 7<=days<=45 AND first_week_return>20%
    진입: next open SHORT, 7d open cover. RT 0.113%.
    """
    from analytics.verification.feature_engineering import features_listing_effect

    feat = features_listing_effect(df, onboard)
    if feat.empty or "open" not in df.columns:
        return SimpleBTResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
    open_ = df["open"].reindex(feat.index).astype(float)
    pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {symbol: []} if symbol else {}
    i = 0
    n = len(feat)
    while i < n - 8:
        row = feat.iloc[i]
        if row["days_since_listing"] < 7 or row["days_since_listing"] > 45:
            i += 1
            continue
        fwr = row.get("first_week_return")
        if fwr is None or np.isnan(fwr) or fwr <= 0.20:
            i += 1
            continue
        entry_i = i + 1
        exit_i = entry_i + 7
        if exit_i >= n:
            break
        entry = float(open_.iloc[entry_i])
        exit_p = float(open_.iloc[exit_i])
        if entry <= 0:
            i += 1
            continue
        gross = -(exit_p / entry - 1.0)  # short
        net = gross - RT_COST
        pnls.append(net)
        if symbol:
            per_sym.setdefault(symbol, []).append(net)
        i = exit_i + 1
    return _metrics_from_pnls(pnls, per_sym)


def backtest_listing_universe(
    data: Dict[str, object],
    onboard_map: Dict[str, pd.Timestamp],
) -> SimpleBTResult:
    all_pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    skip = {"BTCUSDT", "ETHUSDT"} | __import__(
        "analytics.verification.listing_utils", fromlist=["protected_symbols"]
    ).protected_symbols()
    for sym, df in data.items():
        if sym in skip:
            continue
        od = onboard_map.get(sym)
        if od is None:
            continue
        r = backtest_listing_fade(df, od, sym)
        all_pnls.extend(r.pnls)
        if r.pnls:
            per_sym[sym] = list(r.pnls)
    return _metrics_from_pnls(all_pnls, per_sym)


def backtest_cross_sectional(data: Dict[str, object], tiers: Dict[str, int] | None = None) -> SimpleBTResult:
    from backtesting.cross_sectional import CSMConfig, run_cross_sectional

    tiers = dict(tiers or {})
    for sym in data:
        tiers.setdefault(sym, 3)
    merged = {}
    for sym, df in data.items():
        m = df.copy()
        if "funding_rate" not in m.columns:
            m["funding_rate"] = 0.0
        merged[sym] = m
    cfg = CSMConfig(lookback_bars=30, rebalance_bars=7, top_pct=0.10, min_universe=10)
    res = run_cross_sectional(merged, tiers, cfg=cfg, bar_hours=24.0)
    pnls = [t.pnl_usd / max(1.0, t.notional) for t in res.trades]
    per_sym: Dict[str, List[float]] = {}
    for t in res.trades:
        per_sym.setdefault(t.symbol, []).append(t.pnl_usd / max(1.0, t.notional))
    out = _metrics_from_pnls(pnls, per_sym)
    out.n_trades = res.total_trades
    out.win_rate = res.win_rate * 100.0
    out.profit_factor = res.profit_factor
    out.max_drawdown_pct = res.max_drawdown_pct
    return out
