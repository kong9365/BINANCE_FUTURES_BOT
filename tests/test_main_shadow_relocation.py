"""
tests/test_main_shadow_relocation.py
=====================================================================
M15 — 5-Agent shadow 가 _execute_decision(공유 게이트)로 이동했는지 검증.

검증:
  1. decision(SignalDecision) 전달 → run_shadow_for_decision 1회 (run_shadow X)
  2. candidate 만 전달(decision=None) → run_shadow 1회, setup_id=setup_tag
  3. candidate/decision 모두 None → 합성 dict({symbol,action}) 로 run_shadow
  4. shadow 예외 → 메인 게이트 차단 안 함 (fail-safe)
  5. _handle_signal 은 더 이상 shadow 를 직접 호출하지 않음 (중복 제거)
모든 외부 의존성 mock, dry_run + tmp DB.
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from audit.signal_decision import SignalDecision
from data.capital_manager import CapitalSnapshot
from db.init_db import init_db
from strategy.regime_detector import Regime
from main_7590 import MainBot


def _snapshot(*, wallet=1000.0, available=300.0, locked=0.0):
    return CapitalSnapshot(
        wallet_balance=wallet, margin_balance=wallet, available_balance=available,
        locked_margin=locked, unrealized_pnl=0.0, timestamp=0.0,
    )


def _regime(regime=Regime.TREND_UP):
    return SimpleNamespace(
        regime=regime, confidence=0.8, regime_changed=False, prev_regime=None,
        reasons=["t"], adx_4h=30.0, atr_ratio_1h=1.0, atr_ratio_4h=1.0,
        bb_width_pct=3.0, ema_slope=0.1, funding_rate=0.0001,
    )


def _decision(setup_id="1d_tsmom_donchian_long_v1", symbol="ETHUSDT"):
    now = datetime.now(timezone.utc)
    return SignalDecision(
        signal_id="sig-reloc", setup_id=setup_id, params_hash="ffbacd92",
        signal_source="strategy_skill", reasoning="test", ts_signal_generated=now,
        raw_data_hash="raw", features_snapshot_json='{"atr": 1.0, "adx": 30.0}',
        symbol=symbol, action="LONG", confidence=0.7, expires_at=now + timedelta(hours=24),
    )


@pytest.fixture
def db_path(tmp_path) -> str:
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _make_bot(db_path):
    collector = MagicMock()
    collector.client = MagicMock()
    collector.client.get_account.side_effect = Exception("permission denied")
    collector.get_candles = AsyncMock(return_value=[])
    telegram = MagicMock()
    telegram.send = AsyncMock()
    bot = MainBot(
        dry_run=True, db_path=db_path, collector=collector,
        openai_client=None, telegram=telegram, active_strategy="daily_tsmom",
    )
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    # shadow_runner mock (enabled) — env 무관하게 강제
    sr = MagicMock()
    sr.enabled = True
    sr.run_shadow = AsyncMock()
    sr.run_shadow_for_decision = AsyncMock()
    bot.shadow_runner = sr
    return bot


async def test_decision_path_uses_run_shadow_for_decision(db_path):
    """decision 전달 → run_shadow_for_decision 1회, run_shadow 호출 안 함."""
    bot = _make_bot(db_path)
    d = _decision()
    await bot._execute_decision(
        symbol="ETHUSDT", action="LONG", entry_price=100.0, tp=110.0, sl=95.0,
        setup_tag="1d_tsmom_donchian_long_v1", score=30,
        regime_state=_regime(), capital_snapshot=_snapshot(),
        candidate=None, decision=d,
    )
    bot.shadow_runner.run_shadow_for_decision.assert_awaited_once()
    assert bot.shadow_runner.run_shadow_for_decision.call_args.args[0] is d
    bot.shadow_runner.run_shadow.assert_not_awaited()


async def test_candidate_path_uses_run_shadow_with_setup_tag(db_path):
    """decision=None + candidate → run_shadow(setup_id=setup_tag)."""
    bot = _make_bot(db_path)
    cand = {"symbol": "SOLUSDT", "action": "LONG", "confidence": 0.7}
    await bot._execute_decision(
        symbol="SOLUSDT", action="LONG", entry_price=100.0, tp=110.0, sl=95.0,
        setup_tag="oi_surge_long", score=5,
        regime_state=_regime(), capital_snapshot=_snapshot(),
        candidate=cand, decision=None,
    )
    bot.shadow_runner.run_shadow.assert_awaited_once()
    # setup_id=setup_tag 로 라벨 (하드코딩 1d_tsmom 아님)
    assert bot.shadow_runner.run_shadow.call_args.kwargs.get("setup_id") == "oi_surge_long"
    bot.shadow_runner.run_shadow_for_decision.assert_not_awaited()


async def test_no_candidate_no_decision_synthesizes_dict(db_path):
    """candidate/decision 모두 None (breakout) → 합성 dict 로 run_shadow."""
    bot = _make_bot(db_path)
    await bot._execute_decision(
        symbol="ADAUSDT", action="LONG", entry_price=100.0, tp=110.0, sl=95.0,
        setup_tag="breakout_long", score=27,
        regime_state=_regime(), capital_snapshot=_snapshot(),
        candidate=None, decision=None,
    )
    bot.shadow_runner.run_shadow.assert_awaited_once()
    sent_candidate = bot.shadow_runner.run_shadow.call_args.args[0]
    assert sent_candidate == {"symbol": "ADAUSDT", "action": "LONG"}
    assert bot.shadow_runner.run_shadow.call_args.kwargs.get("setup_id") == "breakout_long"


async def test_shadow_exception_does_not_block_gate(db_path):
    """shadow 예외 → 메인 게이트 진행 (fail-safe, 예외 전파 X)."""
    bot = _make_bot(db_path)
    bot.shadow_runner.run_shadow_for_decision = AsyncMock(side_effect=Exception("boom"))
    # 예외 없이 완료되어야 함
    await bot._execute_decision(
        symbol="ETHUSDT", action="LONG", entry_price=100.0, tp=110.0, sl=95.0,
        setup_tag="1d_tsmom_donchian_long_v1", score=30,
        regime_state=_regime(), capital_snapshot=_snapshot(),
        candidate=None, decision=_decision(),
    )
    # 게이트가 정상 진행되어 거래가 기록됨 (shadow 실패 무관)
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


async def test_handle_signal_no_longer_calls_shadow_directly(db_path):
    """_handle_signal 은 shadow 를 직접 호출하지 않음 (게이트로 이동)."""
    bot = _make_bot(db_path)
    # _execute_decision 를 mock 하여 _handle_signal 자체의 shadow 호출만 관찰
    bot._execute_decision = AsyncMock()

    cand = SimpleNamespace(symbol="SOLUSDT", action="LONG", confidence=0.7)
    # 게이트 통과 위해 필요한 mock
    bot.pair_wl.is_allowed = MagicMock(return_value=True)
    bot.oi_filter.evaluate = MagicMock(
        return_value=SimpleNamespace(grade="A", reasons=[])
    )
    bot.quality_gate.check = MagicMock(return_value=SimpleNamespace(
        passed=True, action="LONG", entry_price=100.0, tp=110.0, sl=95.0,
        setup_tag="oi_surge_long", score=80,
    ))
    await bot._handle_signal(cand, _regime(), _snapshot())

    # _handle_signal 은 shadow 를 직접 호출하지 않음 (run_shadow/for_decision 모두 X)
    bot.shadow_runner.run_shadow.assert_not_awaited()
    bot.shadow_runner.run_shadow_for_decision.assert_not_awaited()
    # 대신 _execute_decision 로 위임 (거기서 shadow 발동)
    bot._execute_decision.assert_awaited_once()
