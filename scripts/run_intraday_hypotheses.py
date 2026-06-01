"""
scripts/run_intraday_hypotheses.py
=====================================================================
단기(15m) 가설 3종 동결 게이트 검증 — 검증 전용 (라이브 0, HOLD).

대상(단일 셋업·고정 파라미터·룩어헤드0):
  intra_breakout : strategy="breakout" 재사용 — Donchian20+EMA200+ADX25, 트레일 3ATR, time_stop 32봉(8h).
  vol_breakout   : Larry Williams 변동성 돌파 — 당일오픈 ± 0.5·전일레인지, TP 3ATR/SL 2ATR/ts 24봉.
  intra_mean_rev : 볼린저 평균회귀 — EMA200 필터 하 BB(20,2σ) 이탈, TP=BB중심/SL 1.5ATR/ts 48봉.

★ 범위 축소(운영자 결정): 엔진이 1d용 설계라 15m 풀스코프(30종목×4년×3체결)는 ~15h 불가.
  → 12종목(풀히스토리 다종목) × 최근 2년 × **post_only 판정 단일 체결**. 체결 bracket
  (post_only_strict/taker)은 *통과 후보에 한해 사후* 적용. 통계력 일부 약화(표본·기간·다종목·체결 축소).
★ 다중검정: 3 동시검정(우연통과↑) → PASS="1차 흥미"일 뿐. 1차(burn-in 후)+2차(후반 신선) 둘 다.
★ 성능: max_lookback_bars=1000 (룩어헤드0; EMA200은 윈도 근사 ~e-4 — 리포트 명시). funding=0(F1).
  동결 하네스/CostGuard 무수정. FAIL→폐기(튜닝 0).

사용: python scripts/run_intraday_hypotheses.py [--years 2] [--out docs/intraday_hypotheses_report.json]
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd

logging.disable(logging.WARNING)        # 레짐 워밍업 등 경고 억제(속도·가독)

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtesting.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from analytics.core_strategy_validation import (  # noqa: E402
    compute_metrics, evaluate_gates, to_dt,
)

WARMUP_DAYS = 14
FILL_JUDGE = "post_only"                 # 판정 체결모델(축소 스코프: 단일)
# 12종목 — 모두 풀히스토리(2022~2026) 다종목(cross-pair 다양성). established 메이저+대형알트.
SYMBOLS_12 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT", "NEARUSDT", "BCHUSDT",
]
_COMMON = dict(risk_per_trade_pct=0.005, max_concurrent_positions=5,
               min_trailing_volume_usd=0.0, max_lookback_bars=1000)

TESTERS = {
    "intra_breakout": dict(strategy="breakout", long_only=False,
                           breakout_donchian=20, breakout_ema=200, breakout_adx_min=25.0,
                           breakout_atr_stop=2.0, breakout_trail_exit=True,
                           breakout_trail_atr_mult=3.0, time_stop_bars=32, **_COMMON),
    "vol_breakout": dict(strategy="vol_breakout", long_only=False,
                         volbreak_k=0.5, volbreak_atr_period=14, volbreak_atr_stop_mult=2.0,
                         volbreak_atr_target_mult=3.0, time_stop_bars=24, **_COMMON),
    "intra_mean_rev": dict(strategy="intra_mean_rev", long_only=False,
                           intramr_bb_period=20, intramr_bb_std=2.0, intramr_ema_period=200,
                           intramr_atr_period=14, intramr_atr_stop_mult=1.5,
                           time_stop_bars=48, **_COMMON),
}


def _load_15m(symbols, years: float) -> dict:
    out = {}
    pat = os.path.join(PROJECT_ROOT, "backtests", "cache", "intraday", "*_15m.parquet")
    for p in sorted(glob.glob(pat)):
        sym = os.path.basename(p).replace("_15m.parquet", "")
        if sym not in symbols:
            continue
        d = pd.read_parquet(p)
        ts = d["ts"]
        raw = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) \
            else pd.to_datetime(ts, format="ISO8601")
        idx = pd.DatetimeIndex(raw)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        d = d.copy(); d.index = idx; d = d.sort_index()
        if years > 0:                                       # 최근 N년만(축소 스코프)
            d = d[d.index >= (d.index.max() - pd.Timedelta(days=int(years * 365)))]
        if "funding_rate" not in d.columns:
            d["funding_rate"] = 0.0
        out[sym] = d[[c for c in ["open", "high", "low", "close", "volume", "funding_rate"]
                      if c in d.columns]]
    return out


def _run(candles, params, fill_model):
    cfg = BacktestConfig(pairs=list(candles), entry_fill_model=fill_model, **params)
    return BacktestEngine(cfg).run(candles)


def _jsonable(m):
    return {k: v for k, v in m.items() if k != "trades"}


def _row(label, m, verdict):
    if m["n"] == 0:
        return f"{label:28}{0:>7}   (거래 0)"
    return (f"{label:28}{m['n']:>7}{m['win_rate']*100:>6.1f}%{m['pf']:>7.2f}"
            f"{m['gross_R']:>8.3f}{m['cost_R']:>8.3f}{m['net_R']:>8.3f}"
            f"{m['worst_mdd']:>7.1f}%{m['sharpe']:>7.2f}{m['cross_pair_positive']*100:>6.0f}%  {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "docs", "intraday_hypotheses_report.json"))
    args = ap.parse_args()
    t_load = time.time()
    candles = _load_15m(SYMBOLS_12, args.years)
    if not candles:
        print("데이터 없음"); return
    nbars = sum(len(v) for v in candles.values())
    start = min(df.index.min() for df in candles.values())
    end = max(df.index.max() for df in candles.values())
    cutoff1 = to_dt(start) + timedelta(days=WARMUP_DAYS)
    cutoff2 = cutoff1 + (to_dt(end) - cutoff1) / 2
    print(f"[load] {len(candles)}종목 {nbars:,}봉 {to_dt(start).date()}~{to_dt(end).date()} "
          f"({time.time()-t_load:.1f}s)", flush=True)
    print(f"1차 OOS>={cutoff1.date()} | 2차(신선) OOS>={cutoff2.date()} | 판정={FILL_JUDGE}", flush=True)
    print("★ 축소 스코프(12종목×최근2년×post_only). 3 동시검정 → PASS=1차흥미. funding=0(F1). HOLD.", flush=True)
    print(f"\n{'tester / window':28}{'n':>7}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}"
          f"{'netR':>8}{'w3moMDD':>8}{'Shrp':>7}{'xpair':>6}  verdict", flush=True)

    payload = {"oos_cutoff_1": str(cutoff1.date()), "oos_cutoff_2_fresh": str(cutoff2.date()),
               "n_symbols": len(candles), "symbols": list(candles), "years": args.years,
               "fill_judge": FILL_JUDGE,
               "note": "축소 스코프(12종목×2년×post_only). PASS=1차흥미. 승급=1차+2차 PASS. "
                       "체결 bracket(strict/taker)·풀스코프는 통과 후보에 한해 후속. funding=0. HOLD.",
               "testers": {}}

    promoted_any = []
    for name, params in TESTERS.items():
        t0 = time.time()
        res = _run(candles, params, FILL_JUDGE)
        m1 = compute_metrics(res.trades, res.equity_curve, cutoff1)
        m2 = compute_metrics(res.trades, res.equity_curve, cutoff2)
        v1, f1 = evaluate_gates(m1)
        v2, f2 = evaluate_gates(m2)
        promoted = (v1 == "PASS" and v2 == "PASS")
        if promoted:
            promoted_any.append(name)
        exit_mix = dict(Counter(t.exit_reason for t in m1["trades"]))
        print(_row(f"{name} 1차(full)", m1, v1), flush=True)
        print(_row(f"{name} 2차(fresh)", m2, v2), flush=True)
        if m1["n"] > 0:
            print(f"{'  exit_mix':28}{str(exit_mix)} | LONG {m1['long']['n']} / SHORT {m1['short']['n']}"
                  f"  [{time.time()-t0:.0f}s]", flush=True)
        if f1:
            print(f"{'  1차 미달':28}{', '.join(f1)}", flush=True)
        if f2:
            print(f"{'  2차 미달':28}{', '.join(f2)}", flush=True)
        payload["testers"][name] = {
            "verdict_1": v1, "failed_1": f1, "verdict_2_fresh": v2, "failed_2_fresh": f2,
            "promoted": promoted, "exit_mix": exit_mix, "elapsed_s": round(time.time() - t0, 1),
            "post_only_1": _jsonable(m1), "post_only_2_fresh": _jsonable(m2),
            "by_symbol_top": dict(Counter(t.symbol for t in m1["trades"]).most_common(8)),
        }

    print("\n" + "=" * 96, flush=True)
    if promoted_any:
        print(f"승급(1차+2차 PASS) = {promoted_any} → '잠정 흥미'. 단 라이브 아님 — "
              "체결 bracket(strict/taker)+풀스코프+펀딩 재검증 전까지 HOLD.", flush=True)
    else:
        print("승급 없음 → 모두 폐기(재시도/튜닝 금지). 실거래 HOLD 유지.", flush=True)
    print("③ 신규상장 빔: 생존편향(상폐코인 없음)+표본부족 → 검증불가(시도 안 함, 리포트 기록).", flush=True)
    print("=" * 96, flush=True)

    payload["promoted"] = promoted_any
    payload["listing_beam"] = "검증불가(데이터 한계: exchangeInfo=현재상장만, 상폐코인 0, 생존자~14≪200)"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"결과 저장 -> {args.out}", flush=True)
    print("⚠ 축소스코프·funding=0(F1)·EMA200 윈도근사·Sharpe·R1(HIGH_VOL)·생존편향 — 리포트 caveat 참조.", flush=True)


if __name__ == "__main__":
    main()
