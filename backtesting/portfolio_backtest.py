"""
backtesting/portfolio_backtest.py
=====================================================================
신뢰 가능 포트폴리오 백테스트 코어 (검증 인프라 A1).

docs/STRATEGY_REALISM_REVIEW.md §"전면 재감사"의 Tier-1/2 맹점을 정면으로 고친다:
  - #1 코드 괴리: 지표(EMA/ATR/Wilder-ADX/Donchian)를 strategy/breakout.py 의 스칼라
    함수와 **수학적으로 동일**하게 단일패스 시리즈로 구현(tests 가 등가성 입증).
  - #2 미체결 낙관: post-only 무료 체결 가정 제거 → **테이커 진입**(다음 봉 시가 + 슬리피지).
  - #3 상관 착시: 종목 독립 합산이 아니라 **공유 자본 + 동시보유 제한 + MTM 자본곡선**
    으로 포트폴리오 MDD/Sharpe(상관 드로다운 반영).
  - Tier2: 펀딩 + 티어별 슬리피지 + **스톱 슬리피지(갭 통과)** + BTC 홀드 벤치마크.

성능: 지표 단일패스(O(n)) + 이벤트 루프 → 2년·수십종목 실용적(엔진 O(n^2) 회피).
룩어헤드 차단: 신호는 마감봉 i 기준, 진입은 i+1 봉 시가(엔진/라이브와 동일).

레짐 층화·파라미터 스윕·walk-forward 최적화는 본 코어 위에 별도로 얹는다(A2+).
=====================================================================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy.breakout import BreakoutConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 지표 — strategy/breakout.py 스칼라와 동일 수식의 단일패스 시리즈
# (등가성은 tests/test_portfolio_backtest.py 가 입증)
# ─────────────────────────────────────────────────────
def ema_series(closes: np.ndarray, period: int) -> np.ndarray:
    """SMA(period) 시드 후 표준 EMA. [i] = breakout.ema(closes[:i+1]) (i>=period-1)."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period:
        return out
    k = 2.0 / (period + 1)
    e = closes[:period].mean()
    out[period - 1] = e
    for i in range(period, n):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


def _true_ranges(highs, lows, closes) -> np.ndarray:
    n = len(closes)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    return tr


