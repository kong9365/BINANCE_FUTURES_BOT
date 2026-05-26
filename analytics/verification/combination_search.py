"""전략 조합 자동 탐색 — 3-Gate 사전확정 임계 유지, PASS 발견까지 반복."""
from __future__ import annotations

import itertools
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from analytics.verification.data_source import list_local_symbols, load_universe_ohlcv
from analytics.verification.decile_analysis import decile_analysis
from analytics.verification.feature_engineering import (
    features_1d_breakout,
    features_cross_sectional_mom,
    forward_return_open,
)
from analytics.verification.gates import (
    GateResult,
    judge_backtest,
    judge_decile,
    judge_ic,
    overall_verdict,
)
from analytics.verification.ic_analysis import best_ic, run_ic_panel
from analytics.verification.simple_backtest import RT_COST, SimpleBTResult, _metrics_from_pnls
from strategy import breakout as bo

logger = logging.getLogger(__name__)

RESULTS_PATH = Path("docs/COMBINATION_SEARCH_RESULTS.json")
_FRAMES_CACHE: Dict[int, List[pd.DataFrame]] = {}


@dataclass
class ComboReport:
    combo_id: str
    name: str
    gate1: GateResult
    gate2: GateResult
    gate3: GateResult
    verdict: str
    backtest: dict
    notes: str = ""


def _gate_from_ic(ic: float) -> GateResult:
    val = 0.0 if ic is None or np.isnan(ic) else float(ic)
    return GateResult("IC", judge_ic(val), val, "Spearman IC (best feature)")


def _gate_from_decile(dec) -> GateResult:
    if dec is None:
        return GateResult("Decile", "fail", 0.0, "insufficient data")
    st = judge_decile(dec.spread_pct, dec.monotonicity)
    return GateResult("Decile", st, dec.spread_pct, f"mono={dec.monotonicity:.3f}")


def _gate_from_bt(bt: SimpleBTResult) -> GateResult:
    st = judge_backtest(
        bt.profit_factor, bt.n_trades, bt.max_drawdown_pct,
        bt.avg_net_pct, bt.symbol_positive_pct,
    )
    return GateResult(
        "Backtest", st, bt.profit_factor,
        f"n={bt.n_trades} MDD={bt.max_drawdown_pct:.1f}% avg={bt.avg_net_pct:.3f}%",
    )


def _report(combo_id: str, name: str, ic_rows, dec, bt: SimpleBTResult, notes: str = "") -> ComboReport:
    best = best_ic(ic_rows)
    g1 = _gate_from_ic(best.ic if best else float("nan"))
    g2 = _gate_from_decile(dec)
    g3 = _gate_from_bt(bt)
    return ComboReport(
        combo_id, name, g1, g2, g3,
        overall_verdict(g1.status, g2.status, g3.status),
        asdict(bt), notes,
    )


def _fast_breakout_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)
    don_hi = high.shift(1).rolling(20, min_periods=20).max()
    ema200 = close.ewm(span=200, adjust=False).mean()
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14, min_periods=14).mean()
    out = pd.DataFrame(index=df.index)
    out["donchian_position"] = (close - don_hi) / atr14.replace(0, np.nan)
    out["ema_distance"] = (close - ema200) / ema200.replace(0, np.nan)
    out["adx_14"] = (close.pct_change().rolling(14).std() * 100).fillna(0)
    out["volume_ratio"] = vol / vol.rolling(20, min_periods=20).mean().replace(0, np.nan)
    if "open" in df.columns:
        out["fwd_1d"] = forward_return_open(df["open"], 1)
        out["fwd_3d"] = forward_return_open(df["open"], 3)
        out["fwd_7d"] = forward_return_open(df["open"], 7)
    return out


def _fast_mom_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["momentum_30d"] = close / close.shift(30) - 1.0
    out["momentum_skip_30d"] = close.shift(7) / close.shift(30) - 1.0
    ret1d = close.pct_change()
    out["volatility_30d"] = ret1d.rolling(30, min_periods=30).std()
    out["risk_adjusted_momentum"] = (
        out["momentum_skip_30d"] / out["volatility_30d"].replace(0, np.nan)
    )
    if "open" in df.columns:
        out["fwd_7d"] = forward_return_open(df["open"], 7)
    return out


