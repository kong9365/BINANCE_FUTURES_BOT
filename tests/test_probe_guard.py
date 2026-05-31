"""
tests/test_probe_guard.py
=====================================================================
ProbeGuard 다조건 HALT 평가 단위테스트 (순수).

각 조건 임계 발동·경계(직전 미발동) / fail-closed(손실·자본·연속 None→halt+unevaluable) /
fail-open(카운트·시간 None→skip) / 첫 적중 우선 / auto_stop 플래그 / PROBE_CONFIG 기본·env.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from governance.probe_guard import evaluate_probe_halt

CFG = SimpleNamespace(
    total_loss_pct=0.15, daily_loss_pct=0.05, max_consec_losses=5,
    n_fill=30, n_unfill=30, max_weeks=4,
)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _ev(**kw):
    base = dict(
        current_wallet=200.0, initial_capital=200.0, daily_start_capital=200.0,
        loss_streak=0, filled_count=0, unfilled_count=0,
        probe_start_at=NOW, now=NOW, cfg=CFG,
    )
    base.update(kw)
    return evaluate_probe_halt(**base)


# ── 정상(무발동) ──
def test_baseline_no_halt():
    assert _ev().halt is False


# ── 1. 총손실 ──
def test_total_loss_halt_at_threshold():
    # daily_start=current 로 일일조건 격리(총손실만 평가)
    r = _ev(current_wallet=170.0, daily_start_capital=170.0)   # 총 -15%
    assert r.halt and r.source == "probe_total_loss" and not r.auto_stop and not r.unevaluable
    assert _ev(current_wallet=170.2, daily_start_capital=170.2).halt is False   # 총 -14.9% → 미발동


# ── 2. 일일손실 ──
def test_daily_loss_halt_at_threshold():
    r = _ev(current_wallet=190.0)            # 총 -5%(>-15% 무발동) → 일일 -5% 발동
    assert r.halt and r.source == "probe_daily_loss"
    assert _ev(current_wallet=190.2).halt is False     # -4.9%


# ── 3. 연속손실 ──
def test_consec_losses_halt():
    assert _ev(loss_streak=5).source == "probe_consec_losses"
    assert _ev(loss_streak=4).halt is False


# ── 4. 데이터 목표 자동정지 ──
def test_data_target_autostop_needs_both():
    r = _ev(filled_count=30, unfilled_count=30)
    assert r.halt and r.auto_stop and r.source == "probe_data_target"
    assert _ev(filled_count=30, unfilled_count=29).halt is False   # 둘 다 필요
    assert _ev(filled_count=29, unfilled_count=30).halt is False


# ── 5. 기간 자동정지 ──
def test_max_weeks_autostop():
    r = _ev(probe_start_at=NOW - timedelta(weeks=4))
    assert r.halt and r.auto_stop and r.source == "probe_max_weeks"
    assert _ev(probe_start_at=NOW - timedelta(weeks=3, days=6)).halt is False


# ── fail-closed (손실/자본/연속 None) ──
def test_fail_closed_risk_inputs_none():
    assert _ev(current_wallet=None).source == "probe_total_loss_unevaluable"
    assert _ev(current_wallet=None).unevaluable is True
    assert _ev(initial_capital=None).source == "probe_total_loss_unevaluable"
    assert _ev(daily_start_capital=None).source == "probe_daily_loss_unevaluable"
    assert _ev(loss_streak=None).source == "probe_consec_losses_unevaluable"
    assert _ev(loss_streak=None).unevaluable is True


# ── fail-open (카운트/시간 None) ──
def test_fail_open_counts_time_none():
    assert _ev(filled_count=None, unfilled_count=None).halt is False
    assert _ev(probe_start_at=None).halt is False


# ── 첫 적중 우선 (총손실 > 일일) ──
def test_ordering_total_before_daily():
    r = _ev(current_wallet=100.0)            # -50% (총·일일 둘 다 위반)
    assert r.source == "probe_total_loss"    # 총손실이 먼저


# ── PROBE_CONFIG 기본/env ──
def test_probe_config_defaults_and_env(monkeypatch):
    import config.settings as cfgmod
    for k in ("PROBE_ENABLED", "PROBE_BUDGET_USDT", "PROBE_MAX_CONCURRENT", "PROBE_MAX_WEEKS"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(cfgmod)
    assert cfgmod.PROBE_CONFIG.enabled is False          # 기본 OFF
    assert cfgmod.PROBE_CONFIG.budget_usdt == 200.0
    assert cfgmod.PROBE_CONFIG.max_concurrent == 2
    assert cfgmod.PROBE_CONFIG.max_weeks == 4
    # env override + 0 허용(테스트 강제정지)
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_BUDGET_USDT", "100")
    monkeypatch.setenv("PROBE_MAX_WEEKS", "0")
    importlib.reload(cfgmod)
    assert cfgmod.PROBE_CONFIG.enabled is True
    assert cfgmod.PROBE_CONFIG.budget_usdt == 100.0
    assert cfgmod.PROBE_CONFIG.max_weeks == 0
    # 정리 — 다른 테스트 오염 방지
    for k in ("PROBE_ENABLED", "PROBE_BUDGET_USDT", "PROBE_MAX_WEEKS"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(cfgmod)
