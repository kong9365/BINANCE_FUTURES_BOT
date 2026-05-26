"""4후보 3-Gate 통합 러너."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from analytics.verification.data_source import (
    build_ohlcv_client,
    list_local_symbols,
    load_universe_ohlcv,
    trading_universe_symbols,
)
from analytics.verification.decile_analysis import DecileResult, decile_analysis
from analytics.verification.feature_engineering import (
    features_1d_breakout,
    features_cross_sectional_mom,
    features_listing_effect,
)
from analytics.verification.listing_utils import fetch_onboard_dates, protected_symbols
from analytics.verification.gates import (
    GateResult,
    judge_backtest,
    judge_decile,
    judge_ic,
    overall_verdict,
)
from analytics.verification.ic_analysis import ICResult, best_ic, run_ic_panel
from analytics.verification.simple_backtest import (
    SimpleBTResult,
    backtest_1d_breakout_universe,
    backtest_cross_sectional,
    backtest_listing_universe,
)

logger = logging.getLogger(__name__)

CANDIDATES = {
    "1d_breakout": "1d 돌파 확장",
    "cross_sectional_mom": "횡단면 모멘텀",
    "btc_beta": "BTC-베타 추종",
    "funding_settlement": "펀딩 정산",
    "listing_effect": "신규 상장 효과",
}


@dataclass
class CandidateReport:
    candidate_id: str
    name: str
    gate1: GateResult
    gate2: GateResult
    gate3: GateResult
    verdict: str
    ic_rows: List[dict]
    decile: Optional[dict]
    backtest: dict
    notes: str = ""


def _gate_from_ic(ic: float) -> GateResult:
    val = 0.0 if ic is None or np.isnan(ic) else float(ic)
    return GateResult("IC", judge_ic(val), val, "Spearman IC (best feature)")


def _gate_from_decile(dec: Optional[DecileResult]) -> GateResult:
    if dec is None:
        return GateResult("Decile", "fail", 0.0, "insufficient data")
    st = judge_decile(dec.spread_pct, dec.monotonicity)
    return GateResult("Decile", st, dec.spread_pct, f"mono={dec.monotonicity:.3f}")


def _gate_from_bt(bt: SimpleBTResult) -> GateResult:
    st = judge_backtest(
        bt.profit_factor, bt.n_trades, bt.max_drawdown_pct,
        bt.avg_net_pct, bt.symbol_positive_pct,
    )
    return GateResult(
        "Backtest", st, bt.profit_factor,
        f"n={bt.n_trades} MDD={bt.max_drawdown_pct:.1f}% avg={bt.avg_net_pct:.3f}%",
    )


def _pool_frames(data: Dict[str, pd.DataFrame], skip_index: bool = True) -> pd.DataFrame:
    parts = []
    for sym, df in data.items():
        if skip_index and sym in ("BTCUSDT", "ETHUSDT"):
            continue
        parts.append(df)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts)


def eval_1d_breakout(data: Dict[str, pd.DataFrame]) -> CandidateReport:
    frames = [features_1d_breakout(df) for sym, df in data.items()
              if sym not in ("BTCUSDT", "ETHUSDT")]
    ic_rows = run_ic_panel(
        ["donchian_position", "ema_distance", "adx_14", "volume_ratio"],
        {"1d": "fwd_1d", "3d": "fwd_3d", "7d": "fwd_7d"},
        frames,
    )
    best = best_ic(ic_rows)
    g1 = _gate_from_ic(best.ic if best else float("nan"))
    pooled = _pool_frames({k: features_1d_breakout(v) for k, v in data.items()})
    dec = None
    if not pooled.empty:
        dec = decile_analysis(pooled["donchian_position"], pooled["fwd_1d"],
                              "donchian_position", "1d")
    g2 = _gate_from_decile(dec)
    bt = backtest_1d_breakout_universe(data)
    g3 = _gate_from_bt(bt)
    return CandidateReport(
        "1d_breakout", CANDIDATES["1d_breakout"], g1, g2, g3,
        overall_verdict(g1.status, g2.status, g3.status),
        [asdict(r) for r in ic_rows],
        asdict(dec) if dec else None,
        asdict(bt),
    )


def eval_cross_sectional_mom(data: Dict[str, pd.DataFrame]) -> CandidateReport:
    frames = [features_cross_sectional_mom(df) for sym, df in data.items()
              if sym not in ("BTCUSDT", "ETHUSDT")]
    ic_rows = run_ic_panel(
        ["momentum_30d", "momentum_skip_30d", "risk_adjusted_momentum"],
        {"7d": "fwd_7d"},
        frames,
    )
    best = best_ic(ic_rows)
    g1 = _gate_from_ic(best.ic if best else float("nan"))
    pooled = _pool_frames({k: features_cross_sectional_mom(v) for k, v in data.items()})
    dec = decile_analysis(
        pooled["risk_adjusted_momentum"], pooled["fwd_7d"],
        "risk_adjusted_momentum", "7d",
    ) if not pooled.empty else None
    g2 = _gate_from_decile(dec)
    bt = backtest_cross_sectional(data)
    g3 = _gate_from_bt(bt)
    return CandidateReport(
        "cross_sectional_mom", CANDIDATES["cross_sectional_mom"], g1, g2, g3,
        overall_verdict(g1.status, g2.status, g3.status),
        [asdict(r) for r in ic_rows],
        asdict(dec) if dec else None,
        asdict(bt),
    )


def eval_listing_effect(
    data: Dict[str, pd.DataFrame],
    onboard_map: Dict[str, pd.Timestamp],
) -> CandidateReport:
    skip = protected_symbols() | {"BTCUSDT", "ETHUSDT"}
    frames = []
    for sym, df in data.items():
        if sym in skip:
            continue
        od = onboard_map.get(sym)
        if od is None:
            continue
        f = features_listing_effect(df, od)
        if not f.empty:
            frames.append(f)
    if not frames:
        return CandidateReport(
            "listing_effect", CANDIDATES["listing_effect"],
            GateResult("IC", "fail", 0.0, "no listing window samples"),
            GateResult("Decile", "fail", 0.0, "no samples"),
            GateResult("Backtest", "fail", 0.0, "n=0"),
            "FAIL", [], None, {}, notes="상장 윈도우 표본 0",
        )
    ic_rows = run_ic_panel(
        ["days_since_listing", "return_since_listing", "first_week_return", "volume_ratio_listing_week"],
        {"1d": "fwd_1d", "7d": "fwd_7d"},
        frames,
    )
    best = best_ic(ic_rows)
    g1 = _gate_from_ic(best.ic if best else float("nan"))
    pooled = pd.concat(frames)
    dec = decile_analysis(
        pooled["first_week_return"].fillna(pooled["return_since_listing"]),
        pooled["fwd_7d"],
        "first_week_return", "7d",
    )
    g2 = _gate_from_decile(dec)
    bt = backtest_listing_universe(data, onboard_map)
    g3 = _gate_from_bt(bt)
    note = f"listing_frames={len(frames)} ic_n={sum(r.n for r in ic_rows)}"
    return CandidateReport(
        "listing_effect", CANDIDATES["listing_effect"], g1, g2, g3,
        overall_verdict(g1.status, g2.status, g3.status),
        [asdict(r) for r in ic_rows],
        asdict(dec) if dec else None,
        asdict(bt),
        notes=note,
    )


def eval_data_missing(candidate_id: str, reason: str) -> CandidateReport:
    name = CANDIDATES.get(candidate_id, candidate_id)
    fail = GateResult("n/a", "fail", 0.0, reason)
    return CandidateReport(
        candidate_id, name, fail, fail, fail, "FAIL",
        [], None, {}, notes=reason,
    )


def run_verification(
    candidates: Optional[List[str]] = None,
    persist_supabase: bool = True,
    local_only: bool = False,
) -> Dict[str, Any]:
    candidates = candidates or list(CANDIDATES.keys())
    client = None if local_only else build_ohlcv_client()
    try:
        symbols = trading_universe_symbols()
    except Exception as e:  # noqa: BLE001
        symbols = list_local_symbols("1d")
        logger.warning("[Verify] universe API 실패 → local symbols: %s", e)
    data = load_universe_ohlcv(symbols, interval="1d", client=client, local_only=local_only)
    if not data:
        data = load_universe_ohlcv(list_local_symbols("1d"), interval="1d", client=None, local_only=True)
    onboard_map: Dict[str, pd.Timestamp] = {}
    if "listing_effect" in candidates:
        try:
            from backtesting.collect_oi import _build_live_client
            onboard_map = fetch_onboard_dates(_build_live_client())
        except Exception as e:  # noqa: BLE001
            logger.warning("[Verify] onboard dates 실패: %s", e)
    reports: List[CandidateReport] = []
    for cid in candidates:
        if cid == "1d_breakout":
            reports.append(eval_1d_breakout(data))
        elif cid == "cross_sectional_mom":
            reports.append(eval_cross_sectional_mom(data))
        elif cid == "btc_beta":
            reports.append(eval_data_missing(cid, "1h OHLCV+BTC beta 미backfill — 1d 캐시만 존재"))
        elif cid == "funding_settlement":
            reports.append(eval_data_missing(cid, "1h funding/OI 미backfill — 후보 4 데이터 부족"))
        elif cid == "listing_effect":
            reports.append(eval_listing_effect(data, onboard_map))
        else:
            reports.append(eval_data_missing(cid, f"unknown candidate {cid}"))
    run_id = datetime.now(timezone.utc).strftime("verify_%Y%m%dT%H%M%SZ")
    payload = {
        "run_id": run_id,
        "symbols": len(data),
        "symbol_list": sorted(data.keys()),
        "reports": [asdict(r) for r in reports],
    }
    if persist_supabase and client is not None:
        try:
            client.table("backtest_runs").insert({
                "run_id": run_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "config_json": {"strategy": "verification_4_engines"},
                "summary_json": payload,
                "verdict": "FAIL" if all(r.verdict == "FAIL" for r in reports) else "REVIEW",
            }).execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Verify] backtest_runs 적재 실패: %s", e)
    return payload
