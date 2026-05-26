"""Gate 1~3 임계 판정 (VERIFICATION_PLAN §3, 사전확정)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["pass", "borderline", "fail"]


@dataclass
class GateResult:
    gate: str
    status: GateStatus
    value: float
    detail: str = ""


def judge_ic(ic: float) -> GateStatus:
    if ic > 0.05:
        return "pass"
    if ic >= 0.02:
        return "borderline"
    return "fail"


def judge_decile(spread_pct: float, mono: float) -> GateStatus:
    mono_ok = mono > 0.5
    if spread_pct > 0.5 and mono_ok:
        return "pass"
    if spread_pct >= 0.2 or mono >= 0.3:
        return "borderline"
    return "fail"


def judge_backtest(
    pf: float, n: int, mdd_pct: float, avg_net_pct: float, sym_pos_pct: float,
) -> GateStatus:
    checks = [
        pf > 1.15,
        n >= 100,
        mdd_pct <= 25.0,
        avg_net_pct > 0.2,
        sym_pos_pct >= 40.0,
    ]
    passed = sum(checks)
    if passed == 5:
        return "pass"
    if passed >= 3:
        return "borderline"
    return "fail"


def overall_verdict(g1: GateStatus, g2: GateStatus, g3: GateStatus) -> str:
    statuses = [g1, g2, g3]
    if all(s == "pass" for s in statuses):
        return "PASS"
    border = sum(1 for s in statuses if s == "borderline")
    passed = sum(1 for s in statuses if s == "pass")
    if passed >= 2 and border >= 1:
        return "CONDITIONAL"
    if passed == 3 and border == 0:
        return "PASS"
    if passed >= 2 and g3 != "fail":
        return "CONDITIONAL"
    return "FAIL"