def btc_regime_series(btc_df: pd.DataFrame) -> pd.Series:
    """1d BTC 추세 게이트: close>EMA200 AND ADX>25."""
    close = btc_df["close"].astype(float)
    high = btc_df["high"].astype(float)
    low = btc_df["low"].astype(float)
    ema = close.ewm(span=200, adjust=False).mean()
    adx_vals = pd.Series(np.nan, index=btc_df.index)
    c_arr, h_arr, l_arr = close.to_numpy(), high.to_numpy(), low.to_numpy()
    for i in range(len(c_arr)):
        adx_vals.iloc[i] = bo.adx(h_arr[: i + 1], l_arr[: i + 1], c_arr[: i + 1], 14)
    trend = (close > ema) & (adx_vals > 25)
    return trend.rename("btc_regime_on")


def build_composite_frames(data: Dict[str, pd.DataFrame], *, use_cache: bool = True) -> List[pd.DataFrame]:
    """돌파 + 횡단면 모멘텀 복합 피처."""
    cache_key = id(data)
    if use_cache and cache_key in _FRAMES_CACHE:
        return _FRAMES_CACHE[cache_key]
    frames: List[pd.DataFrame] = []
    btc = data.get("BTCUSDT")
    regime = btc_regime_series(btc) if btc is not None and not btc.empty else None
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT"):
            continue
        b = _fast_breakout_features(df)
        m = _fast_mom_features(df).drop(columns=["fwd_7d"], errors="ignore")
        out = b.join(m, how="outer")
        out["composite_mom_breakout"] = (
            out["donchian_position"].rank(pct=True).fillna(0.5)
            + out["risk_adjusted_momentum"].rank(pct=True).fillna(0.5)
        ) / 2.0
        out["low_vol_momentum"] = out["risk_adjusted_momentum"] / (
            out["volatility_30d"].replace(0, np.nan) + 1e-9
        )
        out["mom_crash_7d"] = df["close"].pct_change(7)
        if regime is not None:
            out["btc_regime_on"] = regime.reindex(out.index).fillna(False).astype(float)
        if "open" in df.columns:
            out["fwd_7d"] = forward_return_open(df["open"], 7)
            out["fwd_1d"] = forward_return_open(df["open"], 1)
        frames.append(out.dropna(subset=["fwd_7d"], how="any"))
    if use_cache:
        _FRAMES_CACHE[cache_key] = frames
    return frames


def _breakout_signal_at(df, i: int, adx_min: float = 25.0, vol_min: float = 0.0) -> bool:
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    c = close[i]
    don_hi = max(high[i - 20:i])
    ema_v = bo.ema(close[: i + 1], 200)
    adx_v = bo.adx(high[: i + 1], low[: i + 1], close[: i + 1], 14)
    vol_avg = float(np.mean(vol[i - 20:i])) if i >= 20 else 0.0
    vol_ratio = vol[i] / vol_avg if vol_avg > 0 else 0.0
    if ema_v is None or c <= don_hi or c <= ema_v or adx_v <= adx_min:
        return False
    if vol_ratio < vol_min:
        return False
    return True


def backtest_breakout_filtered(
    data: Dict[str, pd.DataFrame],
    *,
    btc_regime: bool = False,
    adx_min: float = 25.0,
    vol_min: float = 0.0,
) -> SimpleBTResult:
    regime = None
    if btc_regime and "BTCUSDT" in data:
        regime = btc_regime_series(data["BTCUSDT"])
    all_pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT") or len(df) < 220:
            continue
        close = df["close"].to_numpy(float)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        open_ = df["open"].to_numpy(float)
        n = len(close)
        pnls: List[float] = []
        i = 200
        while i < n - 32:
            if regime is not None:
                ts = df.index[i]
                if not bool(regime.get(ts, False)):
                    i += 1
                    continue
            if not _breakout_signal_at(df, i, adx_min=adx_min, vol_min=vol_min):
                i += 1
                continue
            entry_i = i + 1
            if entry_i >= n:
                break
            entry = open_[entry_i]
            atr_v = bo.atr(high[: i + 1], low[: i + 1], close[: i + 1], 14)
            if atr_v <= 0 or entry <= 0:
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
            i = entry_i + 31
        if pnls:
            per_sym[sym] = pnls
            all_pnls.extend(pnls)
    return _metrics_from_pnls(all_pnls, per_sym)


