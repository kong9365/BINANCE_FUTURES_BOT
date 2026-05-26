"""
analytics/ccs_lite_backtest.py
=====================================================================
CCS-Lite v1.1 R0 (과거 backtest, 1세션·무료) 러너.

목적:
  Supabase 2년 누적 데이터(ohlcv + oi_history + funding_history)에서
  Leverage Flush 6 조건 이벤트를 추출하고, 사전 통과 가능한 kill gate(BTC
  risk-off + symbol/BTC trend EMA) 를 적용해 *STRONG* 후보로 남은 이벤트의
  +4h / +24h forward return 을 측정한다. 사전확정 R0 합격 기준은:

    1) STRONG 이벤트 표본 ≥ 200
    2) +4h 평균 > +0.5%
    3) +4h Sharpe > 1.0
    4) 단조성: STRONG > NEUTRAL (+4h AND +24h 모두)
    5) 횡단면 강건성: ≥60% 종목 양(+)
    6) 단일 종목 기여 < 30%
    7) BTC risk-off active subset 음수(sanity)

설계:
  - I/O는 *입구 한 군데*에서만(Supabase 읽기). 평가는 순수 함수로.
  - 라이브와 동일 모듈(`strategy.leverage_flush` + `strategy.ccs_lite`) 호출
    → backtest-live 괴리 없음(돌파 전략과 동일 원칙).
  - 룩어헤드 차단: forward return 은 이벤트 봉 *다음* 봉 시가 진입 기준.
  - 사전확정 합격 평가는 evaluate()로 자동 산출 → 보고서·`backtest_runs`에 적재.

R0 시나리오(이벤트 부족 시):
  - 표본 < 200 이면 자동 FAIL(다른 기준은 평가 안 함). 정직 종료.
  - 표본 ≥ 200 이지만 +4h 평균 미달이면 FAIL. 등등.

R1 (OOS) 호출 방식:
  run_backtest(..., oos_start_iso="2025-11-01T00:00:00Z") 로 OOS subset 결과만 별도
  evaluate(). R0 동안 OOS 결과 절대 미열람 약속.
=====================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from strategy.btc_risk_off import BTCRiskOffConfig
from strategy.ccs_lite import (
    CCSLiteConfig,
    STATE_NEUTRAL,
    STATE_STRONG_SQUEEZE,
    STATE_STRONG_TREND,
    classify_state,
    gate_btc_trend,
    gate_symbol_trend,
)
from strategy.leverage_flush import LeverageFlushConfig, detect_events

logger = logging.getLogger(__name__)


# ── R0 사전확정 기준 (계획서 §사전확정 합격 기준 R0) ──────────────
R0_MIN_EVENTS = 200
R0_MIN_MEAN_4H = 0.005           # +0.5%
R0_MIN_SHARPE_4H = 1.0
R0_MIN_CROSS_SECTIONAL_POS = 0.60   # ≥60% 종목 양(+)
R0_MAX_SINGLE_CONTRIB = 0.30        # 단일 종목 기여 < 30%
R0_MIN_NEUTRAL_SAMPLE = 50          # 단조성 비교용 NEUTRAL 최소 표본


@dataclass
class R0Criterion:
    """단일 사전확정 기준 평가 결과."""

    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass
class R0Report:
    """R0 전체 평가 리포트."""

    run_id: str
    started_at: str
    finished_at: str
    n_strong: int
    n_neutral: int
    n_blocked: int
    mean_4h: float
    median_4h: float
    sharpe_4h: float
    mean_24h: float
    median_24h: float
    cross_sectional_pos_share: float
    single_symbol_contribution: float
    btc_riskoff_subset_mean_4h: float
    btc_riskoff_subset_n: int
    monotonicity_4h: bool
    monotonicity_24h: bool
    criteria: List[R0Criterion] = field(default_factory=list)
    passed: bool = False
    notes: str = ""

    def to_jsonable(self) -> Dict[str, Any]:
        d = asdict(self)
        d["criteria"] = [asdict(c) for c in self.criteria]
        return d


# ── Supabase 읽기 helpers ─────────────────────────────────────────
def _build_supabase_client():
    """env(SUPABASE_URL/SERVICE_ROLE_KEY) 로 클라이언트 생성. 미설정 시 None."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.warning("[CCSBacktest] SUPABASE_URL/KEY 미설정")
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:  # noqa: BLE001
        logger.error("[CCSBacktest] supabase 클라이언트 생성 실패: %s", e)
        return None


