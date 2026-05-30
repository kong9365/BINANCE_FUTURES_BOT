"""
scripts/run_phaseC_breakout_macro_wf.py
=====================================================================
Phase C 후보 — "개선된 돌파"(확률 최대화) out-of-sample 정직 판정.

★ 사전확정(pre-registered) 기준 — 구현 후 변경 금지. 통과/실패 그대로 보고.
   파라미터 스윕·그리드서치·"통과할 때까지 손보기" 금지(단일값 1개씩).

OOS 방식: 파라미터가 *사전확정·무최적화*이므로 walk_forward 윈도우별 재적합이
불필요(과적합 위험 없음). 200EMA 일봉 전략은 3mo OOS 슬라이스에 워밍업(200봉)이
없어 평가 불가하므로, **전체 데이터 1회 실행(워밍업 포함) → IS 12mo(워밍업) 이후를
OOS 로 partition** 한다. 워밍업 이후 전 구간이 사실상 OOS(룩어헤드 엔진이 차단).
최악 3mo 윈도우 MDD 는 OOS equity 곡선을 슬라이딩해 산출.

스펙 (운영자 승인 2026-05-30): 1d / 롱전용 / BTC 200EMA risk-on / 거래량 동반(×1.5) /
mid-liq 동적 유니버스 / 순수 비대칭 트레일(2ATR 손절 + 3ATR chandelier, 고정 TP 없음) /
코인당 계좌 risk 0.5%(notional≠risk), 동시보유 ≤5 / post_only 현실 체결 + 청산 슬리피지.

사전확정 게이트(post_only 기준 통과해야 함):
  OOS Sharpe≥1.0 / Net PF≥1.2 / 최악 3mo OOS MDD<20% / 총 OOS 거래≥200 / 거래당 gross>cost
실패 구분: n<200=표본부족(판정불가, 답=데이터확장) / n≥200·PF<1.2=엣지미달(결론).

⚠ 1d 테스트 — B-3(post_only 15초 역선택) 해소 테스트 아님(인트라데이 현상).
사용: python scripts/run_phaseC_breakout_macro_wf.py [--max-syms N]
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
WARMUP_MONTHS = 12          # IS(워밍업) — 이후가 OOS
MODES = ["post_only", "post_only_strict", "maker_open", "taker"]

PARAMS = dict(
    long_only=True,
    macro_btc_ema_period=200, volume_confirm_mult=1.5, volume_confirm_bars=20,
    min_trailing_volume_usd=20_000_000.0, trailing_volume_bars=30,
    risk_per_trade_pct=0.005, max_concurrent_positions=5,
    breakout_donchian=20, breakout_adx_min=25.0, breakout_ema=200,
    breakout_atr_stop=2.0, breakout_trail_exit=True, breakout_trail_atr_mult=3.0,
)


def _load_1d(max_syms: int) -> dict:
    out = {}
    pat = os.path.join(PROJECT_ROOT, "backtests", "cache", "verification", "*_1d.parquet")
    for p in sorted(glob.glob(pat))[:max_syms]:
        sym = os.path.basename(p).replace("_1d.parquet", "")
        d = pd.read_parquet(p)
        ts = d["ts"]
        raw = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) \
            else pd.to_datetime(ts)
        idx = pd.DatetimeIndex(raw)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        d = d.copy(); d.index = idx; d = d.sort_index()
        if "funding_rate" not in d.columns:
            d["funding_rate"] = 0.0
        out[sym] = d[[c for c in ["open", "high", "low", "close", "volume", "funding_rate"]
                      if c in d.columns]]
    return out


def _run(candles: dict, fill_model: str):
    cfg = BacktestConfig(pairs=list(candles), strategy="breakout",
                         entry_fill_model=fill_model, **PARAMS)
    return BacktestEngine(cfg).run(candles)


def _worst_3mo_mdd(eq: list) -> float:
    """OOS equity 곡선[(dt,equity)]을 3mo 슬라이딩하며 최악 구간 내 MDD(%) 반환."""
    if len(eq) < 2:
        return 0.0
    worst = 0.0
    j = 0
    for i in range(len(eq)):
        while eq[i][0] - eq[j][0] > timedelta(days=92):
            j += 1
        peak = trough = eq[j][1]
        win_mdd = 0.0
        for k in range(j, i + 1):
            v = eq[k][1]
            peak = max(peak, v)
            if peak > 0:
                win_mdd = max(win_mdd, (peak - v) / peak * 100.0)
        worst = max(worst, win_mdd)
    return worst


def _agg(result, cutoff) -> dict:
    trades = [t for t in result.trades if self_dt(t.entry_ts) >= cutoff]
    n = len(trades)
    eq = [(self_dt(d), e) for d, e in result.equity_curve if self_dt(d) >= cutoff]
    if n == 0:
        return dict(n=0, worst_mdd=_worst_3mo_mdd(eq))
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = -sum(t.pnl_usd for t in losses)
    pf = (gp / gl) if gl > 0 else float("inf")
    rk = [t for t in trades if t.risk_usdt > 0]
    gross_R = sum(t.gross_pnl_usd / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    cost_R = sum((t.fees_usd + t.funding_usd) / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    net_R = sum(t.pnl_R for t in trades) / n
    # OOS Sharpe — 일별 equity 수익률 × √365
    rets = [eq[i][1] / eq[i - 1][1] - 1 for i in range(1, len(eq)) if eq[i - 1][1] > 0]
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return dict(
        n=n, win_rate=len(wins) / n, pf=pf, gross_R=gross_R, cost_R=cost_R, net_R=net_R,
        avg_win=(sum(t.pnl_R for t in wins) / len(wins) if wins else 0.0),
        avg_loss=(sum(t.pnl_R for t in losses) / len(losses) if losses else 0.0),
        avg_hold=sum(t.bars_held for t in trades) / n, sharpe=sharpe,
        worst_mdd=_worst_3mo_mdd(eq), trades=trades,
    )


def self_dt(x):
    """엔진 ts(Timestamp/datetime) → tz-naive datetime."""
    if hasattr(x, "to_pydatetime"):
        x = x.to_pydatetime()
    return x.replace(tzinfo=None) if getattr(x, "tzinfo", None) else x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=52)
    args = ap.parse_args()
    candles = _load_1d(args.max_syms)
    start = min(df.index.min() for df in candles.values())
    cutoff = self_dt(start) + timedelta(days=365)   # IS 12mo 워밍업 이후 = OOS

    print("=" * 74)
    print("Phase C 후보 — 개선된 돌파 (롱전용+BTC매크로+거래량+mid-liq+순수트레일)")
    print(f"데이터 1d {len(candles)}종목 | OOS = {cutoff.date()} 이후(워밍업 12mo 제외) | post_only 판정")
    print("⚠ 1d 테스트 — B-3(post_only 15초 역선택) 해소 아님(인트라데이 현상).")
    print(f"게이트: Sharpe≥{GATE_SHARPE}/PF≥{GATE_PF}/최악3moMDD<{GATE_WORST_MDD}%/n≥{GATE_MIN_TRADES}/gross>cost")
    print("=" * 74)

    by = {m: _agg(_run(candles, m), cutoff) for m in MODES}

    print(f"\n{'mode':16}{'n':>6}{'win%':>7}{'PF':>7}{'grossR':>8}{'costR':>8}{'netR':>8}{'w3moMDD':>9}{'Shrp':>7}")
    for m in MODES:
        a = by[m]
        if a["n"] == 0:
            print(f"{m:16}{0:>6}   (거래 0, MDD {a['worst_mdd']:.1f}%)"); continue
        print(f"{m:16}{a['n']:>6}{a['win_rate']*100:>6.1f}%{a['pf']:>7.2f}"
              f"{a['gross_R']:>8.3f}{a['cost_R']:>8.3f}{a['net_R']:>8.3f}{a['worst_mdd']:>8.1f}%{a['sharpe']:>7.2f}")

    a = by["post_only"]
    print("\n" + "=" * 74)
    print("사전확정 판정 (post_only 현실 체결 기준):")
    if a["n"] < GATE_MIN_TRADES:
        v = (f"표본부족(판정불가) — n={a['n']}<{GATE_MIN_TRADES}. "
             "답은 튜닝이 아니라 데이터 확장(코인↑·기간↑·TF 백필).")
    else:
        ok = (a["sharpe"] >= GATE_SHARPE and a["pf"] >= GATE_PF
              and a["worst_mdd"] < GATE_WORST_MDD and a["gross_R"] > a["cost_R"])
        if ok:
            v = "★ 통과 — 소액 testnet 후보 (단 1d라 B-3 는 B-8 에서 별도)"
        else:
            f = []
            if a["sharpe"] < GATE_SHARPE: f.append(f"Sharpe {a['sharpe']:.2f}<{GATE_SHARPE}")
            if a["pf"] < GATE_PF: f.append(f"PF {a['pf']:.2f}<{GATE_PF}")
            if a["worst_mdd"] >= GATE_WORST_MDD: f.append(f"MDD {a['worst_mdd']:.1f}≥{GATE_WORST_MDD}")
            if a["gross_R"] <= a["cost_R"]: f.append(f"grossR {a['gross_R']:.3f}≤costR {a['cost_R']:.3f}")
            v = f"엣지미달(결론) — n={a['n']}≥{GATE_MIN_TRADES}인데 {', '.join(f)}"
    print(f"  → {v}")
    if a["n"] > 0:
        print(f"  win%={a['win_rate']*100:.1f}(순수트레일=저승률 정상) avg_win={a['avg_win']:.2f}R "
              f"avg_loss={a['avg_loss']:.2f}R avg_hold={a['avg_hold']:.1f}bars")
        print("  코인별 거래수(상위6):", dict(Counter(t.symbol for t in a["trades"]).most_common(6)))
    print("=" * 74)
    print("파라미터 무수정(사전확정). 1d post_only≈maker(B-6)라 4모드 수렴 예상 — 판정은 post_only.")
    print("승률이 아니라 PF·net edge 로 판정(순수 트레일은 저승률·우측꼬리 프로파일).")


if __name__ == "__main__":
    main()
