"""
tests/test_s1_kill_line.py
=====================================================================
끄는 선(kill line) — 프로브 다조건 HALT → KillSwitch.activate 시퀀스.

main_7590._iter 가 수행하는 분기 재현:
  - 실제 위반(halt & not unevaluable) → KillSwitch.activate(영속, 래칭).
  - unevaluable(일시적 조회실패) → activate 안 함(래칭 없이 당 iter 차단).
KILLSWITCH_FILE 은 tmp 로 격리(실제 data/KILLSWITCH 무영향).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from governance.kill_switch import KillSwitch
from governance.probe_guard import evaluate_probe_halt

CFG = SimpleNamespace(
    total_loss_pct=0.15, daily_loss_pct=0.05, max_consec_losses=5,
    n_fill=30, n_unfill=30, max_weeks=4,
)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _halt(**kw):
    base = dict(
        current_wallet=200.0, initial_capital=200.0, daily_start_capital=200.0,
        loss_streak=0, filled_count=0, unfilled_count=0,
        probe_start_at=NOW, now=NOW, cfg=CFG,
    )
    base.update(kw)
    return evaluate_probe_halt(**base)


def test_probe_consec_breach_activates_killswitch(monkeypatch, tmp_path):
    """연속손실 5 위반 → main 시퀀스대로 KillSwitch.activate → 활성·영속."""
    monkeypatch.setenv("KILLSWITCH_FILE", str(tmp_path / "KS"))
    assert KillSwitch.is_active() is False
    r = _halt(loss_streak=5)
    assert r.halt and not r.unevaluable
    KillSwitch.activate(reason=r.reason or r.source, source=r.source)   # main 재현
    assert KillSwitch.is_active() is True
    assert KillSwitch.get_status()["source"] == "probe_consec_losses"


def test_probe_total_loss_activates(monkeypatch, tmp_path):
    """총손실 -15% → activate."""
    monkeypatch.setenv("KILLSWITCH_FILE", str(tmp_path / "KS"))
    r = _halt(current_wallet=170.0)
    assert r.halt and r.source == "probe_total_loss"
    KillSwitch.activate(reason=r.reason, source=r.source)
    assert KillSwitch.is_active() is True


def test_probe_data_target_autostop_activates(monkeypatch, tmp_path):
    """데이터 목표(30+30) 도달 → auto_stop → activate."""
    monkeypatch.setenv("KILLSWITCH_FILE", str(tmp_path / "KS"))
    r = _halt(filled_count=30, unfilled_count=30)
    assert r.halt and r.auto_stop and r.source == "probe_data_target"
    KillSwitch.activate(reason=r.reason, source=r.source)
    assert KillSwitch.is_active() is True


def test_probe_unevaluable_does_not_latch(monkeypatch, tmp_path):
    """일시적 조회실패(자본 None) → unevaluable → main 은 activate 안 함(래칭 없음)."""
    monkeypatch.setenv("KILLSWITCH_FILE", str(tmp_path / "KS"))
    r = _halt(current_wallet=None)
    assert r.halt and r.unevaluable
    # main 분기: unevaluable 이면 activate 미호출 → 비활성 유지
    assert KillSwitch.is_active() is False


def test_probe_no_halt_no_activation(monkeypatch, tmp_path):
    monkeypatch.setenv("KILLSWITCH_FILE", str(tmp_path / "KS"))
    assert _halt().halt is False
    assert KillSwitch.is_active() is False