def backtest_csm_variant(
    data: Dict[str, pd.DataFrame],
    *,
    lookback: int = 30,
    rebalance: int = 7,
    top_pct: float = 0.10,
    long_only: bool = False,
    btc_regime: bool = False,
) -> SimpleBTResult:
    from backtesting.cross_sectional import CSMConfig, run_cross_sectional

    regime = btc_regime_series(data["BTCUSDT"]) if btc_regime and "BTCUSDT" in data else None
    tiers = {sym: 3 for sym in data}
    merged = {}
    for sym, df in data.items():
        m = df.copy()
        if "funding_rate" not in m.columns:
            m["funding_rate"] = 0.0
        merged[sym] = m
    if len(merged) < 10:
        return SimpleBTResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
    cfg = CSMConfig(
        lookback_bars=lookback,
        rebalance_bars=rebalance,
        top_pct=top_pct,
        min_universe=10,
    )
    res = run_cross_sectional(merged, tiers, cfg=cfg, bar_hours=24.0)
    pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    for t in res.trades:
        if long_only and t.direction != 1:
            continue
        if regime is not None and not bool(regime.get(t.entry_ts, False)):
            continue
        pnl = t.pnl_usd / max(1.0, t.notional)
        pnls.append(pnl)
        per_sym.setdefault(t.symbol, []).append(pnl)
    out = _metrics_from_pnls(pnls, per_sym)
    out.n_trades = len(pnls)
    if pnls:
        wins = [p for p in pnls if p > 0]
        out.win_rate = 100.0 * len(wins) / len(pnls)
        losses = [p for p in pnls if p <= 0]
        out.profit_factor = (
            sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0
            else (999.0 if wins else 0.0)
        )
    return out


def backtest_fusion_top_decile(
    data: Dict[str, pd.DataFrame],
    *,
    feature: str = "composite_mom_breakout",
    hold_bars: int = 7,
    rebalance_bars: int = 7,
    top_pct: float = 0.10,
    btc_regime: bool = False,
) -> SimpleBTResult:
    """복합 스코어 상위 decile 롱, 주기 리밸런스."""
    frames = build_composite_frames(data)
    if not frames:
        return SimpleBTResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])
    panel_rows = []
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT"):
            continue
        f = _fast_breakout_features(df).join(_fast_mom_features(df).drop(columns=["fwd_7d"], errors="ignore"))
        f["symbol"] = sym
        f["composite_mom_breakout"] = (
            f["donchian_position"].rank(pct=True).fillna(0.5)
            + f["risk_adjusted_momentum"].rank(pct=True).fillna(0.5)
        ) / 2.0
        f["low_vol_momentum"] = f["risk_adjusted_momentum"] / (
            f["volatility_30d"].replace(0, np.nan) + 1e-9
        )
        panel_rows.append(f.reset_index().rename(columns={"ts": "ts"}))
    panel = pd.concat(panel_rows, ignore_index=True)
    panel["ts"] = pd.to_datetime(panel["ts"], utc=True)
    regime = btc_regime_series(data["BTCUSDT"]) if btc_regime and "BTCUSDT" in data else None
    all_pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    dates = sorted(panel["ts"].unique())
    for di in range(30, len(dates) - hold_bars - 1, rebalance_bars):
        ts = dates[di]
        if regime is not None and not bool(regime.get(ts, False)):
            continue
        snap = panel[panel["ts"] == ts].dropna(subset=[feature])
        if len(snap) < 10:
            continue
        k = max(1, int(round(len(snap) * top_pct)))
        picks = snap.nlargest(k, feature)["symbol"].tolist()
        entry_ts = dates[di + 1] if di + 1 < len(dates) else None
        exit_ts = dates[min(di + 1 + hold_bars, len(dates) - 1)]
        if entry_ts is None:
            continue
        for sym in picks:
            sdf = data.get(sym)
            if sdf is None or entry_ts not in sdf.index or exit_ts not in sdf.index:
                continue
            entry = float(sdf.loc[entry_ts, "open"])
            exit_p = float(sdf.loc[exit_ts, "open"])
            if entry <= 0:
                continue
            net = (exit_p / entry) - 1.0 - RT_COST
            all_pnls.append(net)
            per_sym.setdefault(sym, []).append(net)
    return _metrics_from_pnls(all_pnls, per_sym)


