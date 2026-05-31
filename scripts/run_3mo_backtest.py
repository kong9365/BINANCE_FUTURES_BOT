"""
scripts/run_3mo_backtest.py
=====================================================================
지난 3개월 백테스트 — 현재 백테스트 가능한 전략들을 "테스터"로 각각 실행.

★ 방법(룩어헤드 0): 1d 전체 데이터로 지표(EMA200 등) 워밍업 → **최근 N개월(기본 3) 거래만 평가**.
  ("3개월 원데이터만"은 EMA200=200봉 워밍업이 불가능 → 표준 OOS-partition 방식 채택.)
  파라미터 고정·최적화 0. 작은 표본(3개월)이라 n<200 이면 INSUFFICIENT_SAMPLE(과대해석 금지).

테스터(현재 엔진의 백테스트 가능 전략):
  - breakout : Donchian20+EMA200+ADX25, 진입 stop 2ATR, 고정 TP 4ATR (라이브 트렌드 전략 설정)
  - smc      : 셋업 B(구조+sweep+FVG/OB, 구조기반 청산)
  - trend_follow : 레짐 기반 추세추종(엔진 기본)
  각 테스터 × 체결모델(post_only 판정 / maker_open 비교).
  ※ oi_surge(라이브 기본)는 OI 이력 필요 → OHLCV 캐시로 백테스트 불가(보고서에 명시).

사용: python scripts/run_3mo_backtest.py [--months 3] [--max-syms 60] [--out docs/backtest_3month_report.json]
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
from analytics.core_strategy_validation import compute_metrics, to_dt  # noqa: E402

FILL_MODES = ["post_only", "maker_open"]   # post_only=판정 / maker_open=비교
_COMMON = dict(min_trailing_volume_usd=20_000_000.0, trailing_volume_bars=30,
               risk_per_trade_pct=0.005, max_concurrent_positions=5)

TESTERS = {
    "breakout": dict(strategy="breakout", long_only=False, breakout_donchian=20,
                     breakout_adx_min=25.0, breakout_ema=200, breakout_atr_stop=2.0,
                     breakout_atr_target=4.0, breakout_trail_exit=False, **_COMMON),
    "smc": dict(strategy="smc", long_only=False, **_COMMON),
    "trend_follow": dict(strategy="trend_follow", **_COMMON),
}


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


def _run(candles, strategy_params, fill_model):
    cfg = BacktestConfig(pairs=list(candles), entry_fill_model=fill_model, **strategy_params)
    return BacktestEngine(cfg).run(candles)


def _jsonable(m):
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in m.items() if k not in ("trades", "by_symbol")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--max-syms", type=int, default=60)
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "docs", "backtest_3month_report.json"))
    args = ap.parse_args()
    candles = _load_1d(args.max_syms)
    if not candles:
        print("데이터 없음"); return
    end = max(df.index.max() for df in candles.values())
    cutoff = to_dt(end) - timedelta(days=args.months * 30)

    print("=" * 84)
    print(f"지난 {args.months}개월 백테스트 — 평가구간 {cutoff.date()} ~ {to_dt(end).date()} | 1d {len(candles)}종목")
    print("지표 워밍업은 전체 데이터, 거래 평가는 최근 3개월만. post_only 기준 판정.")
    print("=" * 84)

    payload = {"window_start": str(cutoff.date()), "window_end": str(to_dt(end).date()),
               "n_symbols": len(candles), "testers": {}}

    print(f"\n{'tester':14}{'fill':12}{'n':>5}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}{'netR':>8}{'mdd%':>7}{'Shrp':>7}")
    for name, params in TESTERS.items():
        payload["testers"][name] = {}
        for fm in FILL_MODES:
            res = _run(candles, params, fm)
            m = compute_metrics(res.trades, res.equity_curve, cutoff)
            payload["testers"][name][fm] = _jsonable(m)
            if m["n"] == 0:
                print(f"{name:14}{fm:12}{0:>5}   (거래 0)")
                continue
            print(f"{name:14}{fm:12}{m['n']:>5}{m['win_rate']*100:>6.1f}%{m['pf']:>7.2f}"
                  f"{m['gross_R']:>8.3f}{m['cost_R']:>8.3f}{m['net_R']:>8.3f}{m['worst_mdd']:>6.1f}%{m['sharpe']:>7.2f}")
            if fm == "post_only":
                payload["testers"][name]["long"] = m["long"]
                payload["testers"][name]["short"] = m["short"]
                payload["testers"][name]["by_symbol_top"] = dict(
                    Counter(t.symbol for t in m["trades"]).most_common(6))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n결과 저장 → {args.out}")
    print("⚠ 3개월=소표본. n<200 이면 통계적으로 판정불가(과대해석 금지). 파라미터 무수정.")


if __name__ == "__main__":
    main()