def _fetch_distinct_symbols(client, interval: str = "1h") -> List[str]:
    """OI 데이터가 있는 distinct symbol 리스트. 보호종목 자동 제외.

    노트: ohlcv 가 1.98M 행이라 단순 select 로 distinct 추출 시 첫 페이지(보통
    1k 행 제한)만 들어와 부분 결과가 된다. LF 트리거가 OI 를 *필수*로 요구하므로
    oi_history 에서 distinct symbol 만 추출하는 것이 정합적이다(OI 없는 심볼은
    어차피 0 이벤트).
    """
    from config.settings import PAIR_WHITELIST_CONFIG
    protected = set(PAIR_WHITELIST_CONFIG.protected_symbols)
    raw = client.table("oi_history").select("symbol").eq("period", interval).limit(200_000).execute()
    syms = sorted({r["symbol"] for r in (raw.data or []) if r.get("symbol")})
    return [s for s in syms if s not in protected]


_PAGE = 1000   # Supabase PostgREST 기본 max-rows. 더 큰 limit 도 cap.


def _fetch_paged(query_factory, page_size: int = _PAGE, max_pages: int = 200) -> List[dict]:
    """페이지네이션 헬퍼 — query_factory() 가 새 쿼리 빌더 반환.

    `.range(offset, offset+page_size-1)` 로 chunk 별 호출 후 합산.
    """
    out: List[dict] = []
    for p in range(max_pages):
        start = p * page_size
        end = start + page_size - 1
        res = query_factory().range(start, end).execute()
        chunk = res.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
    return out


def _fetch_ohlcv(client, symbol: str, interval: str = "1h",
                 since_iso: Optional[str] = None) -> pd.DataFrame:
    """단일 심볼의 ohlcv 시계열을 DataFrame 으로(페이지네이션).

    Returns:
        index=ts(UTC, tz-aware) 정렬, cols=[open,high,low,close,volume,taker_buy_base_asset_volume].
    """
    def _q():
        q = (
            client.table("ohlcv")
            .select("ts,open,high,low,close,volume,taker_buy_base")
            .eq("symbol", symbol)
            .eq("interval", interval)
            .order("ts")
        )
        if since_iso:
            q = q.gte("ts", since_iso)
        return q
    data = _fetch_paged(_q)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df = df.rename(columns={"taker_buy_base": "taker_buy_base_asset_volume"})
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    for c in ["open", "high", "low", "close", "volume", "taker_buy_base_asset_volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _fetch_oi(client, symbol: str, period: str = "1h",
              since_iso: Optional[str] = None) -> pd.Series:
    """단일 심볼 OI 시계열. index=ts(UTC, tz-aware). 페이지네이션 포함."""
    def _q():
        q = (
            client.table("oi_history")
            .select("ts,open_interest")
            .eq("symbol", symbol)
            .eq("period", period)
            .order("ts")
        )
        if since_iso:
            q = q.gte("ts", since_iso)
        return q
    data = _fetch_paged(_q)
    if not data:
        return pd.Series(dtype=float)
    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df = df.sort_values("ts").drop_duplicates("ts")
    return pd.Series(
        pd.to_numeric(df["open_interest"], errors="coerce").to_numpy(),
        index=df["ts"], name="open_interest",
    ).dropna()