def atr_series(highs, lows, closes, period: int) -> np.ndarray:
    """Wilder ATR(SMA 시드). [i] = breakout.atr(...[:i+1]) (i>=period)."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    tr = _true_ranges(highs, lows, closes)
    a = tr[1:period + 1].mean()        # bars 1..period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + tr[i]) / period
        out[i] = a
    return out


def adx_series(highs, lows, closes, period: int) -> np.ndarray:
    """Wilder ADX. [i] = breakout.adx(...[:i+1]) (i>=2*period)."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n < 2 * period + 1:
        return out
    plus = np.zeros(n)
    minus = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus[i] = up if (up > dn and up > 0) else 0.0
        minus[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    # Wilder 합 평활 (breakout._wilder 와 동일): 첫 period 합, 이후 s=s-s/p+x
    def _wilder(arr):
        s = arr[1:period + 1].sum()
        res = [(period, s)]            # (index, value) — index 는 bar 위치
        for i in range(period + 1, n):
            s = s - s / period + arr[i]
            res.append((i, s))
        return res
    trs = _wilder(tr)
    ps = _wilder(plus)
    ms = _wilder(minus)
    dx_vals: List[Tuple[int, float]] = []
    for (idx, trv), (_, pv), (_, mv) in zip(trs, ps, ms):
        if trv <= 0:
            dx_vals.append((idx, 0.0))
            continue
        pdi = 100.0 * pv / trv
        mdi = 100.0 * mv / trv
        denom = pdi + mdi
        dx_vals.append((idx, 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0))
    # ADX = DX 의 Wilder 평균(첫 period 평균 시드)
    if len(dx_vals) < period:
        return out
    dxv = [v for _, v in dx_vals]
    a = sum(dxv[:period]) / period
    out[dx_vals[period - 1][0]] = a
    for j in range(period, len(dxv)):
        a = (a * (period - 1) + dxv[j]) / period
        out[dx_vals[j][0]] = a
    return out


def donchian_series(highs, lows, period: int) -> Tuple[np.ndarray, np.ndarray]:
    """직전 N봉(현재 제외) 채널. upper[i]=max(highs[i-period:i]) (i>=period)."""
    n = len(highs)
    up = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    for i in range(period, n):
        up[i] = highs[i - period:i].max()
        lo[i] = lows[i - period:i].min()
    return up, lo


def compute_signals(df: pd.DataFrame, cfg: BreakoutConfig) -> pd.DataFrame:
    """돌파 신호 컬럼 추가: signal(1 LONG / -1 SHORT / 0), atr.

    신호는 '마감봉 i' 기준(룩어헤드 없음). 진입은 호출자가 i+1 봉 시가에서 처리.
    breakout.evaluate_breakout 과 동일 조건(ADX 게이트 + 200EMA 편향 + Donchian 돌파).
    """
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    ema = ema_series(c, cfg.ema_period)
    atr = atr_series(h, l, c, cfg.atr_period)
    adx = adx_series(h, l, c, cfg.adx_period)
    up, lo = donchian_series(h, l, cfg.donchian_entry)
    sig = np.zeros(len(df), dtype=int)
    valid = ~(np.isnan(ema) | np.isnan(atr) | np.isnan(adx) | np.isnan(up))
    long_c = valid & (c > up) & (c > ema) & (adx >= cfg.adx_trend_min) & (atr > 0)
    short_c = valid & (c < lo) & (c < ema) & (adx >= cfg.adx_trend_min) & (atr > 0)
    sig[long_c] = 1
    sig[short_c] = -1
    out = df.copy()
    out["signal"] = sig
    out["atr"] = atr
    return out


# ─────────────────────────────────────────────────────
# 설정 / 결과
# ─────────────────────────────────────────────────────
@dataclass
class ExecConfig:
    taker_fee: float = 0.00045
    slippage_by_tier: Dict[int, float] = field(
        default_factory=lambda: {1: 0.00050, 2: 0.00100, 3: 0.00150})
    stop_slippage_pct: float = 0.0010   # 스톱 갭 통과 추가 슬리피지(편도)
    apply_funding: bool = True
    funding_interval_hours: float = 8.0


@dataclass
class PortfolioConfig:
    initial_capital: float = 1000.0
    max_concurrent: int = 3
    size_pct: float = 0.10              # 진입 명목 = 자본 × size_pct
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 4.0
    time_stop_bars: int = 48


@dataclass
class Trade:
    symbol: str
    direction: int
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float
    exit_price: float
    notional: float
    pnl_usd: float
    pnl_R: float
    fees_usd: float
    funding_usd: float
    exit_reason: str
    bars_held: int


@dataclass
class PortfolioResult:
    trades: List[Trade]
    equity_curve: List[Tuple[pd.Timestamp, float]]
    total_trades: int
    win_rate: float
    profit_factor: float
    expectancy_R: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    benchmark: Dict[str, float]


# ─────────────────────────────────────────────────────
# 포트폴리오 시뮬레이터
# ─────────────────────────────────────────────────────
class _OpenPos:
    __slots__ = ("symbol", "direction", "entry_ts", "entry_price", "notional",
                 "sl", "tp", "atr", "entry_pos", "tier", "funding_acc")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def run_portfolio(
    data: Dict[str, pd.DataFrame],
    tiers: Dict[str, int],
    breakout_cfg: Optional[BreakoutConfig] = None,
    exec_cfg: Optional[ExecConfig] = None,
    port_cfg: Optional[PortfolioConfig] = None,
    bar_hours: float = 1.0,
    benchmark_symbol: str = "BTCUSDT",
) -> PortfolioResult:
    """포트폴리오 백테스트 실행(공유 자본·동시보유 제한·MTM 자본곡선).

    data: {symbol: DataFrame[open,high,low,close,volume,(funding_rate)]} (UTC index).
    tiers: {symbol: 1|2|3} 슬리피지 티어.
    """
    breakout_cfg = breakout_cfg or BreakoutConfig()
    exec_cfg = exec_cfg or ExecConfig()
    port_cfg = port_cfg or PortfolioConfig()

    # 신호 사전 계산 + numpy 배열화
    prepared: Dict[str, dict] = {}
    all_ts: set = set()
    for sym, df in data.items():
        df = df.sort_index()
        sdf = compute_signals(df, breakout_cfg)
        prepared[sym] = {
            "ts": sdf.index.to_numpy(),
            "open": sdf["open"].to_numpy(float),
            "high": sdf["high"].to_numpy(float),
            "low": sdf["low"].to_numpy(float),
            "close": sdf["close"].to_numpy(float),
            "funding": (sdf["funding_rate"].to_numpy(float)
                        if "funding_rate" in sdf.columns else None),
            "signal": sdf["signal"].to_numpy(int),
            "atr": sdf["atr"].to_numpy(float),
            "pos_of_ts": {t: i for i, t in enumerate(sdf.index)},
        }
        all_ts.update(sdf.index)
    timeline = sorted(all_ts)

    equity = port_cfg.initial_capital
    open_pos: Dict[str, _OpenPos] = {}
    trades: List[Trade] = []
    equity_curve: List[Tuple[pd.Timestamp, float]] = []
    fund_periods = bar_hours / exec_cfg.funding_interval_hours

    def _exit(p: _OpenPos, exit_price: float, ts, reason: str, bars_held: int):
        nonlocal equity
        slip = exec_cfg.slippage_by_tier.get(p.tier, 0.0015)
        gross = p.direction * (exit_price - p.entry_price) / p.entry_price
        fees = (2 * exec_cfg.taker_fee + 2 * slip) * p.notional
        gross_usd = gross * p.notional
        pnl = gross_usd - fees - p.funding_acc
        equity += pnl
        risk = port_cfg.atr_stop_mult * p.atr / p.entry_price * p.notional
        trades.append(Trade(
            symbol=p.symbol, direction=p.direction, entry_ts=p.entry_ts, exit_ts=ts,
            entry_price=p.entry_price, exit_price=exit_price, notional=p.notional,
            pnl_usd=pnl, pnl_R=(pnl / risk if risk > 0 else 0.0),
            fees_usd=fees, funding_usd=p.funding_acc, exit_reason=reason,
            bars_held=bars_held,
        ))

    for ts in timeline:
        # 1) 보유 포지션 펀딩 누적 + 청산 체크
        for sym in list(open_pos.keys()):
            p = open_pos[sym]
            d = prepared[sym]
            i = d["pos_of_ts"].get(ts)
            if i is None:
                continue
            if exec_cfg.apply_funding and d["funding"] is not None:
                fr = d["funding"][i]
                if not math.isnan(fr):
                    p.funding_acc += p.direction * fr * p.notional * fund_periods
            hi, lo, cl = d["high"][i], d["low"][i], d["close"][i]
            bars_held = i - p.entry_pos
            ss = exec_cfg.stop_slippage_pct
            if p.direction == 1:
                if lo <= p.sl:
                    _exit(p, p.sl * (1 - ss), ts, "SL", bars_held); del open_pos[sym]; continue
                if hi >= p.tp:
                    _exit(p, p.tp * (1 - ss), ts, "TP", bars_held); del open_pos[sym]; continue
            else:
                if hi >= p.sl:
                    _exit(p, p.sl * (1 + ss), ts, "SL", bars_held); del open_pos[sym]; continue
                if lo <= p.tp:
                    _exit(p, p.tp * (1 + ss), ts, "TP", bars_held); del open_pos[sym]; continue
            if bars_held >= port_cfg.time_stop_bars:
                _exit(p, cl, ts, "TIME_STOP", bars_held); del open_pos[sym]; continue

        # 2) 신규 진입 — 직전 봉(i-1) 신호 → 이번 봉 시가 테이커 진입
        if len(open_pos) < port_cfg.max_concurrent:
            for sym, d in prepared.items():
                if sym in open_pos:
                    continue
                i = d["pos_of_ts"].get(ts)
                if i is None or i == 0:
                    continue
                s = d["signal"][i - 1]
                if s == 0:
                    continue
                if len(open_pos) >= port_cfg.max_concurrent:
                    break
                atr = d["atr"][i - 1]
                if math.isnan(atr) or atr <= 0:
                    continue
                tier = tiers.get(sym, 2)
                slip = exec_cfg.slippage_by_tier.get(tier, 0.0015)
                raw_open = d["open"][i]
                if raw_open <= 0:
                    continue
                # 테이커 진입: 시가에 슬리피지(롱은 비싸게, 숏은 싸게)
                entry = raw_open * (1 + s * slip)
                notional = equity * port_cfg.size_pct
                if notional <= 0:
                    continue
                if s == 1:
                    sl = entry - port_cfg.atr_stop_mult * atr
                    tp = entry + port_cfg.atr_target_mult * atr
                else:
                    sl = entry + port_cfg.atr_stop_mult * atr
                    tp = entry - port_cfg.atr_target_mult * atr
                if sl <= 0 or tp <= 0:
                    continue
                open_pos[sym] = _OpenPos(
                    symbol=sym, direction=int(s), entry_ts=ts, entry_price=entry,
                    notional=notional, sl=sl, tp=tp, atr=atr, entry_pos=i, tier=tier,
                    funding_acc=0.0,
                )

        # 3) MTM 자본(미실현 포함) 기록
        unreal = 0.0
        for sym, p in open_pos.items():
            i = prepared[sym]["pos_of_ts"].get(ts)
            if i is None:
                continue
            cl = prepared[sym]["close"][i]
            unreal += p.direction * (cl - p.entry_price) / p.entry_price * p.notional
        equity_curve.append((ts, equity + unreal))

    return _metrics(trades, equity_curve, port_cfg, data, benchmark_symbol, bar_hours)


def _metrics(trades, equity_curve, port_cfg, data, benchmark_symbol, bar_hours) -> PortfolioResult:
    n = len(trades)
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = abs(sum(t.pnl_usd for t in losses))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    exp_r = (sum(t.pnl_R for t in trades) / n) if n else 0.0
    init = port_cfg.initial_capital
    final = equity_curve[-1][1] if equity_curve else init
    total_ret = (final - init) / init * 100 if init > 0 else 0.0
    # MDD + Sharpe (자본곡선 기반)
    peak = init
    mdd = 0.0
    eq = [v for _, v in equity_curve]
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100 if peak > 0 else 0.0)
    rets = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq)) if eq[i - 1] > 0]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = var ** 0.5
        ppy = (365.0 * 24 / bar_hours)
        sharpe = (mean / std) * (ppy ** 0.5) if std > 0 else 0.0
    else:
        sharpe = 0.0
    # 벤치마크: BTC 홀드
    bench = {}
    bdf = data.get(benchmark_symbol)
    if bdf is not None and len(bdf) > 1:
        c0 = float(bdf["close"].iloc[0]); c1 = float(bdf["close"].iloc[-1])
        bench["btc_hold_return_pct"] = (c1 - c0) / c0 * 100 if c0 > 0 else 0.0
        bser = bdf["close"].to_numpy(float)
        bpeak = bser[0]; bmdd = 0.0
        for v in bser:
            bpeak = max(bpeak, v); bmdd = max(bmdd, (bpeak - v) / bpeak * 100)
        bench["btc_hold_mdd_pct"] = round(bmdd, 2)
    return PortfolioResult(
        trades=trades, equity_curve=equity_curve, total_trades=n,
        win_rate=round(len(wins) / n, 4) if n else 0.0,
        profit_factor=round(pf, 4) if pf != float("inf") else float("inf"),
        expectancy_R=round(exp_r, 4),
        total_return_pct=round(total_ret, 2),
        max_drawdown_pct=round(mdd, 2),
        sharpe=round(sharpe, 3),
        benchmark={k: round(v, 2) for k, v in bench.items()},
    )