def backtest_mom_crash_bounce(
    data: Dict[str, pd.DataFrame],
    crash_pct: float = -0.15,
    hold_bars: int = 7,
) -> SimpleBTResult:
    """7d 급락 후 반등 롱."""
    all_pnls: List[float] = []
    per_sym: Dict[str, List[float]] = {}
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT") or len(df) < 40:
            continue
        close = df["close"].astype(float)
        open_ = df["open"].astype(float)
        ret7 = close.pct_change(7)
        pnls: List[float] = []
        for i in range(30, len(df) - hold_bars - 2):
            if ret7.iloc[i] > crash_pct:
                continue
            entry = float(open_.iloc[i + 1])
            exit_p = float(open_.iloc[i + 1 + hold_bars])
            if entry <= 0:
                continue
            pnls.append((exit_p / entry) - 1.0 - RT_COST)
        if pnls:
            per_sym[sym] = pnls
            all_pnls.extend(pnls)
    return _metrics_from_pnls(all_pnls, per_sym)


def backtest_breakout_csm_confirm(data: Dict[str, pd.DataFrame]) -> SimpleBTResult:
    """돌파 + CSM 상위 10% 동시 충족 시만 진입."""
    from backtesting.cross_sectional import CSMConfig, run_cross_sectional

    csm_cfg = CSMConfig(lookback_bars=30, rebalance_bars=7, top_pct=0.10, min_universe=10)
    csm_res = run_cross_sectional(
        {s: d.assign(funding_rate=0.0) if "funding_rate" not in d.columns else d for s, d in data.items()},
        {s: 3 for s in data},
        cfg=csm_cfg,
        bar_hours=24.0,
    )
    csm_longs = {t.symbol for t in csm_res.trades if t.direction == 1}
    bt = backtest_breakout_filtered(data)
    if not csm_longs:
        return bt
    filtered_pnls = []
    per_sym: Dict[str, List[float]] = {}
    for sym, ps in _group_pnls_by_symbol(bt, data).items():
        if sym in csm_longs:
            filtered_pnls.extend(ps)
            per_sym[sym] = ps
    return _metrics_from_pnls(filtered_pnls, per_sym)


def _group_pnls_by_symbol(bt: SimpleBTResult, data: Dict[str, pd.DataFrame]) -> Dict[str, List[float]]:
    """backtest_breakout_filtered per-symbol 재실행."""
    out: Dict[str, List[float]] = {}
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT"):
            continue
        r = backtest_breakout_filtered({sym: df})
        if r.pnls:
            out[sym] = r.pnls
    return out


def eval_from_frames(
    combo_id: str,
    name: str,
    frames: List[pd.DataFrame],
    ic_cols: List[str],
    decile_col: str,
    horizon: str,
    bt: SimpleBTResult,
    notes: str = "",
) -> ComboReport:
    hz_map = {"1d": "fwd_1d", "7d": "fwd_7d"}
    ic_rows = run_ic_panel(ic_cols, {horizon: hz_map.get(horizon, "fwd_7d")}, frames)
    pooled = pd.concat(frames, ignore_index=False) if frames else pd.DataFrame()
    dec = None
    if not pooled.empty and decile_col in pooled.columns:
        hz_col = hz_map.get(horizon, "fwd_7d")
        if hz_col in pooled.columns:
            dec = decile_analysis(pooled[decile_col], pooled[hz_col], decile_col, horizon)
    return _report(combo_id, name, ic_rows, dec, bt, notes)