# ── 평가 보조 ─────────────────────────────────────────────────────
def _forward_returns(events: pd.DataFrame, ohlcv: pd.DataFrame,
                     horizons_bars: Sequence[int] = (4, 24)) -> pd.DataFrame:
    """이벤트별 +h봉 forward return (다음 봉 시가 진입, h봉 종가 청산).

    룩어헤드 차단: 진입가 = i+1 봉 시가. 청산가 = i+h 봉 종가. 둘 다 *이벤트
    이후* 데이터.
    """
    if events.empty:
        out = events.copy()
        for h in horizons_bars:
            out[f"fwd_{h}h"] = []
        return out
    res = events.copy()
    idx_pos = {ts: i for i, ts in enumerate(ohlcv.index)}
    opens = ohlcv["open"].to_numpy(float)
    closes = ohlcv["close"].to_numpy(float)
    n = len(ohlcv)
    for h in horizons_bars:
        vals: List[float] = []
        for ts in res.index:
            i = idx_pos.get(ts)
            if i is None or i + 1 >= n or i + h >= n:
                vals.append(np.nan); continue
            entry = opens[i + 1]
            exit_p = closes[i + h]
            if entry <= 0:
                vals.append(np.nan)
            else:
                vals.append((exit_p - entry) / entry)
        res[f"fwd_{h}h"] = vals
    return res


def _btc_risk_off_active_at(btc_ohlcv: pd.DataFrame, ts: pd.Timestamp,
                            cfg: Optional[BTCRiskOffConfig] = None) -> bool:
    """ts 직전 BTC 1h 변화 ≤ -1.2% 이면 risk-off active(이벤트 시점 기준)."""
    cfg = cfg or BTCRiskOffConfig()
    if btc_ohlcv.empty or ts not in btc_ohlcv.index:
        return False
    i = btc_ohlcv.index.get_loc(ts)
    if i < 1:
        return False
    prev = float(btc_ohlcv["close"].iloc[i - 1])
    now = float(btc_ohlcv["close"].iloc[i])
    if prev <= 0:
        return False
    drop = (now - prev) / prev
    return drop <= -cfg.drop_threshold_pct


def _attach_states(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    btc_ohlcv_1h: pd.DataFrame,
    btc_closes_4h: pd.Series,
    ccs_cfg: CCSLiteConfig,
) -> pd.DataFrame:
    """이벤트별 kill gate 통과 여부 + state 라벨.

    Returns:
        events + 컬럼: btc_risk_off, btc_trend_block, sym_trend_block,
        passed_gates(bool), state(str).
    """
    out = events.copy()
    btc_block, sym_block, ro_active, states = [], [], [], []
    idx_pos_1h = {ts: i for i, ts in enumerate(ohlcv.index)}
    sym_closes_arr = ohlcv["close"].to_numpy(float)

    for ts in out.index:
        # BTC risk-off
        ro = _btc_risk_off_active_at(btc_ohlcv_1h, ts)
        ro_active.append(ro)

        # symbol trend EMA (이벤트 봉까지의 종가만 사용 — 룩어헤드 없음)
        i_sym = idx_pos_1h.get(ts)
        sym_window = sym_closes_arr[: i_sym + 1] if i_sym is not None else np.array([])
        sym_block_now = gate_symbol_trend(sym_window.tolist(), ccs_cfg)
        sym_block.append(sym_block_now)

        # BTC trend EMA (4h, 이벤트 시각까지의 4h 종가)
        # .loc[:ts] 슬라이스로 룩어헤드 없이 직전까지 추출(tz-aware index 안전).
        btc_window_series = btc_closes_4h.loc[:ts]
        btc_window = btc_window_series.tolist()
        btc_block_now = gate_btc_trend(btc_window, ccs_cfg)
        btc_block.append(btc_block_now)

        # State 분류 (gate 통과 후만 의미 있음 — 일단 모든 이벤트에 라벨링)
        # squeeze 컴포넌트는 R0 사실상 미사용(funding 데이터 별도 로드 필요).
        # → NEUTRAL/STRONG_TREND 만 라벨링(squeeze 는 R1 이후 funding 통합 시).
        try:
            s = classify_state(
                btc_closes_4h=btc_window,
                symbol_closes_1h=sym_window.tolist(),
                prev_funding=None,
                current_funding=None,
                recent_oi_change_pct=None,
                cfg=ccs_cfg,
            )
        except Exception:  # noqa: BLE001
            s = STATE_NEUTRAL
        states.append(s)

    out["btc_risk_off"] = ro_active
    out["btc_trend_block"] = btc_block
    out["sym_trend_block"] = sym_block
    out["passed_gates"] = ~(out["btc_risk_off"] | out["btc_trend_block"] | out["sym_trend_block"])
    out["state"] = states
    return out


