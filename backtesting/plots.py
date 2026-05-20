"""
backtesting/plots.py
=====================================================================
백테스트 시각화 — equity curve / drawdown / monthly returns heatmap

모든 차트는 결정론적이다:
  - matplotlib Agg 백엔드 강제 (헤드리스 환경 안전, GUI 미의존)
  - numpy random seed 고정 (현재 차트는 난수를 쓰지 않으나, 향후 지터/
    샘플링 추가 시에도 재현성을 보장하기 위한 방어적 고정)

명세서: docs/SPEC_v3.1.md §10-6 (보고서 양식 plots/ 폴더)
=====================================================================
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")  # 헤드리스 안전 — pyplot import 전에 호출해야 함

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

if TYPE_CHECKING:
    from backtesting.backtest_engine import BacktestResult

logger = logging.getLogger(__name__)

# 결정론 보장용 seed 고정 (모듈 로드 시 1회)
_RANDOM_SEED = 42
np.random.seed(_RANDOM_SEED)

_MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def plot_equity_curve(result: "BacktestResult", save_path: str) -> str | None:
    """자본 곡선을 그려 save_path 에 저장.

    Args:
        result: BacktestResult (equity_curve 사용).
        save_path: 저장 경로 (.png 등).

    Returns:
        저장 경로. equity_curve 가 비어 있으면 None.
    """
    if not result.equity_curve:
        logger.warning("[plots] equity_curve 비어 있음 — equity curve 차트 생략")
        return None

    xs = [t for t, _ in result.equity_curve]
    ys = [v for _, v in result.equity_curve]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(xs, ys, color="#1f77b4", linewidth=1.2)
    ax.axhline(
        result.config.initial_capital,
        color="#999999", linestyle="--", linewidth=0.8, label="Initial Capital",
    )
    ax.set_title("Equity Curve")
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity (USDT)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(save_path, dpi=100)
    plt.close(fig)
    logger.info("[plots] equity curve 저장: %s", save_path)
    return save_path


def plot_drawdown(result: "BacktestResult", save_path: str) -> str | None:
    """드로다운 곡선을 그려 save_path 에 저장.

    Args:
        result: BacktestResult (drawdown_curve 사용).
        save_path: 저장 경로.

    Returns:
        저장 경로. drawdown_curve 가 비어 있으면 None.
    """
    if not result.drawdown_curve:
        logger.warning("[plots] drawdown_curve 비어 있음 — drawdown 차트 생략")
        return None

    xs = [t for t, _ in result.drawdown_curve]
    ys = [v for _, v in result.drawdown_curve]

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(xs, ys, 0, color="#d62728", alpha=0.4)
    ax.plot(xs, ys, color="#d62728", linewidth=1.0)
    ax.set_title(f"Drawdown (Max {result.max_drawdown_pct:.2f}%)")
    ax.set_xlabel("Time")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.3)
    ax.invert_yaxis()  # 드로다운은 아래로 깊어지도록
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(save_path, dpi=100)
    plt.close(fig)
    logger.info("[plots] drawdown 저장: %s", save_path)
    return save_path


def plot_monthly_returns_heatmap(
    result: "BacktestResult", save_path: str,
) -> str | None:
    """월별 수익률 히트맵(연 × 월)을 그려 save_path 에 저장.

    월 수익률은 자본 곡선의 월말 자본 변화로 산출한다.

    Args:
        result: BacktestResult (equity_curve 사용).
        save_path: 저장 경로.

    Returns:
        저장 경로. 데이터가 2개 미만이면 None.
    """
    if len(result.equity_curve) < 2:
        logger.warning("[plots] equity_curve 데이터 부족 — heatmap 차트 생략")
        return None

    # 월말 자본 (각 (연,월)의 마지막 equity)
    monthly_equity: dict[tuple[int, int], float] = {}
    for dt_pt, v in result.equity_curve:
        monthly_equity[(dt_pt.year, dt_pt.month)] = v

    keys = list(monthly_equity.keys())  # equity_curve 시간 오름차순 → 키도 오름차순
    monthly_return: dict[tuple[int, int], float] = {}
    prev_v = result.config.initial_capital
    for key in keys:
        v = monthly_equity[key]
        monthly_return[key] = (
            (v - prev_v) / prev_v * 100 if prev_v > 0 else 0.0
        )
        prev_v = v

    years = sorted({y for y, _ in keys})
    grid = np.full((len(years), 12), np.nan)
    for (y, m), r in monthly_return.items():
        grid[years.index(y), m - 1] = r

    # 색상 범위: 유한값 절댓값 최대 기준 대칭 (없으면 ±1)
    finite = grid[~np.isnan(grid)]
    bound = float(np.max(np.abs(finite))) if finite.size else 1.0
    if bound == 0.0:
        bound = 1.0

    fig, ax = plt.subplots(figsize=(12, max(2.0, len(years) * 0.6)))
    im = ax.imshow(
        grid, aspect="auto", cmap="RdYlGn", vmin=-bound, vmax=bound,
    )
    ax.set_xticks(range(12))
    ax.set_xticklabels(_MONTH_LABELS)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels([str(y) for y in years])
    ax.set_title("Monthly Returns Heatmap (%)")

    # 셀 값 주석
    for yi in range(len(years)):
        for mi in range(12):
            val = grid[yi, mi]
            if not np.isnan(val):
                ax.text(
                    mi, yi, f"{val:.1f}",
                    ha="center", va="center", fontsize=7, color="black",
                )

    fig.colorbar(im, ax=ax, label="Monthly Return (%)")
    fig.tight_layout()
    fig.savefig(save_path, dpi=100)
    plt.close(fig)
    logger.info("[plots] monthly returns heatmap 저장: %s", save_path)
    return save_path
