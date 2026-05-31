"""
scripts/run_new_hypotheses.py
=====================================================================
새 가설 3종(faber / tsmom_ens / mean_rev) 동결 게이트 검증 — 검증 전용.

★ 단일 실행·파라미터 고정·최적화 0·룩어헤드 0(엔진 _get_candles_until <ts + _simulate_trade
  순방향). 동결 하네스(analytics/core_strategy_validation)는 수정 없이 재사용.
  FAIL 이어도 수정/재시도/필터추가/임계변경 금지 — 결과 저장 후 종료.

★ 다중검정 보정: 3개를 *동시* 검증 → 우연통과 위험↑. 그래서 모든 tester 에 대해
  1차(전체 OOS) + 2차(후반 '신선' 구간) 두 판정을 산출한다. PASS 는 "1차 흥미로움"일 뿐,
  "검증됨"이 아니다. 승급(잠정 흥미) = 1차 PASS AND 2차 PASS. 그래도 라이브는 HOLD —
  펀딩 포함 재검증(F1) + 2차 신선 OOS 둘 다 통과 전까지 신뢰 금지.

★ 한계(리포트 헤드라인): verification 캐시엔 funding 이 없어(=0) cost_R=수수료+슬리피지만.
  다주 롱(faber/tsmom)은 실제 펀딩비 과소계상 → FAIL은 견고하나 PASS는 잠정.

전략(고정 단일 셋업):
  faber          : LONG close>SMA200 / SHORT close<SMA200, 청산 MA(200)교차, 재난 SL 3ATR, TP 무.
  faber_long_only: 위의 롱 전용.
  tsmom_ens      : sign(ret21)+sign(ret63)+sign(ret126) 부호, 월보유(time_stop21), SL 3ATR/TP 6ATR.
  mean_rev       : SMA200 추세필터 하 RSI(2)<5 롱 / >95 숏, 고정 TP 1ATR/SL 2ATR/time_stop5.
  공통: 유니버스 ≥$20M, 사이징 0.5%·동시≤5, post_only 판정 / maker_open 비교.

사용: python scripts/run_new_hypotheses.py [--max-syms N] [--out docs/new_hypotheses_report.json]
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

WARMUP_DAYS = 365
_COMMON = dict(min_trailing_volume_usd=20_000_000.0, trailing_volume_bars=30,
               risk_per_trade_pct=0.005, max_concurrent_positions=5)

TESTERS = {
    "faber": dict(strategy="faber", long_only=False, faber_sma_period=200,
                  faber_atr_period=14, faber_atr_stop_mult=3.0,
                  exit_ma_period=200, time_stop_bars=100000, **_COMMON),
    "faber_long_only": dict(strategy="faber", long_only=True, faber_sma_period=200,
                            faber_atr_period=14, faber_atr_stop_mult=3.0,
                            exit_ma_period=200, time_stop_bars=100000, **_COMMON),
    "tsmom_ens": dict(strategy="tsmom_ens", long_only=False,
                      tsmom_lookbacks=(21, 63, 126), tsmom_atr_period=14,
                      tsmom_atr_stop_mult=3.0, tsmom_atr_target_mult=6.0,
                      time_stop_bars=21, exit_ma_period=0, **_COMMON),
    "mean_rev": dict(strategy="mean_rev", long_only=False, meanrev_sma_period=200,
                     meanrev_rsi_period=2, meanrev_rsi_long_below=5.0,
                     meanrev_rsi_short_above=95.0, meanrev_atr_period=14,
                     meanrev_atr_tp_mult=1.0, meanrev_atr_stop_mult=2.0,
                     time_stop_bars=5, exit_ma_period=0, **_COMMON),
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


def _run(candles, params, fill_model):
    cfg = BacktestConfig(pairs=list(candles), entry_fill_model=fill_model, **params)
    return BacktestEngine(cfg).run(candles)


def _jsonable(m):
    return {k: v for k, v in m.items() if k != "trades"}


def _row(label, m, verdict):
    if m["n"] == 0:
        return f"{label:20}{0:>6}   (거래 0)"
    return (f"{label:20}{m['n']:>6}{m['win_rate']*100:>6.1f}%{m['pf']:>7.2f}"
            f"{m['gross_R']:>8.3f}{m['cost_R']:>8.3f}{m['net_R']:>8.3f}"
            f"{m['worst_mdd']:>7.1f}%{m['sharpe']:>7.2f}{m['cross_pair_positive']*100:>6.0f}%"
            f"  {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=60)
    ap.add_argument("--out", default=os.path.join(PROJECT_ROOT, "docs", "new_hypotheses_report.json"))
    args = ap.parse_args()
    candles = _load_1d(args.max_syms)
    if not candles:
        print("데이터 없음"); return

    start = min(df.index.min() for df in candles.values())
    end = max(df.index.max() for df in candles.values())
    cutoff1 = to_dt(start) + timedelta(days=WARMUP_DAYS)          # 1차: 워밍업 이후 전체
    cutoff2 = cutoff1 + (to_dt(end) - cutoff1) / 2                # 2차: 후반 신선 구간

    print("=" * 96)
    print("새 가설 3종 검증 (faber / tsmom_ens / mean_rev) — 동결 게이트, 검증 전용")
    print(f"1d {len(candles)}종목 | 1차 OOS≥{cutoff1.date()} | 2차(신선) OOS≥{cutoff2.date()} | post_only 판정")
    print("★ 3개 동시검정(우연통과↑) → PASS=1차흥미일 뿐. 승급=1차+2차 PASS. funding=0(F1) → PASS는 잠정. 실거래 HOLD.")
    print("=" * 96)
    print(f"\n{'tester / window':20}{'n':>6}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}"
          f"{'netR':>8}{'w3moMDD':>8}{'Shrp':>7}{'xpair':>6}  verdict")

    payload = {"oos_cutoff_1": str(cutoff1.date()), "oos_cutoff_2_fresh": str(cutoff2.date()),
               "n_symbols": len(candles), "gates": "n>=200,gross_R>cost_R,PF>=1.2,Sharpe>=1.0,MDD<20,xpair>55",
               "note": "PASS=1차흥미(검증아님). 승급=1차+2차 PASS. funding=0 → PASS 잠정. HOLD.",
               "testers": {}}

    promoted_any = []
    for name, params in TESTERS.items():
        po = _run(candles, params, "post_only")
        mo = _run(candles, params, "maker_open")
        m1 = compute_metrics(po.trades, po.equity_curve, cutoff1)
        m2 = compute_metrics(po.trades, po.equity_curve, cutoff2)
        mk = compute_metrics(mo.trades, mo.equity_curve, cutoff1)
        v1, f1 = evaluate_gates(m1)
        v2, f2 = evaluate_gates(m2)
        promoted = (v1 == "PASS" and v2 == "PASS")
        if promoted:
            promoted_any.append(name)
        exit_mix = dict(Counter(t.exit_reason for t in m1["trades"]))

        print(_row(f"{name} 1차(full)", m1, v1))
        print(_row(f"{name} 2차(fresh)", m2, v2))
        print(_row(f"{name} maker(cmp)", mk, "-"))
        if m1["n"] > 0:
            print(f"{'  exit_mix':20}{str(exit_mix)}  | LONG n={m1['long']['n']} SHORT n={m1['short']['n']}")
        if f1:
            print(f"{'  1차 미달':20}{', '.join(f1)}")
        if f2:
            print(f"{'  2차 미달':20}{', '.join(f2)}")

        payload["testers"][name] = {
            "verdict_1": v1, "failed_1": f1, "verdict_2_fresh": v2, "failed_2_fresh": f2,
            "promoted": promoted, "exit_mix": exit_mix,
            "post_only_1": _jsonable(m1), "post_only_2_fresh": _jsonable(m2),
            "maker_open_1": _jsonable(mk),
            "by_symbol_top": dict(Counter(t.symbol for t in m1["trades"]).most_common(8)),
        }

    print("\n" + "=" * 96)
    if promoted_any:
        print(f"승급(1차+2차 PASS) = {promoted_any} → '잠정 흥미'. 단 라이브 아님 —")
        print("  펀딩 포함 재검증(F1) + 추가 확인 전까지 HOLD. PASS=검증됨 아님.")
    else:
        print("승급(1차+2차 PASS) 없음 → 모두 폐기(재시도/튜닝 금지). 실거래 HOLD 유지.")
    print("=" * 96)

    payload["promoted"] = promoted_any
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"결과 저장 → {args.out}")
    print("⚠ funding=0(F1)·Sharpe 저회전 부정확(D2)·R 스케일 차이(D1)·HIGH_VOL 차단(R1) — 리포트 caveat 참조.")


if __name__ == "__main__":
    main()