def _resample_4h_close(ohlcv_1h: pd.DataFrame) -> pd.Series:
    """1h ohlcv → 4h 종가 시리즈.

    closed='left', label='right' 컨벤션 — bin [t, t+4h) 의 종가를 t+4h 라벨로.
    예: 1h bar [00:00, 04:00) → label 04:00, close = 03:00 봉의 close.
    이 컨벤션은 *event 시각 ts* 에서 .loc[:ts] 슬라이스 시 자동으로 *그 이전*
    4h 봉만 가져오므로 룩어헤드 없음(과거에 *닫힌* 4h 봉만 사용).
    """
    if ohlcv_1h.empty:
        return pd.Series(dtype=float)
    r = ohlcv_1h["close"].resample("4h", label="right", closed="left").last().dropna()
    return r


# ── 사전확정 평가 ──────────────────────────────────────────────────
def evaluate_r0(strong: pd.DataFrame, neutral: pd.DataFrame,
                ro_subset: pd.DataFrame) -> List[R0Criterion]:
    """R0 합격 기준 7개를 평가해 R0Criterion 리스트로 반환."""
    crits: List[R0Criterion] = []

    if strong.empty or "fwd_4h" not in strong.columns:
        n_strong = 0
    else:
        n_strong = len(strong.dropna(subset=["fwd_4h"]))
    crits.append(R0Criterion(
        name="n_strong>=200", passed=n_strong >= R0_MIN_EVENTS,
        value=float(n_strong), threshold=float(R0_MIN_EVENTS),
        detail=f"STRONG 이벤트 forward 4h 측정 가능 표본 = {n_strong}",
    ))

    fwd4 = strong["fwd_4h"].dropna() if (not strong.empty and "fwd_4h" in strong.columns) else pd.Series([], dtype=float)
    mean4 = float(fwd4.mean()) if not fwd4.empty else 0.0
    std4 = float(fwd4.std(ddof=1)) if len(fwd4) > 1 else 0.0
    sharpe4 = (mean4 / std4) * np.sqrt(len(fwd4)) if std4 > 0 else 0.0

    crits.append(R0Criterion(
        name="mean_4h>0.5%", passed=mean4 > R0_MIN_MEAN_4H,
        value=mean4, threshold=R0_MIN_MEAN_4H,
        detail=f"mean +4h = {mean4*100:+.3f}%",
    ))
    crits.append(R0Criterion(
        name="sharpe_4h>1.0", passed=sharpe4 > R0_MIN_SHARPE_4H,
        value=sharpe4, threshold=R0_MIN_SHARPE_4H,
        detail=f"sample Sharpe (sqrt(N)·mean/std) = {sharpe4:.2f}",
    ))

    # 단조성 (mean 비교)
    if not neutral.empty and "fwd_4h" in neutral.columns and "fwd_24h" in neutral.columns:
        nm4 = neutral["fwd_4h"].dropna()
        nm24 = neutral["fwd_24h"].dropna()
        sm4_mean = mean4
        sm24_mean = float(strong["fwd_24h"].dropna().mean()) if "fwd_24h" in strong.columns else 0.0
        n4_mean = float(nm4.mean()) if not nm4.empty else 0.0
        n24_mean = float(nm24.mean()) if not nm24.empty else 0.0
        mono4 = sm4_mean > n4_mean and len(nm4) >= R0_MIN_NEUTRAL_SAMPLE
        mono24 = sm24_mean > n24_mean and len(nm24) >= R0_MIN_NEUTRAL_SAMPLE
        crits.append(R0Criterion(
            name="monotonicity_4h_24h",
            passed=bool(mono4 and mono24),
            value=float(int(mono4 and mono24)),
            threshold=1.0,
            detail=(
                f"STRONG +4h {sm4_mean*100:+.3f}% vs NEUTRAL +4h {n4_mean*100:+.3f}% | "
                f"STRONG +24h {sm24_mean*100:+.3f}% vs NEUTRAL +24h {n24_mean*100:+.3f}%"
            ),
        ))
    else:
        crits.append(R0Criterion(
            name="monotonicity_4h_24h", passed=False,
            value=0.0, threshold=1.0, detail="NEUTRAL 표본 없음 — 단조성 평가 불가",
        ))

    # 횡단면 강건성 (≥60% 종목 양(+))
    if "symbol" in strong.columns and not strong.empty:
        per_sym = strong.groupby("symbol")["fwd_4h"].mean().dropna()
        if len(per_sym) > 0:
            pos_share = float((per_sym > 0).mean())
        else:
            pos_share = 0.0
        crits.append(R0Criterion(
            name="cross_sectional_pos>=60%", passed=pos_share >= R0_MIN_CROSS_SECTIONAL_POS,
            value=pos_share, threshold=R0_MIN_CROSS_SECTIONAL_POS,
            detail=f"양(+) +4h 평균인 종목 비율 = {pos_share*100:.1f}% (n_symbols={len(per_sym)})",
        ))
        # 단일 종목 기여
        contrib = strong.groupby("symbol")["fwd_4h"].sum().abs()
        total = float(contrib.sum())
        max_share = float(contrib.max() / total) if total > 0 else 0.0
        crits.append(R0Criterion(
            name="single_symbol_contrib<30%", passed=max_share < R0_MAX_SINGLE_CONTRIB,
            value=max_share, threshold=R0_MAX_SINGLE_CONTRIB,
            detail=f"단일 종목 누적 |return| 비중 최댓값 = {max_share*100:.1f}%",
        ))
    else:
        crits.extend([
            R0Criterion(name="cross_sectional_pos>=60%", passed=False, value=0.0,
                         threshold=R0_MIN_CROSS_SECTIONAL_POS, detail="symbol 컬럼 없음"),
            R0Criterion(name="single_symbol_contrib<30%", passed=False, value=1.0,
                         threshold=R0_MAX_SINGLE_CONTRIB, detail="symbol 컬럼 없음"),
        ])

    # BTC risk-off subset 음수(sanity)
    ro4 = ro_subset["fwd_4h"].dropna() if not ro_subset.empty and "fwd_4h" in ro_subset.columns else pd.Series([], dtype=float)
    ro_mean = float(ro4.mean()) if not ro4.empty else 0.0
    crits.append(R0Criterion(
        name="btc_riskoff_subset<=0", passed=(len(ro4) == 0) or (ro_mean <= 0.0),
        value=ro_mean, threshold=0.0,
        detail=f"BTC risk-off active 시점 +4h mean = {ro_mean*100:+.3f}% (n={len(ro4)})",
    ))

    return crits


