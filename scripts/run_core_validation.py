"""
scripts/run_core_validation.py
=====================================================================
Track B — 1d TSMOM/Donchian 코어(롱+숏 대칭) 비용 반영 후 +EV 검증.

★ 단일 실행·파라미터 고정·최적화 0·룩어헤드 0(엔진 _get_candles_until <ts + _simulate_trade
  순방향, Chandelier 트레일은 봉 t 고/저만 사용). OOS = 워밍업 12mo 이후 partition.
  FAIL 이어도 수정/재시도/필터추가/임계변경 금지 — 결과 저장 후 종료.

전략(고정): Donchian20 / EMA200 / ATR14 / 진입 stop 2.0ATR / Chandelier 트레일 3.0ATR(고정 TP 없음)
  LONG  = Close>DonchianUpper20 AND Close>EMA200 AND BTC risk-on(>200EMA, BTC_RISK_OFF==OFF)
  SHORT = Close<DonchianLower20 AND Close<EMA200 AND BTC risk-off(≤200EMA, BTC_RISK_OFF==ON)
  유니버스 mid-liq(≥$20M, 보호종목 제외) / 사이징 0.5%·동시≤5 / post_only 진입·시장가 청산.

판정(post_only): n≥200 / gross_R>cost_R / PF≥1.2 / Sharpe≥1.0 / 최악3moMDD<20% / cross-pair+>55%.
사용: python scripts/run_core_validation.py [--max-syms N] [--out docs/core_validation_report.json]
"""

from __future__ import annotations

import argparse
import glob
import json
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
from analytics.core_strategy_validation import (  # noqa: E402
    compute_metrics, evaluate_gates, to_dt,
)

MODES = ["post_only", "maker_open"]   # post_only=판정 / maker_open=비교전용
WARMUP_DAYS = 365

PARAMS = dict(
    long_only=False,                                  # 롱+숏 대칭
    macro_btc_ema_period=200,                         # 대칭 BTC_RISK_OFF 게이트
    breakout_donchian=20, breakout_ema=200, breakout_adx_min=0.0,  # ADX 게이트 없음(스펙)
    breakout_atr_stop=2.0, breakout_trail_exit=True, breakout_trail_atr_mult=3.0,
    min_trailing_volume_usd=20_000_000.0, trailing_volume_bars=30,
    risk_per_trade_pct=0.005, max_concurrent_positions=5,
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


def _run(candles, fill_model):
    cfg = BacktestConfig(pairs=list(candles), strategy="breakout",
                         entry_fill_model=fill_model, **PARAMS)
    return BacktestEngine(cfg).run(candles)


def _jsonable(m):
    """compute_metrics 결과를 JSON 직렬화 가능 형태로(Trade 객체 제외)."""
    return {k: v for k, v in m.items() if k not in ("trades",)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=60)
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "docs", "core_validation_report.json"))
    args = ap.parse_args()
    candles = _load_1d(args.max_syms)
    if not candles:
        print("데이터 없음"); return
    start = min(df.index.min() for df in candles.values())
    cutoff = to_dt(start) + timedelta(days=WARMUP_DAYS)

    print("=" * 80)
    print("Track B — 1d TSMOM/Donchian 코어 검증 (롱+숏 대칭, Chandelier, post_only)")
    print(f"데이터 1d {len(candles)}종목 | OOS = {cutoff.date()} 이후(워밍업 12mo) | post_only 판정")
    print("=" * 80)

    results = {}
    for m in MODES:
        res = _run(candles, m)
        results[m] = compute_metrics(res.trades, res.equity_curve, cutoff)

    print(f"\n{'mode':14}{'n':>6}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}{'netR':>8}{'w3moMDD':>9}{'Shrp':>7}{'xpair+':>8}")
    for m in MODES:
        a = results[m]
        if a["n"] == 0:
            print(f"{m:14}{0:>6}  (거래 0)"); continue
        print(f"{m:14}{a['n']:>6}{a['win_rate']*100:>6.1f}%{a['pf']:>7.2f}{a['gross_R']:>8.3f}"
              f"{a['cost_R']:>8.3f}{a['net_R']:>8.3f}{a['worst_mdd']:>8.1f}%{a['sharpe']:>7.2f}"
              f"{a['cross_pair_positive']*100:>7.0f}%")

    a = results["post_only"]
    verdict, failed = evaluate_gates(a)
    print("\n" + "=" * 80)
    print(f"판정 (post_only): {verdict}")
    if failed:
        print("  미달:", ", ".join(failed))
    if a["n"] > 0:
        print(f"  LONG  n={a['long']['n']} grossR={a['long']['gross_R']:.3f} netR={a['long']['net_R']:.3f} win={a['long']['win']*100:.1f}%")
        print(f"  SHORT n={a['short']['n']} grossR={a['short']['gross_R']:.3f} netR={a['short']['net_R']:.3f} win={a['short']['win']*100:.1f}%")
        print(f"  avg_trade={a['avg_trade_R']:.3f}R | cross-pair+ {a['cross_pair_positive']*100:.0f}% | 연도별 {a['by_year']}")
        print("  코인별 거래수(상위8):", dict(Counter(t.symbol for t in a["trades"]).most_common(8)))
    print("=" * 80)

    payload = {
        "verdict": verdict, "failed_gates": failed,
        "params": PARAMS, "oos_cutoff": str(cutoff.date()), "n_symbols": len(candles),
        "post_only": _jsonable(results["post_only"]),
        "maker_open": _jsonable(results["maker_open"]),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"결과 저장 → {args.out}")


if __name__ == "__main__":
    main()
