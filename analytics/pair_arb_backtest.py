"""
analytics/pair_arb_backtest.py
=====================================================================
Pair Stat-Arb R0/R1 backtest runner.

플로우:
  1) Supabase 에서 top-50 USDT-M perp ohlcv 1h 로드 (보호종목 제외)
  2) IS 끝 시점 (또는 OOS 시작 직전) 까지의 데이터로 select_pairs() → qualified pairs
  3) 각 qualified pair 에 대해 시점별 z-score 계산 → evaluate() 시그널 시뮬레이션
  4) 트레이드별 PnL 산출 (룩어헤드 차단: 진입가 = t+1 시가, 청산가 = exit_bar 시가)
  5) 비용 적용(round-trip ~0.33%) → portfolio equity → 사전확정 7기준 평가
  6) backtest_runs 적재

사전확정 R0 기준 (검증 전 못박음):
  n_trades >= 200, PF >= 1.3, Sharpe > 1.0, MDD <= 12%,
  횡단면 자격 페어 >= 50% positive, 단일 페어 기여 < 25%, BTC risk-off subset 검증.

룩어헤드 차단:
  - 페어 자격 판정은 시점 t 이전 lookback_days 데이터만 (IS 끝 고정 시 OOS 동안 재선정 안 함)
  - z-score 윈도도 t 까지의 데이터만
  - 진입가 = signal_bar 의 *다음* 봉(t+1) 시가; 청산가 = exit_bar 의 다음 봉 시가
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from strategy.pair_selector import (
    PairCandidate,
    PairSelectorConfig,
    select_pairs,
)
from strategy.pair_stat_arb import (
    ACTION_ENTER_LONG_A,
    ACTION_ENTER_SHORT_A,
    ACTION_EXIT_HARD_STOP,
    ACTION_EXIT_NORMAL,
    ACTION_EXIT_TIME_STOP,
    PairArbConfig,
    PairState,
    SIDE_LONG_A,
    SIDE_SHORT_A,
    compute_z,
    evaluate,
)
from analytics.ccs_lite_backtest import _build_supabase_client, _fetch_ohlcv

logger = logging.getLogger(__name__)


# ── 사전확정 R0 기준 (계획서 §사전확정 합격 기준) ──
R0_MIN_TRADES = 200
R0_MIN_PF = 1.3
R0_MIN_SHARPE = 1.0
R0_MAX_MDD = 0.12
R0_MIN_CROSS_PAIR_POS_SHARE = 0.50
R0_MAX_SINGLE_PAIR_CONTRIB = 0.25

# ── 비용 모델 (계획서 §비용 모델, 보수적) ──
COST_MAKER_ENTRY_PER_LEG = 0.00018      # 0.018%
COST_TAKER_EXIT_PER_LEG = 0.00045       # 0.045%
COST_SLIP_PER_LEG = 0.00100             # 0.10% (tier-2 보수)
ROUND_TRIP_COST = 2 * (COST_MAKER_ENTRY_PER_LEG + COST_TAKER_EXIT_PER_LEG + COST_SLIP_PER_LEG)


@dataclass
class PairTrade:
    """1 페어 트레이드(진입→청산)."""

    pair_key: str            # "A|B"
    symbol_a: str
    symbol_b: str
    side: str                # LONG_A | SHORT_A
    entry_ts: pd.Timestamp
    entry_price_a: float
    entry_price_b: float
    exit_ts: pd.Timestamp
    exit_price_a: float
    exit_price_b: float
    exit_reason: str         # ACTION_EXIT_*
    gross_return: float      # spread 수익률(비용 차감 전)
    net_return: float        # 비용 차감 후
    beta: float
    intercept: float


@dataclass
class R0Criterion:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass
class PairR0Report:
    run_id: str
    started_at: str
    finished_at: str
    n_qualified_pairs: int
    n_trades: int
    pf: float
    sharpe: float
    mean_trade_return: float
    max_dd: float
    cross_pair_pos_share: float
    single_pair_contribution: float
    criteria: List[R0Criterion] = field(default_factory=list)
    passed: bool = False
    notes: str = ""

    def to_jsonable(self) -> Dict[str, Any]:
        d = asdict(self)
        d["criteria"] = [asdict(c) for c in self.criteria]
        return d


# ── 데이터 로드 ───────────────────────────────────────────────────
def _fetch_universe_symbols(client, top_n: int = 50,
                            min_overlap_bars: int = 24 * 60) -> List[str]:
    """ohlcv 가 있는 비보호 심볼 중 *최근 30일 거래대금 상위 N* (계획서 정합).

    Supabase PostgREST 가 응답 행 수를 1000 으로 cap 하므로 *직접 SQL* 접근 안 됨.
    해결: 2단계 — (1) 전 distinct symbol 발견(funding_history 페이지네이션 dedup),
    (2) 각 심볼의 최근 30일 평균 close×volume 을 ohlcv 에서 계산해 ranking.

    이 함수는 backtesting/universe.py 의 *Supabase 미사용 라이브 ticker 기반*
    리졸버와 별개로, R0 백테스트용 *Supabase only* 리졸버다(인터넷·라이브 ticker 미요구).
    """
    from config.settings import PAIR_WHITELIST_CONFIG
    protected = set(PAIR_WHITELIST_CONFIG.protected_symbols)

    # Stage 1: distinct symbol 발견(funding_history dedup)
    seen: set[str] = set()
    max_pages = 50
    page_size = 1000
    for p in range(max_pages):
        start = p * page_size
        end = start + page_size - 1
        res = (
            client.table("funding_history")
            .select("symbol")
            .order("ts", desc=True)
            .range(start, end)
            .execute()
        )
        chunk = res.data or []
        prev_count = len(seen)
        for r in chunk:
            s = r.get("symbol")
            if s and s not in protected:
                seen.add(s)
        if len(chunk) < page_size:
            break
        if len(seen) == prev_count and p > 5:
            break

    # Stage 2: 심볼당 최근 30일(720봉) close×volume 합산 → 거래대금 proxy
    # 페이지네이션 최소화 위해 *마지막 720 봉*만 가져옴.
    logger.info("[PairArb] ranking %d symbols by recent 30d notional volume...", len(seen))
    notional: Dict[str, float] = {}
    for sym in seen:
        try:
            # 직접 limit-order desc 호출(페이지네이션 불필요 — 720봉 < 1000)
            res = (
                client.table("ohlcv")
                .select("close,volume")
                .eq("symbol", sym)
                .eq("interval", "1h")
                .order("ts", desc=True)
                .limit(720)
                .execute()
            )
            rows = res.data or []
            if not rows:
                continue
            n_sum = 0.0
            for r in rows:
                c = r.get("close")
                v = r.get("volume")
                if c is not None and v is not None:
                    n_sum += float(c) * float(v)
            notional[sym] = n_sum
        except Exception as e:  # noqa: BLE001
            logger.warning("[PairArb] %s notional 계산 실패 — 스킵: %s", sym, e)

    ranked = sorted(notional.items(), key=lambda kv: kv[1], reverse=True)
    top = [s for s, _ in ranked[:top_n]]
    if top:
        logger.info("[PairArb] top 10 by 30d notional: %s",
                    [(s, f"{notional[s]/1e9:.1f}B") for s in top[:10]])
    return top


def _load_ohlcv_dict(client, symbols: Sequence[str],
                     since_iso: Optional[str] = None,
                     until_iso: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """심볼 리스트 → {symbol: ohlcv DataFrame} 로드(페이지네이션). 빈 결과는 dict 에서 제외.

    until_iso 슬라이스는 로컬에서 적용(SupabasePostgREST .lt 추가 호출 비용 절약).
    """
    out: Dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            df = _fetch_ohlcv(client, sym, interval="1h", since_iso=since_iso)
            if df.empty:
                continue
            if until_iso:
                cutoff = pd.Timestamp(until_iso, tz="UTC")
                df = df[df.index < cutoff]
                if df.empty:
                    continue
            out[sym] = df
        except Exception as e:  # noqa: BLE001
            logger.warning("[PairArb] %s ohlcv load 실패 — 스킵: %s", sym, e)
        if i % 10 == 0:
            logger.info("[PairArb] ohlcv loaded %d/%d", i, len(symbols))
    return out


# ── 시뮬레이션 ────────────────────────────────────────────────────
def _simulate_pair(
    candidate: PairCandidate,
    ohlcv_a: pd.DataFrame,
    ohlcv_b: pd.DataFrame,
    start_idx: int,
    cfg: PairArbConfig,
    z_lookback_bars: int,
) -> List[PairTrade]:
    """단일 qualified pair 의 트레이드 리스트 산출.

    Args:
        candidate: pair_selector 산출 (beta, intercept fixed for OOS).
        ohlcv_a, ohlcv_b: 동일 index 정렬된 ohlcv (close + open 컬럼 필수).
        start_idx: 시뮬레이션 시작 인덱스(이전은 lookback 용도).
        cfg: PairArbConfig.
        z_lookback_bars: z-score 계산용 윈도(보통 60일 × 24h = 1440).

    Returns:
        PairTrade 리스트.
    """
    aligned = pd.concat([
        ohlcv_a[["open", "close"]].add_suffix("_a"),
        ohlcv_b[["open", "close"]].add_suffix("_b"),
    ], axis=1).dropna()
    if len(aligned) < start_idx + 10:
        return []
    log_close_a = np.log(aligned["close_a"].to_numpy(float))
    log_close_b = np.log(aligned["close_b"].to_numpy(float))
    opens_a = aligned["open_a"].to_numpy(float)
    opens_b = aligned["open_b"].to_numpy(float)
    timestamps = aligned.index

    trades: List[PairTrade] = []
    state = PairState()
    n = len(aligned)
    pair_key = f"{candidate.symbol_a}|{candidate.symbol_b}"

    for t in range(start_idx, n - 1):   # n-1: t+1 봉 시가 진입 보장
        # z-score: 시점 t 까지 (직전 z_lookback_bars 봉)
        lo = max(0, t - z_lookback_bars + 1)
        z = compute_z(log_close_a[lo:t+1], log_close_b[lo:t+1],
                      candidate.beta, candidate.intercept)
        prev_state = state
        state, action = evaluate(state, z, now=timestamps[t].to_pydatetime(), cfg=cfg)

        if action in (ACTION_ENTER_LONG_A, ACTION_ENTER_SHORT_A):
            # 진입가 = 다음 봉 시가(t+1)
            entry_idx = t + 1
            entry_a = opens_a[entry_idx]
            entry_b = opens_b[entry_idx]
            # 진입 시각도 t+1 봉 시각으로 갱신(state 의 entry_ts 는 t 봉 시각이라 미세 차이)
            state = PairState(side=state.side, entry_z=state.entry_z,
                              entry_ts=timestamps[entry_idx].to_pydatetime())
            # trade 정보 임시 저장(exit 시 close)
            trades.append(PairTrade(
                pair_key=pair_key, symbol_a=candidate.symbol_a, symbol_b=candidate.symbol_b,
                side=state.side, entry_ts=timestamps[entry_idx],
                entry_price_a=entry_a, entry_price_b=entry_b,
                exit_ts=timestamps[entry_idx], exit_price_a=entry_a, exit_price_b=entry_b,
                exit_reason="OPEN", gross_return=0.0, net_return=0.0,
                beta=candidate.beta, intercept=candidate.intercept,
            ))
        elif action in (ACTION_EXIT_NORMAL, ACTION_EXIT_HARD_STOP, ACTION_EXIT_TIME_STOP):
            # 청산가 = 다음 봉 시가
            exit_idx = t + 1
            exit_a = opens_a[exit_idx]
            exit_b = opens_b[exit_idx]
            # 마지막 OPEN trade 채우기
            if trades and trades[-1].exit_reason == "OPEN":
                tr = trades[-1]
                # 페어 spread 수익률 계산
                # LONG_A: long A + short B → return = (a_exit - a_entry)/a_entry - (b_exit - b_entry)/b_entry
                # SHORT_A: short A + long B → return = -(a_exit - a_entry)/a_entry + (b_exit - b_entry)/b_entry
                ret_a = (exit_a - tr.entry_price_a) / tr.entry_price_a
                ret_b = (exit_b - tr.entry_price_b) / tr.entry_price_b
                if tr.side == SIDE_LONG_A:
                    gross = ret_a - ret_b
                else:   # SHORT_A
                    gross = -ret_a + ret_b
                net = gross - ROUND_TRIP_COST
                tr.exit_ts = timestamps[exit_idx]
                tr.exit_price_a = exit_a
                tr.exit_price_b = exit_b
                tr.exit_reason = action
                tr.gross_return = float(gross)
                tr.net_return = float(net)

    # 마지막에 미청산 OPEN trade 있으면 제거(R0 에선 close 되지 않은 trade 미집계)
    trades = [t for t in trades if t.exit_reason != "OPEN"]
    return trades


# ── 메트릭 / 사전확정 평가 ────────────────────────────────────────
def _compute_pf(returns: Sequence[float]) -> float:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def _compute_sharpe(returns: Sequence[float]) -> float:
    arr = np.asarray(returns, dtype=float)
    if len(arr) < 2:
        return 0.0
    std = arr.std(ddof=1)
    if std <= 0:
        return 0.0
    return float(arr.mean() / std * np.sqrt(len(arr)))


def _compute_mdd(equity_curve: Sequence[float]) -> float:
    arr = np.asarray(equity_curve, dtype=float)
    if len(arr) == 0:
        return 0.0
    running_max = np.maximum.accumulate(arr)
    drawdown = (arr - running_max) / np.where(running_max == 0, 1, running_max)
    return float(-drawdown.min())


def _equity_curve(trades_sorted: List[PairTrade]) -> List[float]:
    """단순 균등 가중치 누적 — 1 페어당 1 unit. 동시 보유는 곱셈식 누적."""
    eq = [1.0]
    cur = 1.0
    for t in trades_sorted:
        cur = cur * (1.0 + t.net_return)
        eq.append(cur)
    return eq


def evaluate_r0(trades: List[PairTrade]) -> Tuple[List[R0Criterion], Dict[str, float]]:
    """R0 7 기준 평가."""
    crits: List[R0Criterion] = []
    metrics: Dict[str, float] = {}

    n_trades = len(trades)
    crits.append(R0Criterion(
        name="n_trades>=200", passed=n_trades >= R0_MIN_TRADES,
        value=float(n_trades), threshold=float(R0_MIN_TRADES),
        detail=f"completed pair trades = {n_trades}",
    ))

    net_returns = [t.net_return for t in trades]
    pf = _compute_pf(net_returns)
    sharpe = _compute_sharpe(net_returns)
    mean_ret = float(np.mean(net_returns)) if net_returns else 0.0

    metrics.update({"pf": pf, "sharpe": sharpe, "mean_trade_return": mean_ret})

    crits.append(R0Criterion(
        name="pf>=1.3", passed=pf >= R0_MIN_PF,
        value=pf, threshold=R0_MIN_PF,
        detail=f"profit factor (net of {ROUND_TRIP_COST*100:.2f}% rt cost) = {pf:.3f}",
    ))
    crits.append(R0Criterion(
        name="sharpe>1.0", passed=sharpe > R0_MIN_SHARPE,
        value=sharpe, threshold=R0_MIN_SHARPE,
        detail=f"trade-level Sharpe (sqrt(N)·mean/std) = {sharpe:.2f}",
    ))

    # 시간순 정렬 equity curve → MDD
    trades_sorted = sorted(trades, key=lambda x: x.exit_ts)
    eq = _equity_curve(trades_sorted)
    mdd = _compute_mdd(eq)
    metrics["max_dd"] = mdd
    crits.append(R0Criterion(
        name="max_dd<=12%", passed=mdd <= R0_MAX_MDD,
        value=mdd, threshold=R0_MAX_MDD,
        detail=f"Max drawdown of equity curve = {mdd*100:.1f}%",
    ))

    # 횡단면: 페어별 net_return 합계 양(+) 비율
    pair_pnl: Dict[str, float] = {}
    for t in trades:
        pair_pnl[t.pair_key] = pair_pnl.get(t.pair_key, 0.0) + t.net_return
    if pair_pnl:
        n_pairs = len(pair_pnl)
        pos_share = sum(1 for v in pair_pnl.values() if v > 0) / n_pairs
        metrics["cross_pair_pos_share"] = pos_share
        crits.append(R0Criterion(
            name="cross_pair_positive>=50%", passed=pos_share >= R0_MIN_CROSS_PAIR_POS_SHARE,
            value=pos_share, threshold=R0_MIN_CROSS_PAIR_POS_SHARE,
            detail=f"net positive pairs = {pos_share*100:.0f}% (n_pairs={n_pairs})",
        ))
        # 단일 페어 기여
        total_abs = sum(abs(v) for v in pair_pnl.values())
        if total_abs > 0:
            max_share = max(abs(v) for v in pair_pnl.values()) / total_abs
        else:
            max_share = 0.0
        metrics["single_pair_contribution"] = max_share
        crits.append(R0Criterion(
            name="single_pair_contrib<25%", passed=max_share < R0_MAX_SINGLE_PAIR_CONTRIB,
            value=max_share, threshold=R0_MAX_SINGLE_PAIR_CONTRIB,
            detail=f"single-pair |return| share max = {max_share*100:.1f}%",
        ))
    else:
        metrics["cross_pair_pos_share"] = 0.0
        metrics["single_pair_contribution"] = 1.0
        crits.extend([
            R0Criterion(name="cross_pair_positive>=50%", passed=False, value=0.0,
                         threshold=R0_MIN_CROSS_PAIR_POS_SHARE, detail="no trades"),
            R0Criterion(name="single_pair_contrib<25%", passed=False, value=1.0,
                         threshold=R0_MAX_SINGLE_PAIR_CONTRIB, detail="no trades"),
        ])

    return crits, metrics


# ── 엔트리 ────────────────────────────────────────────────────────
def run_r0(
    top_n: int = 50,
    since_iso: Optional[str] = None,
    oos_start_iso: Optional[str] = None,
    selector_cfg: Optional[PairSelectorConfig] = None,
    arb_cfg: Optional[PairArbConfig] = None,
    client=None,
    run_id_prefix: str = "pair_arb_r0",
) -> PairR0Report:
    """R0 전체 러너.

    Args:
        top_n: 유니버스 상위 N.
        since_iso: 데이터 슬라이스 시작.
        oos_start_iso: 지정 시 OOS subset 만 평가(R1 모드). 페어 선정은 oos_start 직전까지의 데이터로 1회.
        selector_cfg/arb_cfg: 사전확정 임계.
        client: Supabase 클라이언트.
        run_id_prefix: 식별자.

    Returns:
        PairR0Report.
    """
    started = datetime.now(timezone.utc)
    selector_cfg = selector_cfg or PairSelectorConfig()
    arb_cfg = arb_cfg or PairArbConfig()
    client = client if client is not None else _build_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client 미설정")

    # 1) 유니버스
    syms = _fetch_universe_symbols(client, top_n=top_n)
    logger.info("[PairArb] universe symbols=%d", len(syms))

    # 2) ohlcv 일괄 로드
    ohlcv_dict = _load_ohlcv_dict(client, syms, since_iso=since_iso)
    logger.info("[PairArb] ohlcv loaded for %d symbols", len(ohlcv_dict))

    if len(ohlcv_dict) < 2:
        logger.warning("[PairArb] insufficient symbols (%d) for pair selection", len(ohlcv_dict))
        return _empty_report(started, "insufficient symbols", run_id_prefix)

    # 3) 페어 선정 — IS 끝(또는 oos_start) 직전까지 데이터만 사용
    if oos_start_iso:
        selection_cutoff = pd.Timestamp(oos_start_iso, tz="UTC")
    else:
        # OOS 미지정 시 전체 데이터로 선정(=IS 평가만)
        # 시뮬레이션이 *시계열 전체*에 걸쳐 진행되므로 룩어헤드 측면에서 *불완전 IS*.
        # R0 의 honest 평가 위해 selection cutoff = 데이터 끝 60일 전 으로 둠.
        max_ts_all = max(df.index.max() for df in ohlcv_dict.values())
        selection_cutoff = max_ts_all - pd.Timedelta(days=60)

    ohlcv_for_selection = {
        s: df[df.index < selection_cutoff].tail(selector_cfg.lookback_days * 24)
        for s, df in ohlcv_dict.items()
        if len(df[df.index < selection_cutoff]) >= selector_cfg.min_overlap_bars
    }
    logger.info("[PairArb] selecting pairs from %d symbols (cutoff=%s)",
                len(ohlcv_for_selection), selection_cutoff)
    qualified = select_pairs(ohlcv_for_selection, selector_cfg)
    logger.info("[PairArb] qualified pairs = %d", len(qualified))

    # 4) 시뮬레이션 — 자격 페어 각각
    z_lookback_bars = arb_cfg.lookback_days * 24
    all_trades: List[PairTrade] = []
    for i, cand in enumerate(qualified, 1):
        ohlcv_a = ohlcv_dict.get(cand.symbol_a)
        ohlcv_b = ohlcv_dict.get(cand.symbol_b)
        if ohlcv_a is None or ohlcv_b is None:
            continue
        # 시뮬레이션 시작점:
        #   - OOS 모드: oos_start 부터(z-window 는 그 이전 데이터 사용 OK)
        #   - IS 모드: selection_cutoff 부터(룩어헤드 일관성)
        aligned_index = ohlcv_a.index.intersection(ohlcv_b.index)
        if len(aligned_index) < z_lookback_bars + 100:
            continue
        sim_start_ts = pd.Timestamp(oos_start_iso, tz="UTC") if oos_start_iso else selection_cutoff
        try:
            start_pos = aligned_index.searchsorted(sim_start_ts)
        except Exception:
            start_pos = z_lookback_bars
        start_pos = max(start_pos, z_lookback_bars)
        # 같은 aligned index 로 ohlcv_a, ohlcv_b 를 재정렬
        a_aligned = ohlcv_a.reindex(aligned_index)
        b_aligned = ohlcv_b.reindex(aligned_index)
        trades = _simulate_pair(cand, a_aligned, b_aligned, start_pos, arb_cfg, z_lookback_bars)
        all_trades.extend(trades)
        if i % 20 == 0:
            logger.info("[PairArb] simulated %d/%d pairs, trades so far=%d",
                        i, len(qualified), len(all_trades))

    logger.info("[PairArb] total trades=%d", len(all_trades))

    # 5) 평가
    criteria, metrics = evaluate_r0(all_trades)
    passed_all = all(c.passed for c in criteria)

    finished = datetime.now(timezone.utc)
    run_id = f"{run_id_prefix}_{started.strftime('%Y%m%dT%H%M%SZ')}"

    return PairR0Report(
        run_id=run_id,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        n_qualified_pairs=len(qualified),
        n_trades=len(all_trades),
        pf=metrics.get("pf", 0.0),
        sharpe=metrics.get("sharpe", 0.0),
        mean_trade_return=metrics.get("mean_trade_return", 0.0),
        max_dd=metrics.get("max_dd", 0.0),
        cross_pair_pos_share=metrics.get("cross_pair_pos_share", 0.0),
        single_pair_contribution=metrics.get("single_pair_contribution", 0.0),
        criteria=criteria,
        passed=passed_all,
        notes=f"universe={len(syms)}, ohlcv_loaded={len(ohlcv_dict)}, qualified={len(qualified)}, "
               f"selection_cutoff={selection_cutoff}",
    )


def _empty_report(started: datetime, reason: str, prefix: str) -> PairR0Report:
    finished = datetime.now(timezone.utc)
    run_id = f"{prefix}_{started.strftime('%Y%m%dT%H%M%SZ')}"
    return PairR0Report(
        run_id=run_id,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        n_qualified_pairs=0, n_trades=0, pf=0.0, sharpe=0.0,
        mean_trade_return=0.0, max_dd=0.0,
        cross_pair_pos_share=0.0, single_pair_contribution=1.0,
        criteria=[], passed=False, notes=reason,
    )


def record_to_supabase(report: PairR0Report, persist=None,
                       operator_notes: str = "") -> bool:
    """결과를 Supabase `backtest_runs` 테이블에 적재."""
    try:
        from data.persistence import SupabasePersistence
        p = persist or SupabasePersistence()
        row = {
            "run_id": report.run_id,
            "started_at": report.started_at,
            "completed_at": report.finished_at,
            "config_json": {"strategy": "pair_stat_arb_r0"},
            "summary_json": report.to_jsonable(),
            "verdict": "PASS" if report.passed else "FAIL",
            "operator_notes": operator_notes or report.notes,
        }
        return p.insert("backtest_runs", row)
    except Exception as e:  # noqa: BLE001
        logger.warning("[PairArb] backtest_runs 적재 실패: %s", e)
        return False
