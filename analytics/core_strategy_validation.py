"""
analytics/core_strategy_validation.py
=====================================================================
Track B — 1d TSMOM/Donchian 코어 검증 메트릭 + 사전확정 게이트 (순수).

엔진(BacktestEngine breakout + Chandelier trail)이 만든 Trade 리스트로부터 OOS
메트릭을 집계하고 PASS/FAIL/INSUFFICIENT_SAMPLE 를 판정한다. 파라미터·임계 고정.

게이트(전부 충족=PASS / n<200=INSUFFICIENT_SAMPLE / 그 외 미달=FAIL):
  n ≥ 200 / gross_R > cost_R(핵심) / Net PF ≥ 1.2 / OOS Sharpe ≥ 1.0 /
  최악 3mo 윈도우 MDD < 20% / cross-pair positive > 55%
"""

from __future__ import annotations

import math
from datetime import timedelta

GATE_MIN_TRADES = 200
GATE_PF = 1.2
GATE_SHARPE = 1.0
GATE_WORST_MDD = 20.0      # %
GATE_CROSS_PAIR = 0.55


def to_dt(x):
    """엔진 ts(Timestamp/datetime) → tz-naive datetime."""
    if hasattr(x, "to_pydatetime"):
        x = x.to_pydatetime()
    return x.replace(tzinfo=None) if getattr(x, "tzinfo", None) else x


def worst_window_mdd(equity, days=92):
    """equity[(dt, value)] 를 ~3mo 슬라이딩하며 최악 구간 내 MDD(%)."""
    if len(equity) < 2:
        return 0.0
    worst, j = 0.0, 0
    for i in range(len(equity)):
        while equity[i][0] - equity[j][0] > timedelta(days=days):
            j += 1
        peak, win_mdd = equity[j][1], 0.0
        for k in range(j, i + 1):
            peak = max(peak, equity[k][1])
            if peak > 0:
                win_mdd = max(win_mdd, (peak - equity[k][1]) / peak * 100.0)
        worst = max(worst, win_mdd)
    return worst


def _side(trades):
    """방향 분리 통계: n, gross_R, net_R, win%."""
    rk = [t for t in trades if t.risk_usdt > 0]
    if not rk:
        return {"n": 0, "gross_R": 0.0, "net_R": 0.0, "win": 0.0}
    return {
        "n": len(rk),
        "gross_R": sum(t.gross_pnl_usd / t.risk_usdt for t in rk) / len(rk),
        "net_R": sum(t.pnl_R for t in rk) / len(rk),
        "win": sum(1 for t in rk if t.pnl_usd > 0) / len(rk),
    }


def cross_pair_positive_frac(trades):
    """심볼별 net_R 합이 양수인 코인 비율 (1개 종목 의존 방어)."""
    by = {}
    for t in trades:
        by.setdefault(t.symbol, 0.0)
        by[t.symbol] += t.pnl_R
    if not by:
        return 0.0, {}
    pos = sum(1 for v in by.values() if v > 0)
    return pos / len(by), by


def compute_metrics(trades, equity_curve, cutoff):
    """OOS(cutoff 이후) 메트릭 집계. trades=엔진 Trade 리스트, equity_curve=[(dt,equity)]."""
    oos = [t for t in trades if to_dt(t.entry_ts) >= cutoff]
    eq = [(to_dt(d), e) for d, e in equity_curve if to_dt(d) >= cutoff]
    n = len(oos)
    base = {"n": n, "worst_mdd": worst_window_mdd(eq), "trades": oos}
    if n == 0:
        return base
    wins = [t for t in oos if t.pnl_usd > 0]
    gp = sum(t.pnl_usd for t in wins)
    gl = -sum(t.pnl_usd for t in oos if t.pnl_usd <= 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    rk = [t for t in oos if t.risk_usdt > 0]
    gross_R = sum(t.gross_pnl_usd / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    cost_R = sum((t.fees_usd + t.funding_usd) / t.risk_usdt for t in rk) / len(rk) if rk else 0.0
    rets = [eq[i][1] / eq[i - 1][1] - 1 for i in range(1, len(eq)) if eq[i - 1][1] > 0]
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    cp_frac, by_sym = cross_pair_positive_frac(oos)
    yr = {}
    for t in oos:
        yr.setdefault(to_dt(t.entry_ts).year, []).append(t.pnl_R)
    base.update({
        "win_rate": len(wins) / n, "pf": pf, "gross_R": gross_R, "cost_R": cost_R,
        "net_R": sum(t.pnl_R for t in oos) / n,
        "avg_trade_R": sum(t.pnl_R for t in oos) / n,
        "sharpe": sharpe,
        "cross_pair_positive": cp_frac, "by_symbol": by_sym,
        "long": _side([t for t in oos if t.action == "LONG"]),
        "short": _side([t for t in oos if t.action == "SHORT"]),
        "by_year": {y: (len(v), sum(v) / len(v)) for y, v in sorted(yr.items())},
    })
    return base


def evaluate_gates(m) -> tuple:
    """(verdict, failed) — verdict ∈ PASS/FAIL/INSUFFICIENT_SAMPLE."""
    n = m.get("n", 0)
    if n < GATE_MIN_TRADES:
        return "INSUFFICIENT_SAMPLE", [f"n={n}<{GATE_MIN_TRADES}"]
    failed = []
    if not (m["gross_R"] > m["cost_R"]):
        failed.append(f"grossR {m['gross_R']:.3f} ≤ costR {m['cost_R']:.3f}")
    if m["pf"] < GATE_PF:
        failed.append(f"PF {m['pf']:.2f}<{GATE_PF}")
    if m["sharpe"] < GATE_SHARPE:
        failed.append(f"Sharpe {m['sharpe']:.2f}<{GATE_SHARPE}")
    if m["worst_mdd"] >= GATE_WORST_MDD:
        failed.append(f"MDD {m['worst_mdd']:.1f}≥{GATE_WORST_MDD}")
    if m["cross_pair_positive"] <= GATE_CROSS_PAIR:
        failed.append(f"cross-pair+ {m['cross_pair_positive']*100:.0f}%≤{GATE_CROSS_PAIR*100:.0f}%")
    return ("PASS" if not failed else "FAIL"), failed