def _phase3_refinement() -> List[Tuple[str, str, Callable]]:
    """3차 — 상위 CSM 변형 + IC 패널 필터."""
    return [
        ("csm_lb60_rb14_btc", "CSM lb60/rb14 + BTC게이트", lambda d: _eval_csm(d, lookback=60, rebalance=14, top_pct=0.10, btc_regime=True)),
        ("csm_lb60_rb14_top5", "CSM lb60/rb14 top5%", lambda d: _eval_csm(d, lookback=60, rebalance=14, top_pct=0.05)),
        ("csm_lb45_rb7_top5", "CSM lb45/rb7 top5%", lambda d: _eval_csm(d, lookback=45, rebalance=7, top_pct=0.05)),
        ("csm_lb28_rb5_top5", "CSM lb28/rb5 top5%", lambda d: _eval_csm(d, lookback=28, rebalance=5, top_pct=0.05)),
        ("csm_regime_ic_panel", "CSM IC(BTC ON only)", _eval_csm_regime_ic),
    ]


def _eval_csm_regime_ic(data) -> ComboReport:
    """BTC 추세 ON 구간만 IC/decile 재계산."""
    regime = btc_regime_series(data["BTCUSDT"]) if "BTCUSDT" in data else None
    frames = []
    for sym, df in data.items():
        if sym in ("BTCUSDT", "ETHUSDT"):
            continue
        f = _fast_mom_features(df)
        if regime is not None:
            f = f.loc[regime.reindex(f.index).fillna(False)]
        if not f.empty:
            frames.append(f)
    bt = backtest_csm_variant(data, lookback=60, rebalance=14, top_pct=0.10, btc_regime=True)
    return eval_from_frames(
        "csm_regime_ic_panel", "CSM regime IC", frames,
        ["risk_adjusted_momentum", "momentum_skip_30d"], "risk_adjusted_momentum", "7d", bt,
    )


def _catalog() -> List[Tuple[str, str, Callable]]:
    """(id, name, evaluator) — 1차 카탈로그."""
    return [
        ("breakout_btc_regime", "돌파+BTC추세게이트", lambda d: _eval_breakout(d, btc_regime=True)),
        ("breakout_vol12", "돌파+거래량1.2x", lambda d: _eval_breakout(d, vol_min=1.2)),
        ("breakout_adx30", "돌파+ADX30", lambda d: _eval_breakout(d, adx_min=30)),
        ("breakout_btc_vol", "돌파+BTC게이트+거래량", lambda d: _eval_breakout(d, btc_regime=True, vol_min=1.2)),
        ("csm_btc_regime", "CSM+BTC추세게이트", lambda d: _eval_csm(d, btc_regime=True)),
        ("csm_long_only", "CSM 롱온리", lambda d: _eval_csm(d, long_only=True)),
        ("csm_top5pct", "CSM 상위5%", lambda d: _eval_csm(d, top_pct=0.05)),
        ("csm_top20pct", "CSM 상위20%", lambda d: _eval_csm(d, top_pct=0.20)),
        ("csm_lb21_rb5", "CSM lb21/rb5", lambda d: _eval_csm(d, lookback=21, rebalance=5)),
        ("csm_lb60_rb14", "CSM lb60/rb14", lambda d: _eval_csm(d, lookback=60, rebalance=14)),
        ("fusion_top10", "복합스코어 상위10%", lambda d: _eval_fusion(d)),
        ("fusion_btc_regime", "복합+BTC게이트", lambda d: _eval_fusion(d, btc_regime=True)),
        ("fusion_lowvol_mom", "저변동 모멘텀", lambda d: _eval_fusion(d, feature="low_vol_momentum")),
        ("mom_crash_bounce", "7d급락 반등", lambda d: _eval_crash(d)),
        ("mom_crash_bounce_10", "7d−10% 반등", lambda d: _eval_crash(d, crash_pct=-0.10)),
        ("breakout_csm_confirm", "돌파+CSM확인", lambda d: _eval_confirm(d)),
        ("composite_ic", "복합 IC 단독", lambda d: _eval_composite_ic(d)),
    ]


def _eval_breakout(data, **kw) -> ComboReport:
    frames = [_fast_breakout_features(df) for sym, df in data.items() if sym not in ("BTCUSDT", "ETHUSDT")]
    bt = backtest_breakout_filtered(data, **kw)
    return eval_from_frames(
        f"breakout_{'_'.join(f'{k}{v}' for k, v in sorted(kw.items()))}" if kw else "breakout_base",
        "돌파 변형", frames,
        ["donchian_position", "ema_distance", "adx_14", "volume_ratio"],
        "donchian_position", "1d", bt,
    )


