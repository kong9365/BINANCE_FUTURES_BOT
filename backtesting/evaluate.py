"""
backtesting/evaluate.py
=====================================================================
사전확정 합격 기준(Pre-Committed Bar) 평가 — 다중검정 방어용.

검증 후 골대를 옮기지 않도록 기준을 *코드*로 못박는다. 각 전략의
PortfolioResult 를 받아 종목별 분해 + OOS 지표 + 견고성 체크 후 PASS/FAIL.

기본 기준 (docs/STRATEGY_REALISM_REVIEW.md / 신규 계획):
  - 표본 ≥ 150 거래
  - OOS PF ≥ 1.3 & expectancy_R 양(+)
  - 횡단면 강건성: 단일 종목 기여 < 40% & 거래된 종목 과반 순(+)
  - MDD < 20%, Sharpe > 0.8
=====================================================================
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from backtesting.portfolio_backtest import PortfolioResult


@dataclass
class PreCommittedBar:
    min_trades: int = 150
    min_oos_pf: float = 1.3
    min_oos_expR: float = 0.0
    max_single_sym_share: float = 0.40
    require_majority_positive: bool = True
    max_mdd_pct: float = 20.0
    min_sharpe: float = 0.8


@dataclass
class Criterion:
    name: str
    passed: bool
    value: float
    threshold: float
    note: str = ""


@dataclass
class EvalReport:
    strategy: str
    passed: bool
    criteria: List[Criterion]
    per_symbol_pnl: Dict[str, float]
    per_symbol_count: Dict[str, int]
    summary: Dict[str, float]


def evaluate(strategy_name: str, result: PortfolioResult,
             oos_cutoff: Optional[pd.Timestamp] = None,
             bar: Optional[PreCommittedBar] = None) -> EvalReport:
    bar = bar or PreCommittedBar()
    criteria: List[Criterion] = []
    trades = result.trades
    n_total = len(trades)

    per_sym_pnl: Dict[str, float] = defaultdict(float)
    per_sym_count: Dict[str, int] = defaultdict(int)
    for t in trades:
        per_sym_pnl[t.symbol] += t.pnl_usd
        per_sym_count[t.symbol] += 1
    total_pnl = sum(per_sym_pnl.values())

    # 1) 표본
    criteria.append(Criterion(
        "sample_trades", n_total >= bar.min_trades,
        float(n_total), float(bar.min_trades)))

    # 2) OOS PF & expectancy_R
    if oos_cutoff is not None:
        oos_trades = [t for t in trades
                      if pd.Timestamp(t.entry_ts) >= oos_cutoff]
        wins = sum(t.pnl_usd for t in oos_trades if t.pnl_usd > 0)
        losses = abs(sum(t.pnl_usd for t in oos_trades if t.pnl_usd <= 0))
        oos_pf = (wins / losses) if losses > 0 else (99.99 if wins > 0 else 0.0)
        oos_expR = (sum(t.pnl_R for t in oos_trades) / len(oos_trades)
                    if oos_trades else 0.0)
        criteria.append(Criterion("oos_pf", oos_pf >= bar.min_oos_pf,
            oos_pf, bar.min_oos_pf, note=f"{len(oos_trades)} OOS trades"))
        criteria.append(Criterion("oos_expR", oos_expR > bar.min_oos_expR,
            oos_expR, bar.min_oos_expR))
    else:
        criteria.append(Criterion("oos_pf", False, 0.0, bar.min_oos_pf,
                                   note="no oos_cutoff provided"))
        criteria.append(Criterion("oos_expR", False, 0.0, bar.min_oos_expR,
                                   note="no oos_cutoff provided"))

    # 3) 단일 종목 집중도
    max_sym_share = 0.0
    if total_pnl > 0 and per_sym_pnl:
        max_pnl = max(per_sym_pnl.values())
        max_sym_share = max_pnl / total_pnl
    criteria.append(Criterion(
        "single_sym_share", max_sym_share < bar.max_single_sym_share,
        max_sym_share, bar.max_single_sym_share))

    # 4) 과반 종목 순(+)
    pos_count = sum(1 for v in per_sym_pnl.values() if v > 0)
    n_syms = len(per_sym_pnl) or 1
    pos_share = pos_count / n_syms
    criteria.append(Criterion(
        "majority_positive_symbols",
        pos_share > 0.5 if bar.require_majority_positive else True,
        pos_share, 0.5, note=f"{pos_count}/{n_syms}"))

    # 5) MDD
    criteria.append(Criterion(
        "max_drawdown_pct",
        result.max_drawdown_pct < bar.max_mdd_pct,
        result.max_drawdown_pct, bar.max_mdd_pct))

    # 6) Sharpe
    criteria.append(Criterion(
        "sharpe", result.sharpe > bar.min_sharpe,
        result.sharpe, bar.min_sharpe))

    passed = all(c.passed for c in criteria)
    return EvalReport(
        strategy=strategy_name, passed=passed, criteria=criteria,
        per_symbol_pnl=dict(per_sym_pnl),
        per_symbol_count=dict(per_sym_count),
        summary={
            "total_trades": float(n_total),
            "total_pnl_usd": round(total_pnl, 2),
            "ret_pct": result.total_return_pct,
            "mdd_pct": result.max_drawdown_pct,
            "sharpe": result.sharpe,
            "pf": result.profit_factor if result.profit_factor != float("inf") else 99.99,
            "max_sym_share": round(max_sym_share, 4),
            "pos_share": round(pos_share, 4),
        },
    )


def format_report(rep: EvalReport) -> str:
    """사람 읽기용 요약 문자열."""
    lines = [f"=== {rep.strategy} → {'PASS' if rep.passed else 'FAIL'} ==="]
    for c in rep.criteria:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name}: {c.value:.3f} vs {c.threshold:.3f} {c.note}")
    return "\n".join(lines)
