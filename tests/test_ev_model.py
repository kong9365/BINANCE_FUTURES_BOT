"""
tests/test_ev_model.py
=====================================================================
analytics/ev_model.py — Ridge + LGBMRegressor wrapper 단위 테스트.
=====================================================================
"""

from __future__ import annotations

import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from analytics.ev_model import (
    EVModel,
    EVModelConfig,
    EVModelEvaluation,
)


def _make_synth(n: int = 500, n_features: int = 10, signal_strength: float = 0.3,
                  noise: float = 0.1, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """N obs × n_features 합성. y = w·X + noise (실제 신호 존재)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n, n_features))
    true_w = rng.normal(0, 1, n_features) * signal_strength
    y = X @ true_w + rng.normal(0, noise, n)
    cols = [f"f{i}" for i in range(n_features)]
    return pd.DataFrame(X, columns=cols), pd.Series(y, name="y")


def _make_no_signal(n: int = 500, n_features: int = 10, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """X 와 y 가 독립(신호 없음)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n, n_features))
    y = rng.normal(0, 1, n)
    cols = [f"f{i}" for i in range(n_features)]
    return pd.DataFrame(X, columns=cols), pd.Series(y, name="y")


# ── 학습 / 예측 ───────────────────────────────────────────────────
def test_fit_predict_basic():
    X, y = _make_synth(n=500, signal_strength=0.5, noise=0.1)
    model = EVModel()
    model.fit(X[:400], y[:400], X[400:], y[400:])
    p_ridge = model.predict_ridge(X[400:])
    p_lgb = model.predict_lgb(X[400:])
    assert len(p_ridge) == 100
    assert len(p_lgb) == 100
    assert np.isfinite(p_ridge).all()
    assert np.isfinite(p_lgb).all()


def test_fit_with_no_val_set_works():
    X, y = _make_synth(n=300)
    model = EVModel()
    model.fit(X, y)   # no val
    p = model.predict_lgb(X)
    assert len(p) == 300


def test_evaluate_returns_eval_object():
    X, y = _make_synth(n=600, signal_strength=0.5, noise=0.05)
    model = EVModel()
    model.fit(X[:500], y[:500], X[500:], y[500:])
    ev = model.evaluate(X[500:], y[500:], model="lgb")
    assert isinstance(ev, EVModelEvaluation)
    assert ev.name == "lgb"
    assert ev.n_obs == 100
    # 신호 강하니 Spearman > 0
    assert ev.spearman_corr > 0.1
    # decile sort 단조성(top > bottom)
    assert ev.decile_monotonic is True


def test_evaluate_no_signal_low_spearman():
    """X와 y 독립 → Spearman ≈ 0."""
    X, y = _make_no_signal(n=500)
    model = EVModel()
    model.fit(X[:400], y[:400], X[400:], y[400:])
    ev = model.evaluate(X[400:], y[400:], model="lgb")
    # 신호 없음 → |Spearman| 작음
    assert abs(ev.spearman_corr) < 0.25
    # decile_means 의 단조성도 우연한 정도


def test_ridge_baseline_can_recover_linear_signal():
    """선형 signal 강함 → Ridge 가 Spearman 잘 잡아야."""
    X, y = _make_synth(n=600, signal_strength=0.5, noise=0.05)
    model = EVModel()
    model.fit(X[:500], y[:500])
    ev = model.evaluate(X[500:], y[500:], model="ridge")
    assert ev.spearman_corr > 0.3


# ── NaN 처리 ──────────────────────────────────────────────────────
def test_fit_handles_nan_features():
    X, y = _make_synth(n=300)
    X.iloc[5:15, 0] = np.nan
    model = EVModel()
    model.fit(X[:250], y[:250])
    p = model.predict_lgb(X[250:])
    assert np.isfinite(p).all()


def test_predict_handles_nan():
    X, y = _make_synth(n=300)
    model = EVModel()
    model.fit(X[:250], y[:250])
    X_test = X[250:].copy()
    X_test.iloc[0, 0] = np.nan
    p = model.predict_ridge(X_test)
    assert np.isfinite(p).all()


# ── 저장/로드 ─────────────────────────────────────────────────────
def test_save_load_roundtrip():
    X, y = _make_synth(n=300, signal_strength=0.5)
    model = EVModel()
    model.fit(X[:250], y[:250])
    p1 = model.predict_lgb(X[250:])

    with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
        path = f.name
    try:
        model.save(path)
        loaded = EVModel.load(path)
        p2 = loaded.predict_lgb(X[250:])
        assert np.allclose(p1, p2)
    finally:
        os.unlink(path)


# ── 예외 ──────────────────────────────────────────────────────────
def test_predict_before_fit_raises():
    model = EVModel()
    X = pd.DataFrame(np.zeros((5, 3)), columns=["a", "b", "c"])
    with pytest.raises(RuntimeError):
        model.predict_lgb(X)
    with pytest.raises(RuntimeError):
        model.predict_ridge(X)


def test_evaluate_unknown_model_raises():
    X, y = _make_synth(n=100)
    model = EVModel()
    model.fit(X[:80], y[:80])
    with pytest.raises(ValueError):
        model.evaluate(X[80:], y[80:], model="unknown")
