"""
strategy/pair_selector.py
=====================================================================
Cointegrated USDT-M perp pair selector (Pair Stat-Arb R0).

근거(plan §전략 명세):
  공적분된 페어의 log-price spread 는 평균회귀한다는 학계 입증
  (MDPI 2025 *Temporal Dynamics of Market Microstructure*, Tadi 2023).
  단순 correlation 이 아닌 *cointegration* — 가격 자체는 비정상이어도
  선형 결합이 정상(stationary) 한 페어.

선정 파이프라인 (rolling 90-day 윈도):
  1) Log-price OLS regression → 잔차 residuals
  2) β filter: |β| ∈ [0.3, 3.0] (degenerate spread 회피)
  3) ADF test on residuals: p < 0.01 (cointegration 통계 입증)
  4) Hurst exponent on residuals: H < 0.4 (mean-reverting 확인)
  5) Half-life of mean-reversion < 14 days (AR(1) lambda inversion)

설계:
  - 순수 함수 (DataFrame/Series 입력, scalar/dict 출력). I/O 없음.
  - statsmodels 의존(ADF). hurst·half_life 은 numpy 만으로 자체 구현.
  - 룩어헤드 차단: 호출자가 시점 t 이전 90일 데이터만 전달.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class PairSelectorConfig:
    """사전확정 임계 (R0 동안 변경 금지)."""

    lookback_days: int = 90              # 페어 자격 판정 윈도
    p_adf_threshold: float = 0.01        # ADF p-value 상한
    hurst_max: float = 0.40              # Hurst exponent 상한(mean-reverting)
    half_life_max_days: float = 14.0     # 평균회귀 반감기 상한
    beta_min: float = 0.30               # log-price OLS 기울기 절댓값 하한
    beta_max: float = 3.00               # 동 상한
    min_overlap_bars: int = 24 * 60      # 페어 데이터 공통 봉 최소(60일 1h)


@dataclass
class PairCandidate:
    """자격 통과한 페어 1건의 통계."""

    symbol_a: str
    symbol_b: str
    beta: float
    intercept: float
    adf_pvalue: float
    hurst: float
    half_life_hours: float
    n_obs: int


# ── 지표 (순수 함수) ──────────────────────────────────────────────
def compute_spread(
    log_p_a: np.ndarray, log_p_b: np.ndarray
) -> Tuple[float, float, np.ndarray]:
    """log(p_a) = β·log(p_b) + α + ε.

    Args:
        log_p_a: log price series A (1D, finite).
        log_p_b: same length as A.

    Returns:
        (beta, intercept, residuals).

    Raises:
        ValueError: 길이 불일치/NaN/inf 포함.
    """
    if log_p_a.shape != log_p_b.shape:
        raise ValueError("shape mismatch")
    if not (np.isfinite(log_p_a).all() and np.isfinite(log_p_b).all()):
        raise ValueError("non-finite values in input")
    # OLS via numpy
    x = log_p_b
    y = log_p_a
    x_mean = x.mean()
    y_mean = y.mean()
    var = ((x - x_mean) ** 2).sum()
    if var <= 0:
        raise ValueError("zero variance in regressor")
    beta = float(((x - x_mean) * (y - y_mean)).sum() / var)
    intercept = float(y_mean - beta * x_mean)
    residuals = y - (beta * x + intercept)
    return beta, intercept, residuals


def adf_pvalue(residuals: np.ndarray) -> float:
    """Augmented Dickey-Fuller test p-value (statsmodels).

    H0: residual 시리즈에 unit root 존재(비정상). p < 0.01 → 정상 = cointegration.
    """
    from statsmodels.tsa.stattools import adfuller
    if len(residuals) < 20:
        return 1.0
    try:
        result = adfuller(residuals, autolag="AIC")
        return float(result[1])
    except Exception:  # noqa: BLE001
        return 1.0


def hurst_exponent(residuals: np.ndarray, min_lag: int = 2, max_lag: int = 60) -> float:
    """Rescaled Range (R/S) Hurst exponent.

    H ≈ 0.5: random walk.
    H < 0.5: mean-reverting (anti-persistent).
    H > 0.5: trending (persistent).

    이 구현은 단순 lag-variance scaling (Geometric average tau):
        log(τ_lag) = H · log(lag) + const
    """
    n = len(residuals)
    if n < max_lag * 2:
        return 0.5
    max_lag = min(max_lag, n // 2 - 1)
    if max_lag < min_lag + 5:
        return 0.5
    lags = np.arange(min_lag, max_lag)
    tau = []
    for lag in lags:
        diff = residuals[lag:] - residuals[:-lag]
        std = diff.std(ddof=1)
        if std <= 0 or not np.isfinite(std):
            return 0.5
        tau.append(std)
    tau_arr = np.array(tau, dtype=float)
    if not (tau_arr > 0).all():
        return 0.5
    # OLS slope of log(tau) on log(lag) → H × 2 (variance scaling). 표준 컨벤션상 /2.
    log_lags = np.log(lags.astype(float))
    log_tau = np.log(tau_arr)
    slope = np.polyfit(log_lags, log_tau, 1)[0]
    return float(slope)   # slope 자체가 H ; tau 가 std (R/S 변형). 직접 사용.


def half_life(residuals: np.ndarray) -> float:
    """AR(1) 회귀로 평균회귀 반감기(봉 수) 산출.

    residual_t = c + λ · residual_{t-1} + ε  → half_life = -log(2) / log(λ)
    λ ≥ 1 (회귀 없음) → ∞ 반환.
    """
    n = len(residuals)
    if n < 30:
        return float("inf")
    y = residuals[1:]
    x = residuals[:-1]
    x_mean = x.mean()
    y_mean = y.mean()
    var = ((x - x_mean) ** 2).sum()
    if var <= 0:
        return float("inf")
    lam = float(((x - x_mean) * (y - y_mean)).sum() / var)
    if not np.isfinite(lam) or lam <= 0 or lam >= 1:
        return float("inf")
    hl_bars = -np.log(2.0) / np.log(lam)
    return float(hl_bars)


def evaluate_pair(
    series_a: pd.Series,
    series_b: pd.Series,
    cfg: Optional[PairSelectorConfig] = None,
) -> Optional[PairCandidate]:
    """단일 페어의 자격 평가. 5 필터 모두 통과 시 PairCandidate, 아니면 None.

    Args:
        series_a, series_b: tz-aware UTC index, 양(+) price values.
            동일 index 정렬 가정(상위 호출자가 reindex).
        cfg: 임계.

    Returns:
        PairCandidate 또는 None.
    """
    cfg = cfg or PairSelectorConfig()
    # 공통 index 정렬
    aligned = pd.concat([series_a.rename("a"), series_b.rename("b")], axis=1).dropna()
    if len(aligned) < cfg.min_overlap_bars:
        return None
    a = aligned["a"].to_numpy(float)
    b = aligned["b"].to_numpy(float)
    if (a <= 0).any() or (b <= 0).any():
        return None
    log_a = np.log(a)
    log_b = np.log(b)
    try:
        beta, intercept, residuals = compute_spread(log_a, log_b)
    except ValueError:
        return None
    if not (cfg.beta_min <= abs(beta) <= cfg.beta_max):
        return None
    p = adf_pvalue(residuals)
    if p >= cfg.p_adf_threshold:
        return None
    h = hurst_exponent(residuals)
    if h >= cfg.hurst_max:
        return None
    hl_bars = half_life(residuals)
    # 1h interval 가정 → bars/24 = days
    hl_days = hl_bars / 24.0
    if not np.isfinite(hl_days) or hl_days <= 0 or hl_days > cfg.half_life_max_days:
        return None
    # symbol 이름은 호출자가 부여 (시리즈 .name 사용)
    sym_a = series_a.name or "A"
    sym_b = series_b.name or "B"
    return PairCandidate(
        symbol_a=str(sym_a),
        symbol_b=str(sym_b),
        beta=beta,
        intercept=intercept,
        adf_pvalue=p,
        hurst=h,
        half_life_hours=hl_bars,
        n_obs=len(aligned),
    )


def select_pairs(
    ohlcv_dict: Dict[str, pd.DataFrame],
    cfg: Optional[PairSelectorConfig] = None,
    progress_callback=None,
) -> List[PairCandidate]:
    """모든 (a, b) 후보 페어를 스캔해 자격 통과 페어 리스트 반환.

    Args:
        ohlcv_dict: {symbol: DataFrame(index=ts UTC, cols 'close')}. 호출자가 시점
            t 이전 90일 등으로 *사전 슬라이스* 해서 넘기는 것을 권장(룩어헤드 차단).
        cfg: 임계.
        progress_callback: optional callable(i, total) for logging.

    Returns:
        PairCandidate 리스트(adf_pvalue ascending 정렬).
    """
    cfg = cfg or PairSelectorConfig()
    syms = sorted(ohlcv_dict.keys())
    series_dict = {s: ohlcv_dict[s]["close"].rename(s) for s in syms}
    out: List[PairCandidate] = []
    pairs_total = len(syms) * (len(syms) - 1) // 2
    idx = 0
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            idx += 1
            cand = evaluate_pair(series_dict[syms[i]], series_dict[syms[j]], cfg)
            if cand is not None:
                out.append(cand)
            if progress_callback and idx % 100 == 0:
                progress_callback(idx, pairs_total)
    out.sort(key=lambda c: c.adf_pvalue)
    return out
