"""
backtesting/walk_forward.py
=====================================================================
Walk-Forward Optimization 절차 (§10-3)

12개월 in-sample / 3개월 out-of-sample / 1개월 슬라이딩 윈도우로
백테스트를 반복해 OOS 결과 리스트를 만든다.

본 시스템은 고정 파라미터를 권장하므로 (§10-3) _optimize_in_sample 은
실제 최적화를 수행하지 않는다. IS 구간은 레짐 워밍업·표본 확보 의미만 갖고,
검증은 전적으로 OOS 구간에서 이뤄진다.

세션 11 정정 ②: §10-5 골격은 walk_forward / _generate_windows 를
BacktestEngine 메서드로 둔다. 순환 import 회피 + 단일 책임 원칙에 따라
본 모듈 함수로 분리했다. 자세한 내용: docs/CORRECTIONS_v3.1.2.md

명세서: docs/SPEC_v3.1.md §10-3
=====================================================================
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class Window:
    """Walk-Forward 윈도우 1개. in-sample / out-of-sample 구간 분할."""

    is_start: datetime
    is_end: datetime       # = oos_start (IS 종료 = OOS 시작)
    oos_start: datetime
    oos_end: datetime


def _add_months(dt: datetime, months: int) -> datetime:
    """datetime 에 months 개월을 더한다 (월말 일자 보정 포함)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _generate_windows(
    start: datetime,
    end: datetime,
    is_months: int,
    oos_months: int,
    step_months: int,
) -> List[Window]:
    """[start, end] 구간을 IS/OOS 슬라이딩 윈도우로 분할 (§10-3).

    Args:
        start: 데이터 시작 시점.
        end: 데이터 종료 시점.
        is_months: in-sample 길이 (개월).
        oos_months: out-of-sample 길이 (개월).
        step_months: 윈도우 슬라이드 간격 (개월).

    Returns:
        Window 리스트. 마지막 윈도우의 oos_end 는 end 로 클램프된다.
    """
    if start >= end:
        logger.warning("[WalkForward] start >= end — 빈 윈도우 리스트 반환")
        return []
    if is_months <= 0 or oos_months <= 0 or step_months <= 0:
        raise ValueError(
            f"개월 파라미터는 모두 양수여야 함 "
            f"(is={is_months}, oos={oos_months}, step={step_months})"
        )

    windows: List[Window] = []
    cur_is_start = start
    while True:
        is_end = _add_months(cur_is_start, is_months)
        oos_start = is_end
        oos_end = _add_months(oos_start, oos_months)

        if oos_start >= end:
            break
        if oos_end > end:
            oos_end = end

        windows.append(Window(cur_is_start, is_end, oos_start, oos_end))

        if oos_end >= end:
            break
        cur_is_start = _add_months(cur_is_start, step_months)

    return windows


def _optimize_in_sample(
    candles_by_pair: Dict[str, pd.DataFrame],
    window: Window,
) -> Dict[str, Any]:
    """IS 구간 파라미터 최적화 — 본 시스템은 고정 파라미터 권장 (§10-3).

    실제 최적화를 수행하지 않고 빈 딕셔너리를 반환한다. 과적합 위험이 큰
    파라미터 탐색을 의도적으로 배제하는 설계 결정이다 (§10-1 원칙 ④, §10-4 경고).
    """
    return {}


def _slice_candles(
    candles_by_pair: Dict[str, pd.DataFrame],
    start: datetime,
    end: datetime,
) -> Dict[str, pd.DataFrame]:
    """각 페어 DataFrame 을 [start, end] 구간으로 자른다."""
    sliced: Dict[str, pd.DataFrame] = {}
    for symbol, df in candles_by_pair.items():
        sliced[symbol] = df[(df.index >= start) & (df.index <= end)]
    return sliced


def walk_forward(
    candles_by_pair: Dict[str, pd.DataFrame],
    windows: Optional[List[Window]] = None,
    config: Optional[BacktestConfig] = None,
) -> List[BacktestResult]:
    """Walk-Forward 실행 — 윈도우별 OOS BacktestResult 리스트 반환 (§10-3).

    Args:
        candles_by_pair: {symbol: DataFrame}. 전체 기간 캔들.
        windows: 사용할 윈도우 리스트. None 이면 config 의 IS/OOS/step 개월
            설정과 start_date/end_date 로 _generate_windows 자동 생성.
        config: BacktestConfig. None 이면 기본값 사용.

    Returns:
        OOS 구간만 평가한 BacktestResult 리스트 (윈도우 순서대로).
        OOS 구간에 캔들이 없는 윈도우는 건너뛴다.
    """
    if not candles_by_pair:
        raise ValueError("candles_by_pair 가 비어 있습니다.")

    config = config or BacktestConfig()
    if windows is None:
        windows = _generate_windows(
            config.start_date,
            config.end_date,
            config.in_sample_months,
            config.out_sample_months,
            config.step_months,
        )

    results: List[BacktestResult] = []
    for i, window in enumerate(windows, start=1):
        # IS 구간 — 고정 파라미터이므로 최적화는 no-op (§10-3).
        _optimize_in_sample(candles_by_pair, window)

        # OOS 구간 — 실제 검증.
        oos_candles = _slice_candles(
            candles_by_pair, window.oos_start, window.oos_end
        )
        if not any(len(df) > 0 for df in oos_candles.values()):
            logger.warning(
                "[WalkForward] 윈도우 %d OOS 구간(%s~%s) 캔들 없음 — 건너뜀",
                i, window.oos_start, window.oos_end,
            )
            continue

        engine = BacktestEngine(config)
        results.append(engine.run(oos_candles))

    logger.info(
        "[WalkForward] 완료 — 윈도우 %d개 중 %d개 OOS 평가",
        len(windows), len(results),
    )
    return results
