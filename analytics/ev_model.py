"""
analytics/ev_model.py
=====================================================================
ML EV Engine R0 — forward-return regression wrapper (Ridge + LightGBM).

설계:
  - 동일 X, y 로 *두 모델 동시 학습* (Ridge 베이스라인 + LGBMRegressor 메인)
  - Spearman rank correlation, MAE, decile-sort 평가
  - Calibration check via decile-mean prediction vs realization
  - I/O: 모델 save/load 만 (joblib)

사전확정 (R0 평가 시 사용):
  - OOS Spearman > 0.05
  - OOS decile 단조성 (top vs bottom)
  - 둘 중 하나라도 통과 시 모델 PASS (다른 모델은 비교용)
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class EVModelConfig:
    # Ridge
    ridge_alpha: float = 1.0
    # LightGBM
    lgb_num_leaves: int = 31
    lgb_learning_rate: float = 0.03
    lgb_n_estimators: int = 500
    lgb_reg_alpha: float = 0.1
    lgb_reg_lambda: float = 0.1
    lgb_min_child_samples: int = 200
    lgb_subsample: float = 0.8
    lgb_colsample_bytree: float = 0.8
    lgb_early_stopping_rounds: int = 50
    # Scaler (Ridge 만)
    use_scaler: bool = True
    # Imputer
    fill_value: float = 0.0


@dataclass
class EVModelEvaluation:
    """OOS 평가 결과 (단일 모델)."""

    name: str
    n_obs: int
    spearman_corr: float
    mae: float
    decile_means: List[float]   # 10개, top decile 마지막
    decile_monotonic: bool      # top > bottom
    calibration_error: float    # |예측 평균 − 실현 평균| top decile

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "name": self.name, "n_obs": int(self.n_obs),
            "spearman_corr": float(self.spearman_corr), "mae": float(self.mae),
            "decile_means": [float(x) for x in self.decile_means],
            "decile_monotonic": bool(self.decile_monotonic),
            "calibration_error": float(self.calibration_error),
        }


class EVModel:
    """Ridge + LightGBM 듀얼 regressor wrapper."""

    def __init__(self, cfg: Optional[EVModelConfig] = None):
        self.cfg = cfg or EVModelConfig()
        self.feature_names: List[str] = []
        self.scaler = None
        self.ridge = None
        self.lgb = None

    # ── 학습 ────────────────────────────────────────────────────
    def fit(
        self,
        X_train: pd.DataFrame, y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> None:
        """두 모델 동시 학습.

        Args:
            X_train, y_train: NaN 행 사전 제거 권장(호출자).
            X_val, y_val: LGBM early_stopping 용. None 이면 LGBM 도 n_estimators 모두 사용.
        """
        # NaN 처리
        X_train = X_train.fillna(self.cfg.fill_value)
        y_train = y_train.fillna(0.0)
        self.feature_names = list(X_train.columns)

        # Scaler for Ridge
        if self.cfg.use_scaler:
            from sklearn.preprocessing import StandardScaler
            self.scaler = StandardScaler()
            X_train_scaled = self.scaler.fit_transform(X_train)
        else:
            X_train_scaled = X_train.values

        # Ridge
        from sklearn.linear_model import Ridge
        self.ridge = Ridge(alpha=self.cfg.ridge_alpha)
        self.ridge.fit(X_train_scaled, y_train)

        # LightGBM
        from lightgbm import LGBMRegressor, early_stopping, log_evaluation
        self.lgb = LGBMRegressor(
            objective="regression",
            num_leaves=self.cfg.lgb_num_leaves,
            learning_rate=self.cfg.lgb_learning_rate,
            n_estimators=self.cfg.lgb_n_estimators,
            reg_alpha=self.cfg.lgb_reg_alpha,
            reg_lambda=self.cfg.lgb_reg_lambda,
            min_child_samples=self.cfg.lgb_min_child_samples,
            subsample=self.cfg.lgb_subsample,
            colsample_bytree=self.cfg.lgb_colsample_bytree,
            verbose=-1,
        )
        eval_set = None
        callbacks = []
        if X_val is not None and y_val is not None:
            X_val_clean = X_val.fillna(self.cfg.fill_value)[self.feature_names]
            y_val_clean = y_val.fillna(0.0)
            eval_set = [(X_val_clean, y_val_clean)]
            callbacks = [early_stopping(self.cfg.lgb_early_stopping_rounds, verbose=False),
                          log_evaluation(0)]
        self.lgb.fit(X_train, y_train, eval_set=eval_set, callbacks=callbacks)

    # ── 예측 ────────────────────────────────────────────────────
    def predict_ridge(self, X: pd.DataFrame) -> np.ndarray:
        if self.ridge is None:
            raise RuntimeError("ridge not fitted")
        X_clean = X.fillna(self.cfg.fill_value)[self.feature_names]
        if self.scaler is not None:
            X_scaled = self.scaler.transform(X_clean)
        else:
            X_scaled = X_clean.values
        return self.ridge.predict(X_scaled)

    def predict_lgb(self, X: pd.DataFrame) -> np.ndarray:
        if self.lgb is None:
            raise RuntimeError("lgb not fitted")
        X_clean = X.fillna(self.cfg.fill_value)[self.feature_names]
        return self.lgb.predict(X_clean)

    # ── 평가 ────────────────────────────────────────────────────
    def evaluate(
        self, X: pd.DataFrame, y: pd.Series, model: str = "lgb",
    ) -> EVModelEvaluation:
        """OOS 평가."""
        if model == "lgb":
            preds = self.predict_lgb(X)
        elif model == "ridge":
            preds = self.predict_ridge(X)
        else:
            raise ValueError(f"unknown model: {model}")

        y_arr = y.fillna(0.0).values
        # NaN preds (rare) → skip
        mask = np.isfinite(preds) & np.isfinite(y_arr)
        preds = preds[mask]
        y_arr = y_arr[mask]
        n = len(preds)
        if n < 10:
            return EVModelEvaluation(model, n, 0.0, 0.0, [], False, 1.0)

        # Spearman rank corr (numpy 만으로)
        from scipy.stats import spearmanr
        try:
            sp, _ = spearmanr(preds, y_arr)
            if not np.isfinite(sp):
                sp = 0.0
        except Exception:  # noqa: BLE001
            sp = 0.0
        mae = float(np.mean(np.abs(preds - y_arr)))

        # Decile sort
        df = pd.DataFrame({"pred": preds, "y": y_arr}).sort_values("pred")
        try:
            df["decile"] = pd.qcut(df["pred"], 10, labels=False, duplicates="drop")
        except Exception:  # noqa: BLE001
            return EVModelEvaluation(model, n, float(sp), mae, [], False, 1.0)
        decile_means = df.groupby("decile")["y"].mean().to_list()
        if len(decile_means) < 2:
            return EVModelEvaluation(model, n, float(sp), mae, decile_means, False, 1.0)
        decile_monotonic = decile_means[-1] > decile_means[0]
        # Calibration: top decile 평균 예측 vs 평균 실현
        top = df[df["decile"] == df["decile"].max()]
        cal_err = abs(top["pred"].mean() - top["y"].mean())

        return EVModelEvaluation(
            name=model, n_obs=n,
            spearman_corr=float(sp), mae=mae,
            decile_means=decile_means,
            decile_monotonic=bool(decile_monotonic),
            calibration_error=float(cal_err),
        )

    # ── 저장/로드 ────────────────────────────────────────────────
    def save(self, path: str) -> None:
        import joblib
        joblib.dump({
            "cfg": self.cfg, "feature_names": self.feature_names,
            "scaler": self.scaler, "ridge": self.ridge, "lgb": self.lgb,
        }, path)

    @classmethod
    def load(cls, path: str) -> "EVModel":
        import joblib
        d = joblib.load(path)
        m = cls(d["cfg"])
        m.feature_names = d["feature_names"]
        m.scaler = d["scaler"]
        m.ridge = d["ridge"]
        m.lgb = d["lgb"]
        return m