# ── 엔트리 ────────────────────────────────────────────────────────
def run_r0(
    symbols: Optional[Sequence[str]] = None,
    interval: str = "1h",
    since_iso: Optional[str] = None,
    oos_start_iso: Optional[str] = None,
    lf_cfg: Optional[LeverageFlushConfig] = None,
    ccs_cfg: Optional[CCSLiteConfig] = None,
    client=None,
    run_id_prefix: str = "ccs_lite_r0",
) -> R0Report:
    """R0 전체 러너. Supabase 데이터 → 이벤트 → 평가 → 리포트.

    Args:
        symbols: 평가할 심볼 리스트. None 이면 ohlcv 에 있는 모든 비보호 심볼.
        interval: ohlcv interval(기본 1h).
        since_iso: 이 시각 이후 데이터만(테스트/IS 제한). None=전체.
        oos_start_iso: OOS subset 분리 평가용. None 이면 전체로 평가.
        lf_cfg/ccs_cfg: 사전확정 임계. 기본값 사용 권장(자유도 0 원칙).
        client: Supabase 클라이언트(테스트 mock 가능). None 이면 env 로 생성.
        run_id_prefix: backtest_runs 식별자 prefix.

    Returns:
        R0Report (criteria 포함). report.passed 가 최종 PASS/FAIL.
    """
    started = datetime.now(timezone.utc)
    lf_cfg = lf_cfg or LeverageFlushConfig()
    ccs_cfg = ccs_cfg or CCSLiteConfig()
    client = client if client is not None else _build_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client 미설정 — SUPABASE_URL/KEY 확인")

    # 1) BTC 기준 시계열(1h + 4h close)
    logger.info("[CCSBacktest] BTC 1h ohlcv 로드")
    btc_1h = _fetch_ohlcv(client, "BTCUSDT", interval=interval, since_iso=since_iso)
    btc_4h_closes = _resample_4h_close(btc_1h)
    btc_returns = btc_1h["close"].pct_change() if not btc_1h.empty else pd.Series(dtype=float)

    # 2) 유니버스 결정 (보호종목 제외)
    if symbols is None:
        symbols = _fetch_distinct_symbols(client, interval=interval)
    logger.info("[CCSBacktest] 유니버스 %d 심볼", len(symbols))

    all_strong: List[pd.DataFrame] = []
    all_neutral: List[pd.DataFrame] = []
    all_blocked: List[pd.DataFrame] = []
    all_ro_subset: List[pd.DataFrame] = []
    n_skipped = 0

    for idx, sym in enumerate(symbols, 1):
        try:
            ohlcv = _fetch_ohlcv(client, sym, interval=interval, since_iso=since_iso)
            if ohlcv.empty or len(ohlcv) < 250:
                n_skipped += 1; continue
            oi = _fetch_oi(client, sym, period=interval, since_iso=since_iso)
            if oi.empty:
                n_skipped += 1; continue

            events = detect_events(ohlcv, oi, btc_returns, symbol=sym, cfg=lf_cfg)
            if events.empty:
                continue
            events = _attach_states(events, ohlcv, btc_1h, btc_4h_closes, ccs_cfg)
            events = _forward_returns(events, ohlcv, horizons_bars=(4, 24))

            # OOS 분리
            if oos_start_iso:
                cutoff = pd.Timestamp(oos_start_iso, tz="UTC")
                events = events[events.index >= cutoff]
                if events.empty:
                    continue

            # 분류:
            #  - STRONG = passed_gates AND state in (STRONG_TREND, STRONG_SQUEEZE)
            #  - NEUTRAL = passed_gates AND state == NEUTRAL
            #  - BLOCKED = NOT passed_gates
            strong_mask = events["passed_gates"] & (
                events["state"].isin([STATE_STRONG_TREND, STATE_STRONG_SQUEEZE])
            )
            neutral_mask = events["passed_gates"] & (events["state"] == STATE_NEUTRAL)
            blocked_mask = ~events["passed_gates"]
            ro_mask = events["btc_risk_off"]

            all_strong.append(events[strong_mask])
            all_neutral.append(events[neutral_mask])
            all_blocked.append(events[blocked_mask])
            all_ro_subset.append(events[ro_mask])
        except Exception as e:  # noqa: BLE001
            logger.warning("[CCSBacktest] %s 처리 실패 — 스킵: %s", sym, e)
            n_skipped += 1
            continue
        if idx % 30 == 0:
            logger.info("[CCSBacktest] ... %d/%d", idx, len(symbols))

    strong = pd.concat(all_strong, axis=0) if all_strong else pd.DataFrame()
    neutral = pd.concat(all_neutral, axis=0) if all_neutral else pd.DataFrame()
    blocked = pd.concat(all_blocked, axis=0) if all_blocked else pd.DataFrame()
    ro_subset = pd.concat(all_ro_subset, axis=0) if all_ro_subset else pd.DataFrame()

    # 메트릭 집계
    fwd4 = strong["fwd_4h"].dropna() if "fwd_4h" in strong.columns else pd.Series([], dtype=float)
    fwd24 = strong["fwd_24h"].dropna() if "fwd_24h" in strong.columns else pd.Series([], dtype=float)
    mean_4h = float(fwd4.mean()) if not fwd4.empty else 0.0
    median_4h = float(fwd4.median()) if not fwd4.empty else 0.0
    std_4h = float(fwd4.std(ddof=1)) if len(fwd4) > 1 else 0.0
    sharpe_4h = (mean_4h / std_4h) * np.sqrt(len(fwd4)) if std_4h > 0 else 0.0
    mean_24h = float(fwd24.mean()) if not fwd24.empty else 0.0
    median_24h = float(fwd24.median()) if not fwd24.empty else 0.0

    if "symbol" in strong.columns and not strong.empty:
        per_sym = strong.groupby("symbol")["fwd_4h"].mean().dropna()
        cs_pos = float((per_sym > 0).mean()) if len(per_sym) else 0.0
        contrib = strong.groupby("symbol")["fwd_4h"].sum().abs()
        total = float(contrib.sum())
        single_contrib = float(contrib.max() / total) if total > 0 else 0.0
    else:
        cs_pos = 0.0; single_contrib = 0.0

    ro4 = ro_subset["fwd_4h"].dropna() if not ro_subset.empty and "fwd_4h" in ro_subset.columns else pd.Series([], dtype=float)
    ro_mean = float(ro4.mean()) if not ro4.empty else 0.0
    ro_n = int(len(ro4))

    # 단조성
    nm4_series = neutral["fwd_4h"].dropna() if not neutral.empty and "fwd_4h" in neutral.columns else pd.Series([], dtype=float)
    nm24_series = neutral["fwd_24h"].dropna() if not neutral.empty and "fwd_24h" in neutral.columns else pd.Series([], dtype=float)
    mono_4h = bool(mean_4h > (float(nm4_series.mean()) if not nm4_series.empty else 0.0) and len(nm4_series) >= R0_MIN_NEUTRAL_SAMPLE)
    mono_24h = bool(mean_24h > (float(nm24_series.mean()) if not nm24_series.empty else 0.0) and len(nm24_series) >= R0_MIN_NEUTRAL_SAMPLE)

    criteria = evaluate_r0(strong, neutral, ro_subset)
    passed_all = all(c.passed for c in criteria)

    finished = datetime.now(timezone.utc)
    run_id = f"{run_id_prefix}_{started.strftime('%Y%m%dT%H%M%SZ')}"

    return R0Report(
        run_id=run_id,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        n_strong=int(len(fwd4)),
        n_neutral=int(len(nm4_series)),
        n_blocked=int(len(blocked)),
        mean_4h=mean_4h,
        median_4h=median_4h,
        sharpe_4h=sharpe_4h,
        mean_24h=mean_24h,
        median_24h=median_24h,
        cross_sectional_pos_share=cs_pos,
        single_symbol_contribution=single_contrib,
        btc_riskoff_subset_mean_4h=ro_mean,
        btc_riskoff_subset_n=ro_n,
        monotonicity_4h=mono_4h,
        monotonicity_24h=mono_24h,
        criteria=criteria,
        passed=passed_all,
        notes=f"skipped={n_skipped}/{len(symbols)} symbols",
    )


def record_to_supabase(report: R0Report, persist=None,
                       operator_notes: str = "") -> bool:
    """결과를 Supabase `backtest_runs` 테이블에 적재(Best-effort).

    스키마(2026-05-23): id, run_id, started_at, completed_at, config_json,
    summary_json, verdict, operator_notes.
    """
    try:
        from data.persistence import SupabasePersistence
        p = persist or SupabasePersistence()
        row = {
            "run_id": report.run_id,
            "started_at": report.started_at,
            "completed_at": report.finished_at,
            "config_json": {"strategy": "ccs_lite_r0"},
            "summary_json": report.to_jsonable(),
            "verdict": "PASS" if report.passed else "FAIL",
            "operator_notes": operator_notes or report.notes,
        }
        return p.insert("backtest_runs", row)
    except Exception as e:  # noqa: BLE001
        logger.warning("[CCSBacktest] backtest_runs 적재 실패: %s", e)
        return False