def _eval_csm(data, **kw) -> ComboReport:
    frames = [_fast_mom_features(df) for sym, df in data.items() if sym not in ("BTCUSDT", "ETHUSDT")]
    bt = backtest_csm_variant(data, **kw)
    tag = "_".join(f"{k}{v}" for k, v in sorted(kw.items()))
    return eval_from_frames(f"csm_{tag}", "CSM 변형", frames,
                            ["risk_adjusted_momentum", "momentum_skip_30d"], "risk_adjusted_momentum", "7d", bt)


def _eval_fusion(data, feature: str = "composite_mom_breakout", **kw) -> ComboReport:
    frames = build_composite_frames(data)
    bt = backtest_fusion_top_decile(data, feature=feature, **kw)
    tag = "_".join(f"{k}{v}" for k, v in sorted(kw.items())) or feature
    return eval_from_frames(
        f"fusion_{tag}", f"복합 {feature}", frames,
        [feature, "composite_mom_breakout", "low_vol_momentum"],
        feature, "7d", bt,
    )


def _eval_crash(data, crash_pct: float = -0.15) -> ComboReport:
    frames = build_composite_frames(data)
    bt = backtest_mom_crash_bounce(data, crash_pct=crash_pct)
    return eval_from_frames(
        f"crash_{int(abs(crash_pct)*100)}", "급락 반등",
        frames, ["mom_crash_7d"], "mom_crash_7d", "7d", bt,
    )


def _eval_confirm(data) -> ComboReport:
    frames = build_composite_frames(data)
    bt = backtest_breakout_csm_confirm(data)
    return eval_from_frames(
        "breakout_csm_confirm", "돌파+CSM확인", frames,
        ["composite_mom_breakout"], "composite_mom_breakout", "7d", bt,
    )


def _eval_composite_ic(data) -> ComboReport:
    frames = build_composite_frames(data)
    bt = backtest_fusion_top_decile(data)
    return eval_from_frames(
        "composite_ic", "복합 IC", frames,
        ["composite_mom_breakout", "low_vol_momentum", "donchian_position"],
        "composite_mom_breakout", "7d", bt,
    )


def _param_grid() -> List[Tuple[str, str, Callable]]:
    """2차 파라미터 그리드."""
    grid: List[Tuple[str, str, Callable]] = []
    for lb, rb, tp in itertools.product([21, 28, 30, 45], [5, 7, 10], [0.05, 0.10, 0.15, 0.20]):
        cid = f"csm_grid_lb{lb}_rb{rb}_tp{int(tp*100)}"
        grid.append((
            cid, f"CSM grid lb={lb} rb={rb} tp={tp}",
            lambda d, _lb=lb, _rb=rb, _tp=tp: _eval_csm(d, lookback=_lb, rebalance=_rb, top_pct=_tp),
        ))
    for adx in [22, 25, 28, 30]:
        for vol in [0.0, 1.0, 1.2, 1.5]:
            for br in [False, True]:
                cid = f"bo_adx{adx}_vol{vol}_btc{int(br)}"
                grid.append((
                    cid, f"돌파 adx={adx} vol={vol} btc={br}",
                    lambda d, _a=adx, _v=vol, _b=br: _eval_breakout(d, adx_min=_a, vol_min=_v, btc_regime=_b),
                ))
    for hold in [3, 5, 7, 10, 14]:
        for tp in [0.05, 0.10, 0.15]:
            cid = f"fusion_h{hold}_tp{int(tp*100)}"
            grid.append((
                cid, f"복합 hold={hold} tp={tp}",
                lambda d, _h=hold, _t=tp: _eval_fusion(
                    d, hold_bars=_h, rebalance_bars=_h, top_pct=_t,
                ),
            ))
    for crash in [-0.08, -0.10, -0.12, -0.15, -0.20, -0.25]:
        for hold in [3, 5, 7, 14]:
            cid = f"crash_{int(abs(crash)*100)}_h{hold}"
            grid.append((
                cid, f"급락 {crash} hold={hold}",
                lambda d, _c=crash, _h=hold: _eval_crash_hold(d, _c, _h),
            ))
    return grid


