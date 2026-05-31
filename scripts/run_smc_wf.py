"""
scripts/run_smc_wf.py
=====================================================================
SMC 후보 — "일봉 구조 SMC"(셋업 B) out-of-sample 정직 판정.

★ 사전확정(pre-registered) 기준 — 구현 후 변경 금지. 통과/실패/표본부족 그대로 보고.
   파라미터 스윕·그리드·"통과까지 손보기" 금지. 단일 셋업·단일 실행.

확정 스펙(운영자, 무수정): 1d / 셋업 B(trend + 직전3봉 sweep + FVG·OB 복귀) /
   청산 X(SL=sweep 극값 ∓0.1·ATR, TP=활성범위 반대측 SH/SL) / 롱+숏 대칭 /
   프리미티브 swing N=2·FVG≥0.25ATR·sweep K=3 / sizing 0.5%·동시≤5 / mid-liq(≥$20M) /
   post_only 현실 체결(+슬리피지·수수료·스톱슬리피지).

OOS 방식(Phase C 동일): 전체 1회 실행(워밍업 포함) → IS 12mo 이후를 OOS partition.
   (구조 trend 추적은 3mo 슬라이스에서 상태 소실 → 단일실행+partition 채택.)

사전확정 게이트(post_only 기준 통과해야 함):
   OOS Sharpe≥1.0 / Net PF≥1.2 / 최악 3mo OOS MDD<20% / 총 OOS 거래≥200 / 거래당 gross>cost
실패 구분: n<200=표본부족(판정불가, 답=데이터확장) / n≥200·PF<1.2=엣지미달(결론).

⚠ 1d 테스트 — 인트라데이 SMC 가 아니며 B-3(post_only 15초)도 해소 아님(한계).
사용: python scripts/run_smc_wf.py [--max-syms N]
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtesting.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402

GATE_SHARPE, GATE_PF, GATE_WORST_MDD, GATE_MIN_TRADES = 1.0, 1.2, 20.0, 200
WARMUP_MONTHS = 12
MODES = ["post_only", "maker_open"]   # post_only=판정 / maker_open=비교전용

PARAMS = dict(
    long_only=False,                                  # 롱+숏 대칭
    min_trailing_volume_usd=20_000_000.0, trailing_volume_bars=30,
    risk_per_trade_pct=0.005, max_concurrent_positions=5,
    smc_swing_n=2, smc_fvg_atr_mult=0.25, smc_sweep_lookback=3,
    smc_poi_lookback=10, smc_atr_period=14, smc_sl_atr_buffer=0.1,
)


def _load_1d(max_syms: int) -> dict:
    out = {}
    pat = os.path.join(PROJECT_ROOT, "backtests", "cache", "verification", "*_1d.parquet")
    for p in sorted(glob.glob(pat))[:max_syms]:
        sym = os.path.basename(p).replace("_1d.parquet", "")
        d = pd.read_parquet(p)
        ts = d["ts"]
        raw = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts)
        idx = pd.DatetimeIndex(raw)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        d = d.copy(); d.index = idx; d = d.sort_index()
        if "funding_rate" not in d.columns:
            d["funding_rate"] = 0.0
        out[sym] = d[[c for c in ["open", "high", "low", "close", "volume", "funding_rate"]
                      if c in d.columns]]
    return out


def self_dt(x):
    if hasattr(x, "to_pydatetime"):
        x = x.to_pydatetime()
    return x.replace(tzinfo=None) if getattr(x, "tzinfo", None) else x


def _run(candles: dict, fill_model: str):
    cfg = BacktestConfig(pairs=list(candles), strategy="smc",
                         entry_fill_model=fill_model, **PARAMS)
    return BacktestEngine(cfg).run(candles)


def _worst_3mo_mdd(eq: list) -> float:
    if len(eq) < 2:
        return 0.0
    worst, j = 0.0, 0
    for i in range(len(eq)):
        while eq[i][0] - eq[j][0] > timedelta(days=92):
            j += 1
        peak, win_mdd = eq[j][1], 0.0
        for k in range(j, i + 1):
            peak = max(peak, eq[k][1])
            if peak > 0:
                win_mdd = max(win_mdd, (peak - eq[k][1]) / peak * 100.0)
        worst = max(worst, win_mdd)
    return worst


def _side_stats(trades):
    """롱/숏 분리: (n, gross_R, net_R, win%)."""
    rk = [t for t in trades if t.risk_usdt > 0]
    if not rk:
        return dict(n=0, gross_R=0.0, net_R=0.0, win=0.0)
    g = sum(t.gross_pnl_usd / t.risk_usdt for t in rk) / len(rk)
    nr = sum(t.pnl_R for t in rk) / len(rk)
    w = sum(1 for t in rk if t.pnl_usd > 0) / len(rk)
    return dict(n=len(rk), gross_R=g, net_R=nr, win=w)


def _agg(result, cutoff) -> dict:
    trades = [t for t in result.trades if self_dt(t.entry_ts) >= cutoff]
    eq = [(self_dt(d), e) for d, e in result.equity_curve if self_dt(d) >= cutoff]
    n = len(trades)
    if n == 0:
        return dict(n=0, worst_mdd=_worst_3mo_mdd(eq), trades=[])
    wins = [t for t in trades if t.pnl_usd > 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = -sum(t.pnl_usd for t in trades if t.pnl_usd <= 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    rk = [t for t in trades if t.risk_usdt > 0]
    gross_R = sum(t.gross_pnl_usd / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    cost_R = sum((t.fees_usd + t.funding_usd) / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    rets = [eq[i][1] / eq[i - 1][1] - 1 for i in range(1, len(eq)) if eq[i - 1][1] > 0]
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    # 실현/계획 R:R 분포 — 구조 TP 가 R:R<1 로 깔리는지
    planned_rr = []
    for t in rk:
        risk = abs(t.entry_price - t.sl_price)
        rr = abs(t.tp_price - t.entry_price) / risk if risk > 0 else 0.0
        planned_rr.append(rr)
    planned_rr.sort()
    rr_med = planned_rr[len(planned_rr) // 2] if planned_rr else 0.0
    rr_below1 = sum(1 for x in planned_rr if x < 1.0) / len(planned_rr) if planned_rr else 0.0
    return dict(
        n=n, win_rate=len(wins) / n, pf=pf, gross_R=gross_R, cost_R=cost_R,
        net_R=sum(t.pnl_R for t in trades) / n,
        avg_win=(sum(t.pnl_R for t in wins) / len(wins) if wins else 0.0),
        avg_loss=(sum(t.pnl_R for t in trades if t.pnl_usd <= 0) /
                  max(1, n - len(wins))),
        sharpe=sharpe, worst_mdd=_worst_3mo_mdd(eq),
        long=_side_stats([t for t in trades if t.action == "LONG"]),
        short=_side_stats([t for t in trades if t.action == "SHORT"]),
        rr_med=rr_med, rr_below1=rr_below1, trades=trades,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=60)
    args = ap.parse_args()
    candles = _load_1d(args.max_syms)
    if not candles:
        print("데이터 없음"); return
    start = min(df.index.min() for df in candles.values())
    cutoff = self_dt(start) + timedelta(days=365)

    print("=" * 78)
    print("SMC 후보 — 일봉 구조 SMC (셋업 B: 구조+sweep+FVG/OB, 롱+숏, 구조기반 청산)")
    print(f"데이터 1d {len(candles)}종목 | OOS = {cutoff.date()} 이후(워밍업 12mo) | post_only 판정")
    print("⚠ 1d — 인트라데이 SMC 아님, B-3(15초) 미해소(한계).")
    print(f"게이트: Sharpe≥{GATE_SHARPE}/PF≥{GATE_PF}/최악3moMDD<{GATE_WORST_MDD}%/n≥{GATE_MIN_TRADES}/gross>cost")
    print("=" * 78)

    by = {m: _agg(_run(candles, m), cutoff) for m in MODES}

    print(f"\n{'mode':14}{'n':>6}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}{'netR':>8}{'w3moMDD':>9}{'Shrp':>7}")
    for m in MODES:
        a = by[m]
        if a["n"] == 0:
            print(f"{m:14}{0:>6}   (거래 0, MDD {a['worst_mdd']:.1f}%)"); continue
        print(f"{m:14}{a['n']:>6}{a['win_rate']*100:>6.1f}%{a['pf']:>7.2f}"
              f"{a['gross_R']:>8.3f}{a['cost_R']:>8.3f}{a['net_R']:>8.3f}{a['worst_mdd']:>8.1f}%{a['sharpe']:>7.2f}")

    a = by["post_only"]
    print("\n" + "=" * 78)
    if a["n"] == 0:
        print("판정 : 표본부족(판정불가) — OOS 거래 0. 답=데이터확장(인트라데이/코인↑).")
        print("=" * 78); return
    # 롱/숏 분리
    print(f"롱/숏 분리(post_only): LONG n={a['long']['n']} grossR={a['long']['gross_R']:.3f} "
          f"netR={a['long']['net_R']:.3f} win={a['long']['win']*100:.1f}% | "
          f"SHORT n={a['short']['n']} grossR={a['short']['gross_R']:.3f} "
          f"netR={a['short']['net_R']:.3f} win={a['short']['win']*100:.1f}%")
    print(f"실현 R:R: 계획 R:R 중앙값 {a['rr_med']:.2f}, R:R<1 비율 {a['rr_below1']*100:.1f}% "
          f"(구조 TP 가 R:R<1 로 깔리는지) | avg_win {a['avg_win']:.2f}R avg_loss {a['avg_loss']:.2f}R")
    print("코인별 거래수(상위8):", dict(Counter(t.symbol for t in a["trades"]).most_common(8)))
    # 윈도우(연도)별 net_R
    yr = {}
    for t in a["trades"]:
        y = self_dt(t.entry_ts).year
        yr.setdefault(y, []).append(t.pnl_R)
    print("연도별 (n, 평균netR):", {y: (len(v), round(sum(v) / len(v), 3)) for y, v in sorted(yr.items())})

    print("\n사전확정 판정 (post_only):")
    if a["n"] < GATE_MIN_TRADES:
        v = (f"표본부족(판정불가) — n={a['n']}<{GATE_MIN_TRADES}. 답은 튜닝이 아니라 "
             "데이터 확장(인트라데이 백필·코인↑). 파라미터 무수정.")
    else:
        ok = (a["sharpe"] >= GATE_SHARPE and a["pf"] >= GATE_PF
              and a["worst_mdd"] < GATE_WORST_MDD and a["gross_R"] > a["cost_R"])
        if ok:
            v = "★ 통과 — 소액 testnet 후보 (단 1d라 인트라데이/B-3 는 별도)"
        else:
            f = []
            if a["sharpe"] < GATE_SHARPE: f.append(f"Sharpe {a['sharpe']:.2f}<{GATE_SHARPE}")
            if a["pf"] < GATE_PF: f.append(f"PF {a['pf']:.2f}<{GATE_PF}")
            if a["worst_mdd"] >= GATE_WORST_MDD: f.append(f"MDD {a['worst_mdd']:.1f}≥{GATE_WORST_MDD}")
            if a["gross_R"] <= a["cost_R"]: f.append(f"grossR {a['gross_R']:.3f}≤costR {a['cost_R']:.3f}")
            v = f"엣지미달(결론) — n={a['n']}≥{GATE_MIN_TRADES}인데 {', '.join(f)}"
    print(f"  → {v}")
    print("=" * 78)
    print("파라미터 무수정(사전확정). maker_open 은 비교 전용(최종 판정 아님 — post_only 기준).")


if __name__ == "__main__":
    main()
