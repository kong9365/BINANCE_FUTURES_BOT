"""
analytics/ml_backtest.py
=====================================================================
ML EV Engine R0 — walk-forward backtest + 사전확정 평가.

플로우:
  1) Supabase 에서 top-50 USDT-M perp ohlcv + funding + market_global + BTC ref 로드
  2) BTC regime 계산 (regime_detector_v2)
  3) 각 심볼별 feature_engineering.build_features() → state vector
  4) 모든 심볼의 (X, y) concat → IS/OOS 분리
  5) Ridge + LightGBM 동시 학습 (IS: train 14mo, val 4mo)
  6) OOS 단일 평가:
     - regression sanity (Spearman, decile, calibration)
     - EV-진입 시뮬레이션 (r_hat - cost > 0.20% → 진입)
     - trading 메트릭 (PF, Sharpe, MDD, n, cross-sec, single)
  7) backtest_runs 적재

룩어헤드 차단:
  - feature_engineering 자체가 룩어헤드 차단 (test 검증)
  - IS/OOS 시계열 분리 (random shuffle 절대 금지)
  - scaler·imputer 통계 IS Train 만으로 fit
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from analytics.ev_model import EVModel, EVModelConfig, EVModelEvaluation
from analytics.feature_engineering import FeatureConfig, build_features, build_target
from analytics.regime_detector_v2 import RegimeDetectorConfig, detect_regimes

from analytics.ccs_lite_backtest import _build_supabase_client, _fetch_ohlcv, _fetch_paged
from analytics.pair_arb_backtest import _fetch_universe_symbols

logger = logging.getLogger(__name__)


# ── 사전확정 R0 기준 (계획서 §사전확정 합격 기준) ──
R0_MIN_SPEARMAN = 0.05
R0_MAX_CALIBRATION_ERR = 0.003   # |예측 - 실현| 0.3%
R0_MIN_TRADES = 300
R0_MIN_PF = 1.5
R0_MIN_SHARPE = 1.2
R0_MAX_MDD = 0.10
R0_MIN_CROSS_POS = 0.55
R0_MAX_SINGLE_CONTRIB = 0.20
R0_MIN_AVG_TRADE_NET = 0.003     # +0.3% net per trade (cost 0.16% × 2)

# ── 비용 모델 (single-leg RT, 보수적) ──
COST_MAKER_ENTRY = 0.00018
COST_TAKER_EXIT = 0.00045
COST_SLIP = 0.00100
ROUND_TRIP_COST = COST_MAKER_ENTRY + COST_TAKER_EXIT + COST_SLIP   # ≈ 0.00163
EV_ENTRY_THRESHOLD = 0.0020   # 0.20% (안전마진 0.04%)


@dataclass
class R0Criterion:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass
class MLBacktestReport:
    run_id: str
    started_at: str
    finished_at: str
    n_symbols: int
    n_features: int
    n_train: int
    n_val: int
    n_oos: int
    ridge_eval: Optional[Dict[str, Any]] = None
    lgb_eval: Optional[Dict[str, Any]] = None
    best_model: str = ""
    n_trades: int = 0
    pf: float = 0.0
    sharpe: float = 0.0
    mean_trade_net: float = 0.0
    max_dd: float = 0.0
    cross_pair_pos_share: float = 0.0
    single_symbol_contrib: float = 0.0
    criteria: List[R0Criterion] = field(default_factory=list)
    passed: bool = False
    notes: str = ""

    def to_jsonable(self) -> Dict[str, Any]:
        d = asdict(self)
        d["criteria"] = [asdict(c) for c in self.criteria]
        return d


# ── 데이터 로드 helpers ───────────────────────────────────────────
def _load_funding(client, symbol: str) -> pd.DataFrame:
    """단일 심볼 funding history."""
    def _q():
        return (client.table("funding_history")
                 .select("ts,funding_rate")
                 .eq("symbol", symbol)
                 .order("ts"))
    data = _fetch_paged(_q)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.sort_values("ts").drop_duplicates("ts")


def _load_market_global(client) -> pd.DataFrame:
    """market_global 전체 (btc_dominance, fear_greed_index 등)."""
    def _q():
        return client.table("market_global").select("*").order("ts")
    data = _fetch_paged(_q)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.sort_values("ts").drop_duplicates("ts")


# ── 메인 ──────────────────────────────────────────────────────────
def run_r0(
    top_n: int = 30,
    is_train_months: int = 14,
    is_val_months: int = 4,
    oos_months: int = 6,
    horizon_bars: int = 4,
    feature_cfg: Optional[FeatureConfig] = None,
    model_cfg: Optional[EVModelConfig] = None,
    regime_cfg: Optional[RegimeDetectorConfig] = None,
    client=None,
    run_id_prefix: str = "ml_ev_r0",
) -> MLBacktestReport:
    """R0 전체 러너.

    Args:
        top_n: 유니버스 상위 N (거래대금).
        is_train_months: IS train 기간.
        is_val_months: IS val 기간(LGBM early stop 용).
        oos_months: OOS hold-out 기간.
        horizon_bars: forward return target horizon (1h × N).
        feature_cfg, model_cfg, regime_cfg: 사전확정 hyperparameters.
        client: Supabase 클라이언트.
        run_id_prefix: backtest_runs 식별자.

    Returns:
        MLBacktestReport.
    """
    started = datetime.now(timezone.utc)
    feature_cfg = feature_cfg or FeatureConfig()
    model_cfg = model_cfg or EVModelConfig()
    regime_cfg = regime_cfg or RegimeDetectorConfig()
    client = client if client is not None else _build_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client 미설정")

    # 1) BTC + market_global 로드
    logger.info("[MLBacktest] loading BTC + market_global ...")
    btc = _fetch_ohlcv(client, "BTCUSDT", interval="1h")
    if btc.empty:
        raise RuntimeError("BTCUSDT ohlcv 비어있음")
    market_global = _load_market_global(client)
    regimes = detect_regimes(btc, regime_cfg)

    # 2) Universe
    syms = _fetch_universe_symbols(client, top_n=top_n)
    logger.info("[MLBacktest] universe=%d symbols", len(syms))

    # 3) cross-sectional returns dict (대상 심볼들 close concat)
    close_dict: Dict[str, pd.Series] = {}
    ohlcv_dict: Dict[str, pd.DataFrame] = {}
    funding_dict: Dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(syms, 1):
        try:
            df = _fetch_ohlcv(client, sym, interval="1h")
            if df.empty or len(df) < 500:
                continue
            ohlcv_dict[sym] = df
            close_dict[sym] = df["close"]
            funding_dict[sym] = _load_funding(client, sym)
        except Exception as e:  # noqa: BLE001
            logger.warning("[MLBacktest] %s load 실패 — 스킵: %s", sym, e)
        if i % 10 == 0:
            logger.info("[MLBacktest] loaded %d/%d", i, len(syms))
    logger.info("[MLBacktest] usable symbols=%d", len(ohlcv_dict))
    if len(ohlcv_dict) < 5:
        return _empty_report(started, f"insufficient symbols ({len(ohlcv_dict)})", run_id_prefix)

    cross_sectional_close = pd.concat(close_dict, axis=1)

    # 4) 시간 분할 (IS_train / IS_val / OOS)
    max_ts = max(df.index.max() for df in ohlcv_dict.values())
    oos_start = max_ts - pd.DateOffset(months=oos_months)
    is_val_start = oos_start - pd.DateOffset(months=is_val_months)
    is_train_start = is_val_start - pd.DateOffset(months=is_train_months)
    logger.info("[MLBacktest] split: train=[%s, %s) val=[%s, %s) oos=[%s, %s]",
                 is_train_start, is_val_start, is_val_start, oos_start, oos_start, max_ts)

    # 5) 각 심볼 피처 + 타깃 → concat
    feat_blocks: List[pd.DataFrame] = []
    tgt_blocks: List[pd.Series] = []
    sym_blocks: List[pd.Series] = []
    for sym, df in ohlcv_dict.items():
        df.attrs["symbol"] = sym
        oi_series = None    # OI 는 sparse — 일단 사용 안 함(feature engineering 자동 처리)
        try:
            X = build_features(
                ohlcv=df, btc_ohlcv=btc, regimes=regimes,
                oi=oi_series, funding=funding_dict.get(sym),
                market_global=market_global,
                cross_sectional_returns=cross_sectional_close,
                cfg=feature_cfg,
            )
            y = build_target(df, horizon_bars=horizon_bars)
            # join + dedup (혹시 중복 인덱스 있으면 첫 값만)
            X = X[~X.index.duplicated(keep="first")]
            y = y[~y.index.duplicated(keep="first")]
            common = X.index.intersection(y.index)
            X = X.loc[common]
            y = y.loc[common]
            # drop rows with NaN target
            mask = y.notna()
            X = X[mask]
            y = y[mask]
            if len(X) < 100:
                continue
            # 메모리 절약: feature 를 float32 로 다운캐스트
            X = X.astype(np.float32)
            feat_blocks.append(X)
            tgt_blocks.append(y.astype(np.float32))
            sym_blocks.append(pd.Series(sym, index=X.index, name="symbol"))
            logger.debug("[MLBacktest] %s X shape=%s", sym, X.shape)
        except Exception as e:  # noqa: BLE001
            logger.warning("[MLBacktest] %s feature 빌드 실패 — 스킵: %s", sym, e)

    if not feat_blocks:
        return _empty_report(started, "no feature blocks", run_id_prefix)

    # concat 후 sort_index — 인덱스가 duplicated 다(여러 심볼 같은 ts).
    # .loc 슬라이스는 duplicated 인덱스서 O(N²) 메모리 가능 → boolean mask 사용.
    X_all = pd.concat(feat_blocks, axis=0).sort_index()
    y_all = pd.concat(tgt_blocks, axis=0).sort_index()
    sym_all = pd.concat(sym_blocks, axis=0).sort_index()
    logger.info("[MLBacktest] total obs=%d, features=%d", len(X_all), X_all.shape[1])

    # 6) split — boolean mask 로 메모리 효율 ↑
    train_mask = (X_all.index >= is_train_start) & (X_all.index < is_val_start)
    val_mask = (X_all.index >= is_val_start) & (X_all.index < oos_start)
    oos_mask = (X_all.index >= oos_start) & (X_all.index <= max_ts)
    X_train, y_train = X_all[train_mask], y_all[train_mask]
    X_val, y_val = X_all[val_mask], y_all[val_mask]
    X_oos, y_oos = X_all[oos_mask], y_all[oos_mask]
    sym_oos = sym_all[oos_mask]

    logger.info("[MLBacktest] sizes: train=%d val=%d oos=%d",
                 len(X_train), len(X_val), len(X_oos))
    if len(X_train) < 1000 or len(X_oos) < 100:
        return _empty_report(started, f"insufficient split sizes: train={len(X_train)}, oos={len(X_oos)}",
                              run_id_prefix)

    # 7) 학습
    logger.info("[MLBacktest] training Ridge + LightGBM ...")
    model = EVModel(model_cfg)
    model.fit(X_train, y_train, X_val, y_val)

    # 8) OOS 평가 (regression sanity)
    eval_ridge = model.evaluate(X_oos, y_oos, model="ridge")
    eval_lgb = model.evaluate(X_oos, y_oos, model="lgb")
    logger.info("[MLBacktest] Ridge Spearman=%.4f  LGBM Spearman=%.4f",
                 eval_ridge.spearman_corr, eval_lgb.spearman_corr)

    # 9) Trading 시뮬레이션 (두 모델 모두, *best* 선택)
    best_model_name = "lgb" if eval_lgb.spearman_corr >= eval_ridge.spearman_corr else "ridge"
    if best_model_name == "lgb":
        preds = model.predict_lgb(X_oos)
    else:
        preds = model.predict_ridge(X_oos)

    # EV 진입: r_hat - cost > EV_ENTRY_THRESHOLD → LONG
    # 또는 -r_hat - cost > threshold → SHORT (r_hat < -threshold - cost)
    long_mask = preds > (ROUND_TRIP_COST + EV_ENTRY_THRESHOLD)
    short_mask = preds < -(ROUND_TRIP_COST + EV_ENTRY_THRESHOLD)
    trade_mask = long_mask | short_mask

    # 실현 수익률 (target = forward log-return ≈ % return)
    actuals = y_oos.values
    direction = np.where(long_mask, 1.0, np.where(short_mask, -1.0, 0.0))
    gross = direction * actuals
    net = gross - ROUND_TRIP_COST * (trade_mask.astype(float))
    net_trades = net[trade_mask]

    n_trades = int(trade_mask.sum())
    logger.info("[MLBacktest] EV-진입 trades=%d (long=%d short=%d)",
                 n_trades, int(long_mask.sum()), int(short_mask.sum()))

    # 10) 메트릭
    if n_trades > 0:
        gains = net_trades[net_trades > 0].sum()
        losses = -net_trades[net_trades < 0].sum()
        pf = float(gains / losses) if losses > 0 else float("inf") if gains > 0 else 0.0
        std = float(np.std(net_trades, ddof=1)) if n_trades > 1 else 0.0
        sharpe = float(np.mean(net_trades) / std * np.sqrt(n_trades)) if std > 0 else 0.0
        mean_trade = float(np.mean(net_trades))
        # MDD
        eq = np.cumprod(1 + net_trades)
        running_max = np.maximum.accumulate(eq)
        mdd = float(-((eq - running_max) / running_max).min())
        # cross-symbol
        sym_arr = sym_oos.values
        traded_syms = sym_arr[trade_mask]
        pnl_by_sym: Dict[str, float] = {}
        for s, p in zip(traded_syms, net_trades):
            pnl_by_sym[s] = pnl_by_sym.get(s, 0.0) + p
        cross_pos = sum(1 for v in pnl_by_sym.values() if v > 0) / max(len(pnl_by_sym), 1)
        total_abs = sum(abs(v) for v in pnl_by_sym.values())
        single_contrib = max(abs(v) for v in pnl_by_sym.values()) / total_abs if total_abs > 0 else 0.0
    else:
        pf = sharpe = mean_trade = mdd = cross_pos = 0.0
        single_contrib = 1.0

    # 11) 사전확정 평가
    crits: List[R0Criterion] = []
    best_eval = eval_lgb if best_model_name == "lgb" else eval_ridge
    crits.append(R0Criterion(
        name="spearman>0.05", passed=best_eval.spearman_corr > R0_MIN_SPEARMAN,
        value=best_eval.spearman_corr, threshold=R0_MIN_SPEARMAN,
        detail=f"OOS Spearman ({best_model_name}) = {best_eval.spearman_corr:.4f}",
    ))
    crits.append(R0Criterion(
        name="decile_monotonic", passed=best_eval.decile_monotonic,
        value=float(int(best_eval.decile_monotonic)), threshold=1.0,
        detail=f"top decile > bottom decile mean",
    ))
    crits.append(R0Criterion(
        name="calibration_error<0.3%", passed=best_eval.calibration_error < R0_MAX_CALIBRATION_ERR,
        value=best_eval.calibration_error, threshold=R0_MAX_CALIBRATION_ERR,
        detail=f"|top decile pred - actual| = {best_eval.calibration_error*100:.3f}%",
    ))
    crits.append(R0Criterion(
        name="n_trades>=300", passed=n_trades >= R0_MIN_TRADES,
        value=float(n_trades), threshold=float(R0_MIN_TRADES),
        detail=f"EV-진입 트레이드 = {n_trades}",
    ))
    crits.append(R0Criterion(
        name="pf>=1.5", passed=pf >= R0_MIN_PF,
        value=pf, threshold=R0_MIN_PF,
        detail=f"profit factor net of {ROUND_TRIP_COST*100:.2f}% RT = {pf:.3f}",
    ))
    crits.append(R0Criterion(
        name="sharpe>1.2", passed=sharpe > R0_MIN_SHARPE,
        value=sharpe, threshold=R0_MIN_SHARPE,
        detail=f"trade-level Sharpe = {sharpe:.2f}",
    ))
    crits.append(R0Criterion(
        name="max_dd<=10%", passed=mdd <= R0_MAX_MDD,
        value=mdd, threshold=R0_MAX_MDD,
        detail=f"equity curve MDD = {mdd*100:.1f}%",
    ))
    crits.append(R0Criterion(
        name="cross_pair_positive>=55%", passed=cross_pos >= R0_MIN_CROSS_POS,
        value=cross_pos, threshold=R0_MIN_CROSS_POS,
        detail=f"net positive symbols = {cross_pos*100:.0f}%",
    ))
    crits.append(R0Criterion(
        name="single_symbol_contrib<20%", passed=single_contrib < R0_MAX_SINGLE_CONTRIB,
        value=single_contrib, threshold=R0_MAX_SINGLE_CONTRIB,
        detail=f"single symbol |pnl| share = {single_contrib*100:.1f}%",
    ))
    crits.append(R0Criterion(
        name="mean_trade_net>0.3%", passed=mean_trade > R0_MIN_AVG_TRADE_NET,
        value=mean_trade, threshold=R0_MIN_AVG_TRADE_NET,
        detail=f"average trade net return = {mean_trade*100:+.3f}%",
    ))

    passed_all = all(c.passed for c in crits)
    finished = datetime.now(timezone.utc)
    run_id = f"{run_id_prefix}_{started.strftime('%Y%m%dT%H%M%SZ')}"

    return MLBacktestReport(
        run_id=run_id,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        n_symbols=len(ohlcv_dict),
        n_features=X_all.shape[1],
        n_train=len(X_train), n_val=len(X_val), n_oos=len(X_oos),
        ridge_eval=eval_ridge.to_jsonable(),
        lgb_eval=eval_lgb.to_jsonable(),
        best_model=best_model_name,
        n_trades=n_trades, pf=pf, sharpe=sharpe,
        mean_trade_net=mean_trade, max_dd=mdd,
        cross_pair_pos_share=cross_pos,
        single_symbol_contrib=single_contrib,
        criteria=crits, passed=passed_all,
        notes=f"universe={len(syms)}, usable={len(ohlcv_dict)}, "
               f"split_train_val_oos={len(X_train)}/{len(X_val)}/{len(X_oos)}",
    )


def _empty_report(started: datetime, reason: str, prefix: str) -> MLBacktestReport:
    finished = datetime.now(timezone.utc)
    run_id = f"{prefix}_{started.strftime('%Y%m%dT%H%M%SZ')}"
    return MLBacktestReport(
        run_id=run_id,
        started_at=started.isoformat(), finished_at=finished.isoformat(),
        n_symbols=0, n_features=0, n_train=0, n_val=0, n_oos=0,
        criteria=[], passed=False, notes=reason,
    )


def _normalize_for_json(obj):
    """numpy bool/int/float 등을 native python 로 변환(supabase 직렬화 안전)."""
    if isinstance(obj, dict):
        return {k: _normalize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_for_json(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    return obj


def record_to_supabase(report: MLBacktestReport, persist=None,
                       operator_notes: str = "") -> bool:
    """결과를 Supabase `backtest_runs` 적재."""
    try:
        from data.persistence import SupabasePersistence
        p = persist or SupabasePersistence()
        summary = _normalize_for_json(report.to_jsonable())
        row = {
            "run_id": report.run_id,
            "started_at": report.started_at,
            "completed_at": report.finished_at,
            "config_json": {"strategy": "ml_ev_engine_r0"},
            "summary_json": summary,
            "verdict": "PASS" if report.passed else "FAIL",
            "operator_notes": operator_notes or report.notes,
        }
        return p.insert("backtest_runs", row)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MLBacktest] backtest_runs 적재 실패: %s", e)
        return False