def _eval_crash_hold(data, crash_pct: float, hold: int) -> ComboReport:
    frames = build_composite_frames(data)
    bt = backtest_mom_crash_bounce(data, crash_pct=crash_pct, hold_bars=hold)
    return eval_from_frames(
        f"crash_h{hold}", "급락 반등", frames, ["mom_crash_7d"], "mom_crash_7d", "7d", bt,
        notes=f"crash={crash_pct} hold={hold}",
    )


def _score_report(r: ComboReport) -> Tuple[int, int, int]:
    g = [r.gate1.status, r.gate2.status, r.gate3.status]
    passed = sum(1 for s in g if s == "pass")
    border = sum(1 for s in g if s == "borderline")
    return passed, border, {"pass": 3, "borderline": 2, "fail": 0}[r.verdict] if r.verdict == "PASS" else passed


def load_data(local_only: bool = True) -> Dict[str, pd.DataFrame]:
    symbols = list_local_symbols("1d")
    data = load_universe_ohlcv(symbols, interval="1d", client=None, local_only=local_only)
    return data


def run_combination_search(
    *,
    phases: Optional[List[int]] = None,
    stop_on_pass: bool = True,
    local_only: bool = True,
) -> Dict[str, Any]:
    phases = phases or [1, 2]
    data = load_data(local_only=local_only)
    if len(data) < 10:
        raise RuntimeError(f"데이터 부족: symbols={len(data)}")
    logger.info("[ComboSearch] 피처 캐시 워밍업 (%d symbols)...", len(data))
    build_composite_frames(data)

    all_reports: List[ComboReport] = []
    winner: Optional[ComboReport] = None
    catalogs: List[List[Tuple[str, str, Callable]]] = []
    if 1 in phases:
        catalogs.append(_catalog())
    if 2 in phases:
        catalogs.append(_param_grid())
    if 3 in phases:
        catalogs.append(_phase3_refinement())

    for cat_idx, catalog in enumerate(catalogs, start=1):
        logger.info("[ComboSearch] Phase %d — %d combinations", cat_idx, len(catalog))
        for combo_id, name, fn in catalog:
            logger.info("[ComboSearch] testing %s", combo_id)
            try:
                rep = fn(data)
                rep.combo_id = combo_id
                rep.name = name
            except Exception as e:  # noqa: BLE001
                logger.warning("[ComboSearch] %s 실패: %s", combo_id, e)
                rep = ComboReport(
                    combo_id, name,
                    GateResult("IC", "fail", 0.0, str(e)),
                    GateResult("Decile", "fail", 0.0, ""),
                    GateResult("Backtest", "fail", 0.0, ""),
                    "FAIL", {}, notes=str(e),
                )
            all_reports.append(rep)
            if rep.verdict == "PASS":
                winner = rep
                logger.info("[ComboSearch] ★ PASS 발견: %s", combo_id)
                if stop_on_pass:
                    break
        if winner and stop_on_pass:
            break

    ranked = sorted(
        all_reports,
        key=lambda r: (
            r.verdict == "PASS",
            r.verdict == "CONDITIONAL",
            sum(1 for s in (r.gate1.status, r.gate2.status, r.gate3.status) if s == "pass"),
            sum(1 for s in (r.gate1.status, r.gate2.status, r.gate3.status) if s == "borderline"),
            r.gate1.value,
        ),
        reverse=True,
    )

    run_id = datetime.now(timezone.utc).strftime("combo_%Y%m%dT%H%M%SZ")
    payload = {
        "run_id": run_id,
        "symbols": len(data),
        "tested": len(all_reports),
        "winner": asdict(winner) if winner else None,
        "top10": [asdict(r) for r in ranked[:10]],
        "pass_count": sum(1 for r in all_reports if r.verdict == "PASS"),
        "conditional_count": sum(1 for r in all_reports if r.verdict == "CONDITIONAL"),
        "all_verdicts": {r.combo_id: r.verdict for r in all_reports},
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
