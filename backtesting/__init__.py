"""
backtesting/
=====================================================================
Phase 0 백테스트 & 검증 패키지.

모듈:
  - backtest_engine.py   BacktestEngine — 시간순 이벤트 루프 백테스트 (§10-2, §10-5)
  - walk_forward.py      walk_forward / _generate_windows — Walk-Forward 절차 (§10-3)
  - plots.py             equity curve / drawdown / monthly heatmap (matplotlib, Agg)

룩어헤드(look-ahead) 차단이 본 패키지의 절대 원칙이다 (§10-1 원칙 ⑤):
어떤 지표·시그널도 현재 봉 마감 이전 데이터만 사용한다.
=====================================================================
"""

from __future__ import annotations

from backtesting.backtest_engine import (
    BacktestConfig,
    BacktestResult,
    BacktestEngine,
    Signal,
    Trade,
)
from backtesting.walk_forward import Window, walk_forward

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestEngine",
    "Signal",
    "Trade",
    "Window",
    "walk_forward",
]
