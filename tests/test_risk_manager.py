"""
tests/test_risk_manager.py
=====================================================================
RiskManager 단위 테스트 — 14개 시나리오.

근거:
  - docs/SPEC_v3.1.md §9 (기존 10개 시나리오)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-4-3 (신규 4개 시나리오)

구성:
  - DB는 tmp_path 임시 SQLite (db.init_db.init_db로 schema.sql + v3.1.1 적용)
  - capital_manager는 MagicMock + AsyncMock 주입
  - 시나리오 4~6: trades 테이블에 가상 손실 거래 N건 삽입
  - pytest asyncio_mode=auto 이므로 async def test_* 직접 사용

14개 시나리오 expected:
   1. 모든 항목 통과                          → check_all() == True
   2. 일일 손실 -1.5% 도달                    → _check_daily_loss blocked
   3. 전체 MDD -15% 도달                      → _check_total_drawdown blocked
   4. 3연패 (최근 손실)                       → _check_cooldown blocked (4시간)
   5. 5연패                                   → _check_cooldown blocked (24시간)
   6. 7연패                                   → _check_consecutive_losses blocked (terminal)
   7. 동시 포지션 한도                        → 1k=1, 5k=2, 20k=3
   8. available 99 < $100                     → _check_min_balance blocked
   9. get_max_leverage(1, "TREND_UP")         → 5
  10. get_max_leverage(2, "HIGH_VOL")         → 0
  11. wallet 950 / daily_start 1000 (-5%)     → check_all() == False
  12. wallet 840 / initial 1000 (-16%)        → check_all() == False
  13. available 80                            → _check_min_balance blocked
  14. get_initial_capital() == None           → check_all() == False (안전 우선)
=====================================================================
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.capital_manager import CapitalSnapshot
from db.init_db import init_db
from trading.risk_manager import CheckResult, RiskManager


# ── fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> str:
    """schema.sql + v3.1.1 마이그레이션이 적용된 임시 DB 경로."""
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


def _make_cm(
    *,
    wallet: float = 1000.0,
    available: float = 970.0,
    initial: float | None = 1000.0,
    daily_start: float | None = 1000.0,
    margin: float = 1000.0,
    locked: float = 30.0,
    unrealized: float = 0.0,
) -> MagicMock:
    """CapitalManager mock — get_snapshot은 AsyncMock, 나머지는 MagicMock."""
    cm = MagicMock()
    cm.get_snapshot = AsyncMock(
        return_value=CapitalSnapshot(
            wallet_balance=wallet,
            margin_balance=margin,
            available_balance=available,
            locked_margin=locked,
            unrealized_pnl=unrealized,
            timestamp=0.0,
        )
    )
    cm.get_initial_capital = MagicMock(return_value=initial)
    cm.get_daily_start_capital = MagicMock(return_value=daily_start)
    return cm


def _insert_closed_loss_trades(db_path: str, n: int, pnl_each: float = -5.0) -> None:
    """닫힌 손실 거래 N건 삽입 (timestamp = 현재 UTC, 최신 = 마지막 삽입)."""
    conn = sqlite3.connect(db_path)
    try:
        ts = datetime.now(timezone.utc).isoformat()
        for _ in range(n):
            conn.execute(
                "INSERT INTO trades "
                "(timestamp, symbol, action, entry_price, exit_price, "
                " quantity, pnl_usd, pnl_usd_net) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, "SOLUSDT", "LONG", 100.0, 99.0, 1.0, pnl_each, pnl_each),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_closed_profit_trade(db_path: str, pnl: float) -> None:
    """당월 닫힌 이익 거래 1건 삽입 (월초 잔고 역산 테스트용)."""
    conn = sqlite3.connect(db_path)
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO trades "
            "(timestamp, symbol, action, entry_price, exit_price, "
            " quantity, pnl_usd, pnl_usd_net) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, "SOLUSDT", "LONG", 100.0, 102.0, 1.0, pnl, pnl),
        )
        conn.commit()
    finally:
        conn.close()


# ── 시나리오 1: 모든 항목 통과 ──────────────────────────────────────

async def test_scenario_1_check_all_passes(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    assert await rm.check_all() is True


# ── 시나리오 2: 일일 손실 -1.5% 도달 → 차단 ─────────────────────────

async def test_scenario_2_daily_loss_blocks(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    # current 985, daily_start 1000 → -1.5% (한도값)
    result = rm._check_daily_loss(current=985.0, daily_start=1000.0)
    assert isinstance(result, CheckResult)
    assert result.passed is False
    assert "일일 손실" in result.reason


# ── 시나리오 3: 전체 MDD -15% 도달 → 차단 ───────────────────────────

async def test_scenario_3_total_drawdown_blocks(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    # current 850, initial 1000 → -15% (한도값)
    result = rm._check_total_drawdown(current=850.0, initial=1000.0)
    assert result.passed is False
    assert "전체 MDD" in result.reason


# ── 시나리오 4: 3연패 → 쿨다운 4시간 ────────────────────────────────

async def test_scenario_4_three_losses_cooldown_4h(db_path):
    _insert_closed_loss_trades(db_path, 3)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())

    cooldown = rm._check_cooldown()
    assert cooldown.passed is False
    assert "3연패" in cooldown.reason
    assert "4시간" in cooldown.reason

    # 3연패는 terminal 아님 → _check_consecutive_losses는 통과(위임)
    assert rm._check_consecutive_losses().passed is True
    # 쿨다운이 막으므로 check_all 차단
    assert await rm.check_all() is False


# ── 시나리오 5: 5연패 → 쿨다운 24시간 ───────────────────────────────

async def test_scenario_5_five_losses_cooldown_24h(db_path):
    _insert_closed_loss_trades(db_path, 5)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())

    cooldown = rm._check_cooldown()
    assert cooldown.passed is False
    assert "5연패" in cooldown.reason
    assert "24시간" in cooldown.reason

    assert await rm.check_all() is False


# ── 시나리오 6: 7연패 → terminal 7일 ────────────────────────────────

async def test_scenario_6_seven_losses_terminal(db_path):
    _insert_closed_loss_trades(db_path, 7)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())

    # terminal 차단은 _check_consecutive_losses가 담당
    consec = rm._check_consecutive_losses()
    assert consec.passed is False
    assert "terminal" in consec.reason
    assert "7연패" in consec.reason

    # 쿨다운도 7일(168시간)로 함께 차단
    cooldown = rm._check_cooldown()
    assert cooldown.passed is False
    assert "168시간" in cooldown.reason

    assert await rm.check_all() is False


# ── 시나리오 7: 동시 포지션 한도 (자본별) ───────────────────────────

async def test_scenario_7_max_concurrent_positions(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    assert rm.get_max_concurrent_positions(1000) == 1
    assert rm.get_max_concurrent_positions(5000) == 2
    assert rm.get_max_concurrent_positions(20000) == 3
    # 경계 직전 값 확인
    assert rm.get_max_concurrent_positions(4999) == 1
    assert rm.get_max_concurrent_positions(19999) == 2


def test_scenario_7b_probe_concurrent_relaxation(db_path, monkeypatch):
    """★Probe 모드: get_max_concurrent = PROBE_CONFIG.max_concurrent(2) — $200서 1→2
    게이트 완화(운영자 명시 승인). probe OFF 면 기존 자본별 한도(완화 0)."""
    import trading.risk_manager as rmmod
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    # OFF(기본) — 기존 자본별
    monkeypatch.setattr(rmmod.PROBE_CONFIG, "enabled", False)
    assert rm.get_max_concurrent_positions(200) == 1
    # ON — probe cap 2 ($200서 1→2 완화)
    monkeypatch.setattr(rmmod.PROBE_CONFIG, "enabled", True)
    monkeypatch.setattr(rmmod.PROBE_CONFIG, "max_concurrent", 2)
    assert rm.get_max_concurrent_positions(200) == 2
    assert rm.get_max_concurrent_positions(20000) == 2     # probe 모드는 자본 무관 cap


# ── 시나리오 8: available < $100 → 차단 ─────────────────────────────

async def test_scenario_8_min_balance_blocks(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_min_balance(available=99.0)
    assert result.passed is False
    assert "available_balance" in result.reason


# ── 시나리오 9: get_max_leverage(1, TREND_UP) → 5 ───────────────────

async def test_scenario_9_max_leverage_tier1_trend_up(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    assert rm.get_max_leverage(tier=1, regime="TREND_UP") == 5


# ── 시나리오 10: get_max_leverage(2, HIGH_VOL) → 0 ──────────────────

async def test_scenario_10_max_leverage_tier2_high_vol(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    assert rm.get_max_leverage(tier=2, regime="HIGH_VOL") == 0


# ── 시나리오 11: wallet 950 / daily_start 1000 (-5%) → check_all 차단 ──

async def test_scenario_11_daily_loss_5pct_check_all_blocks(db_path):
    cm = _make_cm(wallet=950.0, available=970.0, daily_start=1000.0, initial=1000.0)
    rm = RiskManager(db_path=db_path, capital_manager=cm)
    # 직접 체크
    assert rm._check_daily_loss(current=950.0, daily_start=1000.0).passed is False
    # 통합
    assert await rm.check_all() is False


# ── 시나리오 12: wallet 840 / initial 1000 (-16%) → check_all 차단 ──

async def test_scenario_12_total_drawdown_16pct_check_all_blocks(db_path):
    cm = _make_cm(wallet=840.0, available=970.0, daily_start=840.0, initial=1000.0)
    rm = RiskManager(db_path=db_path, capital_manager=cm)
    # 직접 체크 (daily_start를 840으로 두어 일일 손실 체크와 분리)
    assert rm._check_total_drawdown(current=840.0, initial=1000.0).passed is False
    # 통합
    assert await rm.check_all() is False


# ── 시나리오 13: available 80 → _check_min_balance 차단 ─────────────

async def test_scenario_13_available_80_min_balance_blocks(db_path):
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_min_balance(available=80.0)
    assert result.passed is False
    assert "80.00" in result.reason

    # check_all 통합에서도 차단되는지 확인
    cm = _make_cm(available=80.0)
    rm2 = RiskManager(db_path=db_path, capital_manager=cm)
    assert await rm2.check_all() is False


# ── 시나리오 14: get_initial_capital() == None → check_all 차단 ─────

async def test_scenario_14_initial_capital_none_blocks(db_path):
    cm = _make_cm(initial=None)
    rm = RiskManager(db_path=db_path, capital_manager=cm)
    # 안전 우선: 자본 정보 미준비 시 즉시 차단
    assert await rm.check_all() is False


# ── 감사 M2: 비정상(≤0) 베이스라인 fail-closed ─────────────────────

async def test_zero_daily_start_fails_closed(db_path):
    """daily_start <= 0 → 통과(스킵)가 아니라 차단."""
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_loss(current=1000.0, daily_start=0.0)
    assert result.passed is False
    assert "비정상" in result.reason


async def test_zero_initial_fails_closed(db_path):
    """initial <= 0 → 차단."""
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_total_drawdown(current=1000.0, initial=0.0)
    assert result.passed is False
    assert "비정상" in result.reason


async def test_negative_month_start_fails_closed(db_path):
    """월초 잔고 역산값 <= 0 → 차단 (당월 손익이 현재 잔고를 초과하는 비정상)."""
    # current 100, 당월 실현손익 +200 → month_start = 100 - 200 = -100 (<=0)
    _insert_closed_profit_trade(db_path, pnl=200.0)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_monthly_drawdown(current=100.0)
    assert result.passed is False
    assert "비정상" in result.reason


# ── 감사 [4]: 실제 PnL 기반 연패 계산 ───────────────────────────────

async def test_loss_streak_uses_real_pnl_net(db_path):
    """pnl_usd_net 음수 거래로 연패가 집계되어 check_all 이 차단된다."""
    # 음수 net PnL 3건 → 3연패 → 쿨다운 차단
    _insert_closed_loss_trades(db_path, 3, pnl_each=-7.5)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    streak, last_loss_at = rm._get_loss_streak()
    assert streak == 3
    assert last_loss_at is not None
    assert await rm.check_all() is False


# =====================================================================
# A2 [2.3] — _check_daily_trade_count (레짐별 한도, regime=None → 전역 5)
# =====================================================================


def _insert_trades_today(db_path: str, n: int) -> None:
    """오늘(현재 UTC) timestamp 로 진입 행 N건 삽입 (일일 거래수 카운트용)."""
    conn = sqlite3.connect(db_path)
    try:
        ts = datetime.now(timezone.utc).isoformat()
        for _ in range(n):
            conn.execute(
                "INSERT INTO trades (timestamp, symbol, action, entry_price, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, "SOLUSDT", "LONG", 100.0, 1.0),
            )
        conn.commit()
    finally:
        conn.close()


async def test_a2_global_limit_blocks_at_5(db_path):
    """regime=None → 전역 한도 5. 오늘 5건 도달 시 차단."""
    _insert_trades_today(db_path, 5)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime=None)
    assert result.passed is False
    assert "일일 거래수 5/5" in result.reason


async def test_a2_global_limit_passes_under_5(db_path):
    """regime=None → 전역 5. 오늘 4건이면 통과(다음 1건 허용)."""
    _insert_trades_today(db_path, 4)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime=None)
    assert result.passed is True
    assert "4/5" in result.reason


async def test_a2_regime_specific_ranging_blocks_at_1(db_path):
    """regime=RANGING → 한도 1. 오늘 1건이면 다음 진입 차단(더 엄격)."""
    _insert_trades_today(db_path, 1)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime="RANGING")
    assert result.passed is False
    assert "1/1" in result.reason


async def test_a2_regime_specific_trend_up_allows_under_5(db_path):
    """regime=TREND_UP → 한도 5. 오늘 1건이면 통과."""
    _insert_trades_today(db_path, 1)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime="TREND_UP")
    assert result.passed is True
    assert "1/5" in result.reason


async def test_a2_unknown_regime_falls_back_to_global(db_path):
    """알 수 없는 regime → 전역 한도 5 폴백."""
    _insert_trades_today(db_path, 4)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime="NOT_A_REGIME")
    assert result.passed is True
    assert "4/5" in result.reason


async def test_a2_db_failure_fails_closed(db_path):
    """DB 조회 실패 → 안전 우선 차단(passed=False)."""
    rm = RiskManager(db_path="/nonexistent/dir/nope.db", capital_manager=_make_cm())
    result = rm._check_daily_trade_count(regime=None)
    assert result.passed is False
    assert "DB 조회 실패" in result.reason


async def test_a2_check_all_includes_daily_trade_count(db_path):
    """check_all(regime=RANGING) 가 일일 거래수 한도를 반영해 차단한다."""
    _insert_trades_today(db_path, 1)   # RANGING 한도 1 도달
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    assert await rm.check_all(regime="RANGING") is False


# =====================================================================
# A4b [가드] — 안전 게이트(연패)는 보수적: 합성 청산(_stop_fallback)도 손실로 포함
# 운영자 결정 (2026-05-30): expectancy 쪽만 제외, loss-streak 은 과차단 방향 유지.
# =====================================================================


def _insert_stop_fallback_losses(db_path: str, n: int) -> None:
    """exit_reason 에 _stop_fallback 가 붙은 합성 손실 N건 삽입."""
    conn = sqlite3.connect(db_path)
    try:
        ts = datetime.now(timezone.utc).isoformat()
        for _ in range(n):
            conn.execute(
                "INSERT INTO trades "
                "(timestamp, symbol, action, entry_price, exit_price, "
                " quantity, pnl_usd, pnl_usd_net, exit_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, "SOLUSDT", "LONG", 100.0, 99.0, 1.0, -5.0, -5.0,
                 "no_position_stop_fallback"),
            )
        conn.commit()
    finally:
        conn.close()


async def test_a4b_stop_fallback_included_in_loss_streak(db_path):
    """안전 게이트(연패/쿨다운)는 보수적 — 합성 청산 손실도 손실로 *포함*(streak 3).

    운영자 결정 (2026-05-30): expectancy(엣지 통계)는 합성 제외 유지하되,
    loss-streak 은 과차단(안전) 방향으로 합성 손실을 포함한다. 빈도는 드물어도
    방향이 중요(보수적 게이트).
    """
    _insert_stop_fallback_losses(db_path, 3)
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    streak, last_loss_at = rm._get_loss_streak()
    assert streak == 3
    assert last_loss_at is not None


async def test_a4b_normal_loss_still_counts_in_streak(db_path):
    """대조군: 합성 아닌 정상 손실도 당연히 연패로 집계(기존 동작 유지)."""
    _insert_closed_loss_trades(db_path, 2, pnl_each=-5.0)   # exit_reason NULL
    rm = RiskManager(db_path=db_path, capital_manager=_make_cm())
    streak, _ = rm._get_loss_streak()
    assert streak == 2
