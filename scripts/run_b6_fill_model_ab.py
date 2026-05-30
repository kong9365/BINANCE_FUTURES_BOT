"""
scripts/run_b6_fill_model_ab.py
=====================================================================
B-6 [B-3] — 진입 체결 모델 A/B 리포트.

목표: 백테스트를 라이브와 "같은 게임"으로 — maker_open(legacy, 봉 open 확정체결)
vs post_only(라이브 GTX, limit=신호봉 close 닿아야 체결) vs taker(open 확정+taker)
를 *동일 신호셋·기간*으로 비교해, 체결률·거래수·승률·PF·expectancy 델타를 본다.

데이터(실측, backtests/cache):
  - daily_tsmom : verification/*_1d.parquet (mainnet 1d, ~3년)
  - breakout    : *_1h.csv (mainnet 1h)
  - oi_surge    : *_1h.csv (open_interest 포함)

⚠ 한계(반드시 명시): post_only fill% 는 봉(1d/1h) ≫ 라이브 15초 타임아웃이라
   *체결률 상한* 추정이다. 즉 "가장 후하게 줘도 엣지가 깨지면" 결론이 견고하다는
   방향 테스트이며, 정밀 캘리브레이션은 B-8(testnet 실체결) 몫이다.

사용: python scripts/run_b6_fill_model_ab.py [--max-syms N]
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtesting.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402

MODELS = ["maker_open", "post_only", "post_only_strict", "taker"]
_OHLC_COLS = ["open", "high", "low", "close", "volume", "funding_rate", "open_interest"]


def _to_dt_index(df: pd.DataFrame, tscol: str) -> pd.DataFrame:
    """ts/timestamp 컬럼을 UTC DatetimeIndex 로 — epoch ms 또는 ISO 자동 판별."""
    s = df[tscol]
    if pd.api.types.is_numeric_dtype(s):
        idx = pd.to_datetime(s, unit="ms", utc=True)
    else:
        idx = pd.to_datetime(s, utc=True)
    out = df.copy()
    out.index = idx
    out = out.sort_index()
    if "funding_rate" not in out.columns:
        out["funding_rate"] = 0.0
    keep = [c for c in _OHLC_COLS if c in out.columns]
    return out[keep]


def load_parquet_1d(max_syms: int) -> dict:
    out = {}
    pat = os.path.join(PROJECT_ROOT, "backtests", "cache", "verification", "*_1d.parquet")
    for p in sorted(glob.glob(pat))[:max_syms]:
        sym = os.path.basename(p).replace("_1d.parquet", "")
        out[sym] = _to_dt_index(pd.read_parquet(p), "ts")
    return out


def load_csv_1h(max_syms: int) -> dict:
    out = {}
    pat = os.path.join(PROJECT_ROOT, "backtests", "cache", "*_1h.csv")
    for p in sorted(glob.glob(pat))[:max_syms]:
        sym = os.path.basename(p).replace("_1h.csv", "")
        out[sym] = _to_dt_index(pd.read_csv(p), "timestamp")
    return out


def run_models(strategy: str, candles: dict, **cfg_kw) -> dict:
    res = {}
    for m in MODELS:
        cfg = BacktestConfig(
            pairs=list(candles), strategy=strategy, entry_fill_model=m, **cfg_kw
        )
        res[m] = BacktestEngine(cfg).run(candles)
    return res


def report(name: str, res: dict) -> None:
    base = res["maker_open"].total_trades or 1
    print(f"\n=== {name} ===")
    print(f"{'model':12}{'trades':>8}{'fill%':>8}{'win%':>8}{'PF':>8}{'expR':>9}{'ret%':>9}")
    for m in MODELS:
        r = res[m]
        fill = 100.0 * r.total_trades / base
        print(
            f"{m:12}{r.total_trades:>8}{fill:>7.0f}%{r.win_rate * 100:>7.1f}%"
            f"{r.profit_factor:>8.2f}{r.expectancy_R:>9.3f}{r.total_return_pct:>8.1f}%"
        )
    pf_legacy = res["maker_open"].profit_factor
    pf_post = res["post_only"].profit_factor
    print(
        f"  Δ(post_only−legacy): 체결률 {100.0*res['post_only'].total_trades/base:.0f}%, "
        f"PF {pf_post - pf_legacy:+.2f}, expR {res['post_only'].expectancy_R - res['maker_open'].expectancy_R:+.3f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=20)
    args = ap.parse_args()

    print("=" * 72)
    print("B-6 [B-3] 진입 체결 모델 A/B — maker_open(legacy) / post_only(라이브) / taker")
    print("⚠ post_only fill% = 체결률 *상한* 추정(봉 ≫ 라이브 15초). 방향 테스트.")
    print("   정밀 캘리브레이션은 B-8(testnet 실체결). 가설(B-3): 모멘텀/돌파에서")
    print("   체결률 급감 + 엣지 약화·역전이 드러난다.")
    print("=" * 72)

    d1 = load_parquet_1d(args.max_syms)
    if d1:
        report(f"daily_tsmom (1d, {len(d1)}종목)", run_models("breakout", d1))

    h1 = load_csv_1h(args.max_syms)
    if h1:
        report(f"breakout (1h, {len(h1)}종목)", run_models("breakout", h1))

    h_oi = {k: v for k, v in h1.items() if "open_interest" in v.columns}
    if h_oi:
        report(
            f"oi_surge (1h, {len(h_oi)}종목)",
            run_models(
                "oi_surge", h_oi,
                oi_change_threshold_pct=5.0, price_change_threshold_pct=2.0,
            ),
        )

    print("\n" + "=" * 72)
    print("해석: post_only 체결률↓ + (PF/expR 가 maker_open 대비 약화·역전)이면 B-3 확정 —")
    print("백테스트가 라이브 체결 역선택을 가려온 것. legacy 로 되돌리지 말 것(가시화된 진실).")


if __name__ == "__main__":
    main()
