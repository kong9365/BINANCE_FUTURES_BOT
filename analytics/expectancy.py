"""
analytics/expectancy.py
=====================================================================
ExpectancyAnalyzer — 거래 기대값·승률 통계 (v3.1 기존 모듈, sample_count 반환 강화)

근거:
  - docs/SPEC_v3.1.md §8-4 (WeeklyGPTAnalyst가 overall/by_setup/by_regime 사용)
  - docs/SPEC_v3.1.md §8-3 (DynamicPositionSizer가 get_win_rate/get_avg_R 사용,
    §8-4 부록 매트릭스: "sample_count 반환 추가")

책임:
  - trades 테이블에서 최근 N일 거래를 집계
  - 전체 / setup_tag별 / regime별 Stats 산출
  - DynamicPositionSizer용 (win_rate, sample_count), (avg_win_R, avg_loss_R) 제공

R-multiple 계산:
  - LONG  : R = (exit_price - entry_price) / (entry_price - stop_loss)
  - SHORT : R = (entry_price - exit_price) / (stop_loss - entry_price)
  - stop_loss 누락 또는 위험폭 <= 0 이면 R 집계에서 제외 (거래 수·승률에는 포함)

설계 메모:
  - 단일 데이터셋에서 avg_R 와 expectancy_R 는 수학적으로 동일하다
    (expectancy = win_rate*avg_win_R + loss_rate*avg_loss_R = mean(R)).
    필드를 분리해 둔 것은 향후 가중/조정 expectancy 도입 여지를 위함.
  - 빈 결과는 None 이 아니라 _default_stats() 를 반환한다.
  - DB 조회 실패는 logger.error 후 _default_stats() 반환 (호출자 보호).
  - 시간은 모두 UTC. trades.timestamp 는 UTC ISO 문자열로 저장된다.
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class Stats:
    """거래 통계 묶음.

    Attributes:
        trade_count: 닫힌 거래 수.
        win_rate: 승률 (0.0~1.0). 승 = COALESCE(pnl_usd_net, pnl_usd) > 0.
        avg_R: 거래당 평균 R-multiple (R 산출 가능 거래 대상).
        expectancy_R: 기대값 R. 단일 데이터셋에서 avg_R 와 동일.
        total_pnl_usdt: 순손익 합계 (USDT).
        setup_tag: by_setup() 결과 식별용. overall()/by_regime() 에서는 None.
            (SPEC §8-4-2 의 WeeklyGPTAnalyst._build_prompt 가 s.setup_tag 를 참조)
    """

    trade_count: int
    win_rate: float
    avg_R: float
    expectancy_R: float
    total_pnl_usdt: float
    setup_tag: str | None = None


def _default_stats(setup_tag: str | None = None) -> Stats:
    """빈 결과·실패 시 반환할 0 통계."""
    return Stats(
        trade_count=0,
        win_rate=0.0,
        avg_R=0.0,
        expectancy_R=0.0,
        total_pnl_usdt=0.0,
        setup_tag=setup_tag,
    )


class ExpectancyAnalyzer:
    """trades 테이블 기반 거래 기대값·승률 분석기.

    사용:
        analyzer = ExpectancyAnalyzer(db_path)
        overall = analyzer.overall(days=7)
        by_setup = analyzer.by_setup(days=7)
        by_regime = analyzer.by_regime(days=7)
        win_rate, n = analyzer.get_win_rate("TREND_PULLBACK")
        avg_win_R, avg_loss_R = analyzer.get_avg_R("TREND_PULLBACK")
    """

    def __init__(self, db_path: str) -> None:
        """ExpectancyAnalyzer 초기화.

        Args:
            db_path: sqlite3 DB 파일 경로 (trades 테이블 조회용).
        """
        if not db_path:
            logger.warning("[Expectancy] db_path 가 비어 있음 — 모든 조회가 default 반환")
        self.db_path = db_path

    # ── public ──────────────────────────────────────────────────

    def overall(self, days: int = 7) -> Stats:
        """최근 N일 전체 거래 통계를 반환한다.

        Args:
            days: 집계 기간 (일).
        Returns:
            전체 Stats. 거래 없거나 조회 실패 시 _default_stats().
        """
        rows = self._fetch_closed_trades(days)
        if rows is None:
            return _default_stats()
        return self._aggregate(rows)

    def by_setup(self, days: int = 7) -> list[Stats]:
        """최근 N일 거래를 setup_tag 별로 분리해 Stats 리스트로 반환한다.

        setup_tag 가 NULL/빈 값인 거래는 제외한다.

        Args:
            days: 집계 기간 (일).
        Returns:
            setup_tag 별 Stats 리스트 (setup_tag 필드 채워짐). 빈 경우 [].
        """
        rows = self._fetch_closed_trades(days)
        if rows is None:
            return []

        grouped: dict[str, list[tuple]] = {}
        for row in rows:
            tag = row[5]  # setup_tag (행 순서: action,entry,exit,stop,net_pnl,setup_tag,regime)
            if not tag:
                continue
            grouped.setdefault(tag, []).append(row)

        result = [
            self._aggregate(group, setup_tag=tag) for tag, group in grouped.items()
        ]
        result.sort(key=lambda s: s.trade_count, reverse=True)
        return result

    def by_regime(self, days: int = 7) -> dict[str, Stats]:
        """최근 N일 거래를 regime 별로 분리해 {regime: Stats} 로 반환한다.

        regime 이 NULL/빈 값인 거래는 제외한다.

        Args:
            days: 집계 기간 (일).
        Returns:
            regime 별 Stats 딕셔너리. 빈 경우 {}.
        """
        rows = self._fetch_closed_trades(days)
        if rows is None:
            return {}

        grouped: dict[str, list[tuple]] = {}
        for row in rows:
            regime = row[6]  # regime (행 순서: action,entry,exit,stop,net_pnl,setup_tag,regime)
            if not regime:
                continue
            grouped.setdefault(regime, []).append(row)

        return {regime: self._aggregate(group) for regime, group in grouped.items()}

    def get_win_rate(
        self, setup_tag: str, days: int = 7, return_count: bool = True
    ):
        """특정 setup_tag 의 승률을 반환한다.

        DynamicPositionSizer 가 표본 수(sample_count) 기반으로 사이즈를
        축소하므로 기본값 return_count=True 로 (win_rate, count) 튜플을 준다.

        Args:
            setup_tag: 대상 셋업 태그.
            days: 집계 기간 (일).
            return_count: True 면 (win_rate, count) 튜플, False 면 win_rate float.
        Returns:
            return_count=True → (float, int) / False → float.
        """
        rows = self._fetch_closed_trades(days, setup_tag=setup_tag)
        if rows is None:
            return (0.0, 0) if return_count else 0.0

        stats = self._aggregate(rows, setup_tag=setup_tag)
        if return_count:
            return stats.win_rate, stats.trade_count
        return stats.win_rate

    def get_avg_R(self, setup_tag: str, days: int = 7) -> tuple[float, float]:
        """특정 setup_tag 의 평균 승리 R / 평균 손실 R 을 반환한다.

        Args:
            setup_tag: 대상 셋업 태그.
            days: 집계 기간 (일).
        Returns:
            (avg_win_R, avg_loss_R). avg_loss_R 은 음수. 표본 없으면 (0.0, 0.0).
        """
        rows = self._fetch_closed_trades(days, setup_tag=setup_tag)
        if rows is None:
            return 0.0, 0.0

        win_r: list[float] = []
        loss_r: list[float] = []
        for action, entry, exit_price, stop, _net_pnl, _tag, _regime in rows:
            r = self._compute_r(action, entry, exit_price, stop)
            if r is None:
                continue
            if r > 0:
                win_r.append(r)
            elif r < 0:
                loss_r.append(r)

        avg_win_R = sum(win_r) / len(win_r) if win_r else 0.0
        avg_loss_R = sum(loss_r) / len(loss_r) if loss_r else 0.0
        return round(avg_win_R, 6), round(avg_loss_R, 6)

    # ── 내부 ────────────────────────────────────────────────────

    def _fetch_closed_trades(
        self, days: int, setup_tag: str | None = None
    ) -> list[tuple] | None:
        """최근 N일 닫힌 거래 행을 조회한다.

        Args:
            days: 집계 기간 (일).
            setup_tag: 지정 시 해당 셋업만 필터.
        Returns:
            (action, entry_price, exit_price, stop_loss, net_pnl, setup_tag, regime)
            튜플 리스트. 조회 실패 시 None (호출자가 default 처리).
        """
        # v3.1.2 정정 카테고리: trades.timestamp 는 UTC ISO 이므로 UTC 기준으로 윈도우 산출.
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = (
            "SELECT action, entry_price, exit_price, stop_loss, "
            "       COALESCE(pnl_usd_net, pnl_usd, 0), setup_tag, regime "
            "FROM trades "
            "WHERE timestamp >= ? AND exit_price IS NOT NULL"
        )
        params: tuple = (since,)
        if setup_tag is not None:
            query += " AND setup_tag = ?"
            params = (since, setup_tag)

        try:
            conn = sqlite3.connect(self.db_path)
            try:
                return conn.execute(query, params).fetchall()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — DB 실패는 default 반환으로 흡수
            logger.error("[Expectancy] 거래 조회 실패: %s → default 반환", e)
            return None

    def _aggregate(self, rows: list[tuple], setup_tag: str | None = None) -> Stats:
        """거래 행 리스트를 Stats 로 집계한다.

        Args:
            rows: _fetch_closed_trades 가 반환한 튜플 리스트.
            setup_tag: 결과 Stats 에 기록할 식별 태그 (by_setup 용).
        Returns:
            집계된 Stats. rows 가 비면 _default_stats(setup_tag).
        """
        n = len(rows)
        if n == 0:
            return _default_stats(setup_tag)

        wins = 0
        total_pnl = 0.0
        r_values: list[float] = []
        for action, entry, exit_price, stop, net_pnl, _tag, _regime in rows:
            net = float(net_pnl or 0.0)
            total_pnl += net
            if net > 0:
                wins += 1
            r = self._compute_r(action, entry, exit_price, stop)
            if r is not None:
                r_values.append(r)

        win_rate = wins / n
        avg_R = sum(r_values) / len(r_values) if r_values else 0.0
        # 단일 데이터셋에서 expectancy_R == avg_R (수학적으로 동일, 설계 메모 참조).
        expectancy_R = avg_R

        return Stats(
            trade_count=n,
            win_rate=round(win_rate, 6),
            avg_R=round(avg_R, 6),
            expectancy_R=round(expectancy_R, 6),
            total_pnl_usdt=round(total_pnl, 6),
            setup_tag=setup_tag,
        )

    @staticmethod
    def _compute_r(
        action: str | None,
        entry: float | None,
        exit_price: float | None,
        stop: float | None,
    ) -> float | None:
        """단일 거래의 R-multiple 을 계산한다.

        Args:
            action: "LONG" / "SHORT".
            entry: 진입가.
            exit_price: 청산가.
            stop: 손절가.
        Returns:
            R-multiple. 입력 누락·위험폭 <= 0·미지원 action 이면 None.
        """
        if entry is None or exit_price is None or stop is None:
            return None
        side = (action or "").upper()
        if side == "LONG":
            risk = entry - stop
            reward = exit_price - entry
        elif side == "SHORT":
            risk = stop - entry
            reward = entry - exit_price
        else:
            return None
        if risk <= 0:
            return None
        return reward / risk
