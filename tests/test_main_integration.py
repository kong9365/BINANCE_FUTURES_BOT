"""
tests/test_main_integration.py
=====================================================================
MainBot 통합 흐름 테스트 (mock 환경).

근거:
  - docs/SPEC_v3.1.md §8-8 (통합 흐름)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-5 (시작 시퀀스, _handle_signal)
  - docs/SESSION_PROMPTS.md 세션 12 (통합 검증 시나리오)

구성:
  - 모든 외부 의존성(collector / binance client / telegram)은 mock 주입
  - executor 는 dry_run=True + tmp DB (실제 거래소 호출 없음 — CLAUDE.md 규칙)
  - regime/health/macro 등 비결정 요소는 시나리오별로 monkeypatch

검증 시나리오 (세션 12 프롬프트):
  1. 봇 시작 → 보호 종목(BTCUSDT/ETHUSDT/HOLOUSDT/LYNUSDT) Telegram 알림
  2. 봇 시작 → capital_initial 테이블에 초기 자본 기록
  3. BTCUSDT 시그널 → pair_wl.is_allowed=False → 진입 안 됨 (보호 종목)
  4. SOLUSDT 시그널 → 모든 게이트 통과 → 진입 + exit_plan 추적 + shadow 기록
  5. available_balance < size_usdt → 마진 부족 추가 검증으로 차단
  6. C_DANGER 신호 → shadow.record_blocked, 진입 안 됨
=====================================================================
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.capital_manager import CapitalSnapshot
from data.oi_scanner import Candidate
from trading.executor import Position
from db.init_db import init_db
from sizing.dynamic_sizer import SizingResult
from strategy.regime_detector import Regime
from main_7590 import MainBot, _resolve_db_path, _suppress_http_client_logs


# ── fixtures / helpers ──────────────────────────────────────────────

def _snapshot(*, wallet=1000.0, available=970.0, locked=30.0):
    return CapitalSnapshot(
        wallet_balance=wallet,
        margin_balance=wallet,
        available_balance=available,
        locked_margin=locked,
        unrealized_pnl=0.0,
        timestamp=0.0,
    )


def _regime(regime=Regime.TREND_UP, confidence=0.8):
    """RegimeState 최소 스텁 — MainBot 이 참조하는 필드만 채운다."""
    return SimpleNamespace(
        regime=regime,
        confidence=confidence,
        regime_changed=False,
        prev_regime=None,
        reasons=["test"],
        adx_4h=30.0, atr_ratio_1h=1.0, atr_ratio_4h=1.0,
        bb_width_pct=3.0, ema_slope=0.1, funding_rate=0.0001,
    )


def _candidate(symbol="SOLUSDT", *, price=150.0, oi_change=10.0,
               price_change=5.0, volume_24h=200_000_000.0):
    return Candidate(
        symbol=symbol, price=price, oi_now=1100.0,
        oi_change_pct=oi_change, price_change_pct=price_change,
        volume_24h=volume_24h,
    )


@pytest.fixture
def db_path(tmp_path) -> str:
    """schema + v3.1.1 마이그레이션이 적용된 임시 DB.

    start() 도 init_db 를 호출하지만 멱등하므로, _handle_signal 을 직접
    호출하는 시나리오를 위해 fixture 에서 미리 초기화한다.
    """
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


@pytest.fixture
def bot(db_path, monkeypatch) -> MainBot:
    """mock 의존성으로 조립된 MainBot (dry_run).

    CMC_API_KEY 환경변수를 제거해 _build_cmc_client() 가 결정적으로 None 을
    반환하도록 한다 (테스트가 실제 CMC 네트워크 호출을 하지 않도록).
    """
    monkeypatch.delenv("CMC_API_KEY", raising=False)

    collector = MagicMock()
    collector.client = MagicMock()
    # Spot 권한 없음(정상) 시뮬레이션
    collector.client.get_account.side_effect = Exception("permission denied")

    telegram = MagicMock()
    telegram.send = AsyncMock()

    b = MainBot(
        dry_run=True,
        db_path=db_path,
        collector=collector,
        openai_client=None,
        telegram=telegram,
    )
    return b


def _fetch_all(db_path: str, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


# ── C-1. TradeExecutorConfig main 연결 ──────────────────────────────

async def test_executor_receives_settings_config(db_path, monkeypatch):
    """C-1 — MainBot 이 TRADE_EXECUTOR_CONFIG 값을 executor 에 실제로 주입한다."""
    import config.settings as settings

    monkeypatch.delenv("CMC_API_KEY", raising=False)
    # settings 값을 기본과 다르게 바꿔 주입 여부를 검증 (공유 싱글톤 mutate)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "fill_timeout_s", 7.5)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "fill_poll_interval_s", 0.25)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "exchange_info_ttl_s", 123.0)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "place_take_profit", False)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "working_type", "CONTRACT_PRICE")
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "price_protect", False)
    monkeypatch.setattr(settings.TRADE_EXECUTOR_CONFIG, "block_hedge_mode", False)

    collector = MagicMock()
    collector.client = MagicMock()
    telegram = MagicMock()
    telegram.send = AsyncMock()

    b = MainBot(dry_run=True, db_path=db_path, collector=collector,
                openai_client=None, telegram=telegram)

    ex = b.executor
    assert ex.fill_timeout_s == 7.5
    assert ex.fill_poll_interval_s == 0.25
    assert ex.exchange_info_ttl_s == 123.0
    assert ex.place_take_profit is False
    assert ex.working_type == "CONTRACT_PRICE"
    assert ex.price_protect is False
    assert ex.block_hedge_mode is False


async def test_executor_uses_config_defaults_unchanged(db_path, monkeypatch):
    """C-1 — settings 미변경 시 executor 기본 동작값과 동일(기존 동작 불변)."""
    monkeypatch.delenv("CMC_API_KEY", raising=False)
    collector = MagicMock()
    collector.client = MagicMock()
    telegram = MagicMock()
    telegram.send = AsyncMock()

    b = MainBot(dry_run=True, db_path=db_path, collector=collector,
                openai_client=None, telegram=telegram)

    ex = b.executor
    assert ex.fill_timeout_s == 15.0
    assert ex.fill_poll_interval_s == 1.0
    assert ex.exchange_info_ttl_s == 3600.0
    assert ex.place_take_profit is True
    assert ex.working_type == "MARK_PRICE"
    assert ex.price_protect is True
    assert ex.block_hedge_mode is True


# ── 보안: httpx/httpcore 로그 억제 (Telegram 토큰 URL 노출 차단) ──────

def test_httpx_log_suppressed():
    """_suppress_http_client_logs 가 httpx/httpcore 로거를 WARNING 이상으로 올린다."""
    _suppress_http_client_logs()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_telegram_token_url_not_logged_at_info(caplog):
    """httpx 의 INFO 레벨 URL 로그(토큰 포함)가 억제되어 기록되지 않는다."""
    _suppress_http_client_logs()
    with caplog.at_level(logging.INFO):
        # 실제 운영에서 httpx 가 찍던 형태 (토큰 자리는 플레이스홀더)
        logging.getLogger("httpx").info(
            "HTTP Request: POST https://api.telegram.org/bot<TOKEN>/sendMessage"
        )
    assert not any(
        "api.telegram.org/bot" in r.getMessage() for r in caplog.records
    ), "httpx INFO 로그(토큰 URL)가 억제되지 않음"


# ── Protected Existing Position Coexist Mode ────────────────────────

async def test_preflight_allows_only_protected_existing(bot):
    """기존 포지션/주문/algo 가 모두 보호종목이면 preflight 통과(공존 허용)."""
    bot.executor.preflight_open_position_symbols = AsyncMock(
        return_value=["INJUSDT", "LYNUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=["ETHUSDT"])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=["INJUSDT"])
    bot.telegram.send.reset_mock()

    await bot._live_account_preflight()   # 예외 없이 통과해야 함

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("공존" in m for m in sent)


async def test_preflight_blocks_nonprotected_existing_position(bot):
    bot.executor.preflight_open_position_symbols = AsyncMock(return_value=["SOLUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=[])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError):
        await bot._live_account_preflight()


async def test_preflight_blocks_nonprotected_existing_order(bot):
    bot.executor.preflight_open_position_symbols = AsyncMock(return_value=["INJUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=["SOLUSDT"])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError):
        await bot._live_account_preflight()


async def test_preflight_blocks_nonprotected_existing_algo(bot):
    bot.executor.preflight_open_position_symbols = AsyncMock(return_value=[])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=[])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=["SOLUSDT"])
    with pytest.raises(RuntimeError):
        await bot._live_account_preflight()


async def test_preflight_query_failure_fail_closed(bot):
    """기존 계좌 조회 실패 → fail-closed(시작 차단)."""
    bot.executor.preflight_open_position_symbols = AsyncMock(
        side_effect=RuntimeError("net down"))
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=[])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError):
        await bot._live_account_preflight()


async def test_close_all_excludes_protected(bot):
    """emergency close-all 은 보호종목 기존 포지션을 제외하고 비보호만 청산한다."""
    bot.executor.get_open_positions = AsyncMock(return_value=[
        Position("INJUSDT", 1.0, 10.0, 0.0, "LONG"),
        Position("LYNUSDT", 2.0, 5.0, 0.0, "LONG"),
        Position("SOLUSDT", 0.5, 100.0, 0.0, "LONG"),
    ])
    bot.executor.close_position = AsyncMock(return_value={"success": True})

    await bot._close_all_positions_safe()

    closed = [c.args[0] for c in bot.executor.close_position.call_args_list]
    assert "SOLUSDT" in closed
    assert "INJUSDT" not in closed
    assert "LYNUSDT" not in closed


async def test_live_probe_budget_cap(bot, monkeypatch):
    """사이징 capital 이 min(available, live_probe_budget_usdt) 로 cap 된다."""
    import config.settings as settings
    monkeypatch.setattr(settings.LIVE_PROBE_CONFIG, "live_probe_budget_usdt", 300.0)
    bot.risk_manager.check_all = AsyncMock(return_value=True)

    captured = {}
    real_calc = bot.sizer.calculate

    def spy(**kwargs):
        captured["capital"] = kwargs.get("capital")
        return real_calc(**kwargs)

    bot.sizer.calculate = MagicMock(side_effect=spy)

    await bot._handle_signal(
        _candidate("SOLUSDT"), _regime(Regime.TREND_UP, 0.8),
        _snapshot(available=1886.0),
    )
    assert captured.get("capital") == 300.0   # min(1886, 300)


async def test_existing_protected_not_recorded_in_db(bot, db_path):
    """preflight 가 기존 보호종목을 조회해도 trades 에 INSERT 하지 않는다."""
    bot.executor.preflight_open_position_symbols = AsyncMock(
        return_value=["INJUSDT", "LYNUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=["ETHUSDT"])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=["INJUSDT"])

    await bot._live_account_preflight()

    assert _fetch_all(db_path, "trades") == []


# ── 1~2. 시작 시퀀스 ────────────────────────────────────────────────

async def test_start_sends_protected_symbol_alert(bot):
    """봇 시작 → 보호 종목 Telegram 알림 (BTCUSDT/ETHUSDT/HOLOUSDT/LYNUSDT)."""
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot._main_loop = AsyncMock()   # 메인 루프는 진입하지 않고 시작 시퀀스만 검증

    await bot.start()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    protected_msg = next((m for m in sent if "보호 종목 활성" in m), None)
    assert protected_msg is not None
    # 보호 종목 6개 전부 알림에 포함 (INJUSDT 추가)
    for sym in ("BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"):
        assert sym in protected_msg


async def test_start_records_initial_capital(bot, db_path):
    """봇 시작 → capital_initial 테이블에 초기 자본 신규 기록 (부록 E-7-2)."""
    bot.capital_manager.get_snapshot = AsyncMock(
        return_value=_snapshot(wallet=1234.56)
    )
    bot._main_loop = AsyncMock()

    await bot.start()

    rows = _fetch_all(db_path, "capital_initial")
    assert len(rows) == 1
    assert rows[0]["initial_wallet_balance"] == 1234.56
    assert rows[0]["is_active"] == 1


async def test_start_restores_initial_capital_on_restart(bot, db_path):
    """재시작 시 capital_initial 활성 행이 있으면 그 값으로 복원한다."""
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot._main_loop = AsyncMock()
    await bot.start()                       # 1회차 — 1000.0 기록

    # 2회차: get_snapshot 이 다른 값을 줘도 DB 활성 행(1000)이 복원돼야 함
    bot2 = bot
    bot2.capital_manager.set_initial_capital = MagicMock()
    bot2.capital_manager.get_snapshot = AsyncMock(
        return_value=_snapshot(wallet=9999.0)
    )
    await bot2.start()
    bot2.capital_manager.set_initial_capital.assert_called_once_with(1000.0)


# ── 3. BTCUSDT (보호 종목) → 진입 차단 ──────────────────────────────

async def test_protected_symbol_signal_blocked(bot):
    """BTCUSDT 후보 → pair_wl.is_allowed=False → enter_trade 호출 안 됨."""
    bot.executor.enter_trade = AsyncMock()
    # 사전 확인: BTCUSDT 는 protected_symbols 에 포함
    assert "BTCUSDT" in bot.pair_wl.protected_symbols
    assert bot.pair_wl.is_allowed("BTCUSDT", capital=1000.0,
                                  regime=Regime.TREND_UP) is False

    await bot._handle_signal(
        _candidate("BTCUSDT"), _regime(), _snapshot()
    )
    bot.executor.enter_trade.assert_not_awaited()


async def test_lyn_protected_symbol_blocked(bot):
    """LYNUSDT (신규 추가 보호 종목) 도 진입 차단된다."""
    bot.executor.enter_trade = AsyncMock()
    assert "LYNUSDT" in bot.pair_wl.protected_symbols
    await bot._handle_signal(_candidate("LYNUSDT"), _regime(), _snapshot())
    bot.executor.enter_trade.assert_not_awaited()


# ── 4. SOLUSDT → 전 게이트 통과 → 진입 ──────────────────────────────

async def test_sol_signal_passes_all_gates_and_enters(bot, db_path):
    """SOLUSDT 후보 → 모든 게이트 통과 → trades 기록 + exit_plan 추적 + shadow 기록."""
    # 리스크 게이트는 통과로 고정 (capital_manager DB 의존성 우회)
    bot.risk_manager.check_all = AsyncMock(return_value=True)

    snap = _snapshot(wallet=1000.0, available=970.0, locked=30.0)
    await bot._handle_signal(_candidate("SOLUSDT"), _regime(Regime.TREND_UP, 0.8), snap)

    # trades 행 기록 — v3.1.1 자본 상태 컬럼 포함
    trades = _fetch_all(db_path, "trades")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "SOLUSDT"
    assert trades[0]["action"] == "LONG"
    assert trades[0]["wallet_balance_at_entry"] == 1000.0
    assert trades[0]["available_at_entry"] == 970.0
    assert trades[0]["locked_margin_at_entry"] == 30.0

    # ExitPlanController 추적 시작
    assert "SOLUSDT" in bot.exit_plan.get_tracked()

    # ShadowRecorder 기록 (was_taken_real=1)
    shadow_rows = _fetch_all(db_path, "shadow_decisions")
    taken = [r for r in shadow_rows if r["was_taken_real"] == 1]
    assert len(taken) == 1
    assert taken[0]["symbol"] == "SOLUSDT"

    # 진입 알림
    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("✅ 진입" in m and "SOLUSDT" in m for m in sent)


# ── 5. 마진 부족 추가 검증 ──────────────────────────────────────────

async def test_size_exceeds_available_blocked(bot, db_path):
    """sizer 가 available 초과 사이즈를 내면 마진 부족 추가 검증으로 차단된다."""
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    bot.executor.enter_trade = AsyncMock()
    # available=970 인데 size_usdt=9999 → 차단되어야 함
    bot.sizer.calculate = MagicMock(return_value=SizingResult(
        size_pct=50.0, size_usdt=9999.0, kelly_raw=0.3,
        kelly_fraction_used=0.25, regime_cap_pct=10.0, capital_cap_pct=5.0,
        confidence_factor=0.8, final_cap_pct=5.0, reason="test oversized",
    ))

    await bot._handle_signal(
        _candidate("SOLUSDT"), _regime(Regime.TREND_UP, 0.8),
        _snapshot(available=970.0),
    )
    bot.executor.enter_trade.assert_not_awaited()
    assert _fetch_all(db_path, "trades") == []


# ── 6. C_DANGER → shadow.record_blocked ─────────────────────────────

async def test_c_danger_signal_recorded_blocked(bot, db_path):
    """역추세(C_DANGER) 신호 → 진입 안 됨 + shadow.record_blocked(oi_filter)."""
    bot.executor.enter_trade = AsyncMock()
    # 가격↑(LONG)인데 regime=TREND_DOWN → OIFilter C_DANGER
    await bot._handle_signal(
        _candidate("SOLUSDT", price_change=5.0),
        _regime(Regime.TREND_DOWN, 0.8),
        _snapshot(),
    )
    bot.executor.enter_trade.assert_not_awaited()

    shadow_rows = _fetch_all(db_path, "shadow_decisions")
    blocked = [r for r in shadow_rows if r["was_taken_real"] == 0]
    assert len(blocked) == 1
    assert blocked[0]["blocked_by"] == "oi_filter"
    assert blocked[0]["symbol"] == "SOLUSDT"


# ── 6b. 보호주문 실패 → Critical Telegram (v3.1.2) ──────────────────

async def test_protective_failure_sends_critical_alert(bot):
    """executor 가 critical(보호주문 실패→강제청산) 반환 시 Critical 알림 발송."""
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    bot.executor.enter_trade = AsyncMock(return_value={
        "success": False, "critical": True, "trade_id": None,
        "symbol": "SOLUSDT", "sl_order_id": None, "tp_order_id": None,
        "reason": "손절 주문 생성 실패 → 포지션 강제청산됨",
    })
    bot.telegram.send.reset_mock()

    await bot._handle_signal(_candidate("SOLUSDT"), _regime(Regime.TREND_UP, 0.8),
                             _snapshot())

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("🚨 CRITICAL" in m and "SOLUSDT" in m for m in sent)
    # 추적 시작 안 함
    assert "SOLUSDT" not in bot.exit_plan.get_tracked()


async def test_hedge_mode_critical_alert(bot):
    """executor 가 Hedge Mode 차단(critical) 반환 시 reason 이 Telegram 에 명확히 노출."""
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    bot.executor.enter_trade = AsyncMock(return_value={
        "success": False, "critical": True, "trade_id": None,
        "symbol": "SOLUSDT", "sl_order_id": None, "tp_order_id": None,
        "reason": "hedge_mode_blocked",
    })
    bot.telegram.send.reset_mock()

    await bot._handle_signal(_candidate("SOLUSDT"), _regime(Regime.TREND_UP, 0.8),
                             _snapshot())

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    # A-4 요구 10 — 차단 reason 이 운영자에게 명확히 보여야 함
    assert any("🚨 CRITICAL" in m and "SOLUSDT" in m and "hedge_mode_blocked" in m
               for m in sent)
    assert "SOLUSDT" not in bot.exit_plan.get_tracked()


# ── Telegram 알림 강화 (16h 운영 모니터링) ──────────────────────────

async def test_entry_and_protective_alerts_content(bot):
    """진입 알림(요구4: trade_id/entry_order_id/notional) + 보호주문 알림(요구5)."""
    bot.risk_manager.check_all = AsyncMock(return_value=True)
    bot.executor.enter_trade = AsyncMock(return_value={
        "success": True, "critical": False, "trade_id": 42,
        "symbol": "SOLUSDT", "quantity": 0.5, "entry_price_actual": 150.0,
        "entry_order_id": "bot-SOLUSDT-abc", "sl_order_id": "9001",
        "tp_order_id": "9002", "reason": "ok",
    })
    bot.telegram.send.reset_mock()

    await bot._handle_signal(_candidate("SOLUSDT"),
                             _regime(Regime.TREND_UP, 0.8), _snapshot())

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    entry_msg = next((m for m in sent if "✅ 진입" in m), None)
    assert entry_msg is not None
    assert "trade_id: 42" in entry_msg
    assert "entry_order_id: bot-SOLUSDT-abc" in entry_msg
    assert "notional:" in entry_msg
    prot_msg = next((m for m in sent if "🛡️ 보호주문 등록" in m), None)
    assert prot_msg is not None
    assert "sl_order_id: 9001" in prot_msg
    assert "tp_order_id: 9002" in prot_msg


async def test_close_alert_includes_db_fields(bot, db_path):
    """청산 알림에 exit_price/pnl_usd_net/duration_seconds/trade_status 포함(요구6)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, action, entry_price, quantity, "
        "leverage, take_profit, stop_loss, exit_price, pnl_usd, pnl_usd_net, "
        "duration_seconds, exit_reason, trade_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-21T00:00:00+00:00", "SOLUSDT", "LONG", 100.0, 0.5, 3,
         102.0, 99.0, 101.5, 0.75, 0.70, 123, "take_profit", "CLOSED"),
    )
    conn.commit(); conn.close()

    msg = bot._close_alert_text("SOLUSDT", method="take_profit")
    assert "exit_price: 101.5" in msg
    assert "pnl_usd_net: 0.7" in msg
    assert "duration_seconds: 123" in msg
    assert "trade_status: CLOSED" in msg


async def test_preflight_alert_pass_content(bot):
    """preflight PASS 알림에 One-way / 공존 항목 수 / 비보호 0 명시(요구2)."""
    bot.executor.preflight_open_position_symbols = AsyncMock(
        return_value=["INJUSDT", "LYNUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=["ETHUSDT"])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=["INJUSDT"])
    bot.telegram.send.reset_mock()

    await bot._live_account_preflight()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    msg = next((m for m in sent if "Live Preflight PASS" in m), None)
    assert msg is not None
    assert "One-way Mode" in msg
    assert "비보호종목 기존 포지션/주문/algo: 0건" in msg
    # baseline 기록되어 heartbeat 불변 비교 가능
    assert bot._protected_baseline is not None


async def test_heartbeat_detects_protected_change_critical(bot):
    """heartbeat 가 기존 보호종목 보유분 변경을 감지하면 CRITICAL 알림."""
    bot.dry_run = False
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot._started_at = datetime.now(timezone.utc)
    bot._protected_baseline = {
        "positions": ["INJUSDT", "LYNUSDT"], "orders": ["ETHUSDT"],
        "algos": ["INJUSDT"],
    }
    # INJUSDT 포지션이 사라짐 → 변경 감지
    bot.executor.preflight_open_position_symbols = AsyncMock(return_value=["LYNUSDT"])
    bot.executor.preflight_open_order_symbols = AsyncMock(return_value=["ETHUSDT"])
    bot.executor.preflight_open_algo_symbols = AsyncMock(return_value=["INJUSDT"])
    bot.telegram.send.reset_mock()

    await bot._maybe_send_heartbeat()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("🚨 CRITICAL" in m and "보호종목 보유분 변경 감지" in m for m in sent)


# ── 6c. reconcile_closed_positions 호출 (v3.1.2) ────────────────────

async def test_manage_positions_reconciles_exchange_close(bot):
    """live 에서 거래소 보호주문 체결분을 reconcile → 추적 해제 + 알림."""
    bot.dry_run = False
    # 추적 중인 포지션 1건 등록
    bot.exit_plan.start_tracking(
        trade_id=1,
        decision={"symbol": "SOLUSDT", "action": "LONG",
                  "entry_price": 100.0, "tp": 102.0, "sl": 99.0},
        quantity=0.5, sl_order_id="222", tp_order_id="333",
    )
    bot.executor.reconcile_closed_positions = AsyncMock(
        return_value=[{"symbol": "SOLUSDT", "exit_price": 99.0,
                       "reason": "exchange_stop_or_tp"}]
    )
    bot.telegram.send.reset_mock()

    await bot._manage_open_positions()

    bot.executor.reconcile_closed_positions.assert_awaited_once()
    assert "SOLUSDT" not in bot.exit_plan.get_tracked()
    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("포지션 청산" in m and "SOLUSDT" in m and "reconcile" in m
               for m in sent)


# ── 7. 하트비트 (Telegram 주기 상태 알림) ───────────────────────────

async def test_heartbeat_sends_on_first_call(bot):
    """_last_heartbeat 가 None 이면 첫 호출에서 하트비트를 발송한다."""
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot.collector.use_testnet = False
    bot._started_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    bot._loop_count = 3
    bot._last_candidate_count = 2
    bot.telegram.send.reset_mock()

    await bot._maybe_send_heartbeat()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    hb = next((m for m in sent if "💓 하트비트" in m), None)
    assert hb is not None
    assert "DRY-RUN" in hb
    assert "루프: 3" in hb
    assert "직전 스캔 2" in hb
    # 강화된 heartbeat 필드 (요구 3)
    assert "스캔 횟수:" in hb
    assert "진입 시도/성공:" in hb
    assert "ERROR/CRITICAL:" in hb
    assert "DB:" in hb
    assert "보호종목 기존 보유분:" in hb
    assert bot._last_heartbeat is not None


async def test_heartbeat_skipped_within_interval(bot):
    """마지막 발송 이후 interval 미경과면 발송하지 않는다."""
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot._started_at = datetime.now(timezone.utc)
    bot._heartbeat_interval_s = 3600
    bot._last_heartbeat = datetime.now(timezone.utc)
    bot.telegram.send.reset_mock()

    await bot._maybe_send_heartbeat()

    bot.telegram.send.assert_not_awaited()


async def test_heartbeat_resends_after_interval(bot):
    """마지막 발송 이후 interval 경과 시 다시 발송한다."""
    bot.capital_manager.get_snapshot = AsyncMock(return_value=_snapshot())
    bot.collector.use_testnet = False
    bot._started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    bot._heartbeat_interval_s = 3600
    bot._last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=3601)
    bot.telegram.send.reset_mock()

    await bot._maybe_send_heartbeat()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("💓 하트비트" in m for m in sent)


async def test_heartbeat_survives_capital_fetch_failure(bot):
    """자본 조회가 실패해도 하트비트는 발송된다 (생존 신호 우선)."""
    bot.capital_manager.get_snapshot = AsyncMock(side_effect=Exception("api down"))
    bot.collector.use_testnet = False
    bot._started_at = datetime.now(timezone.utc)
    bot.telegram.send.reset_mock()

    await bot._maybe_send_heartbeat()

    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    hb = next((m for m in sent if "💓 하트비트" in m), None)
    assert hb is not None
    assert "조회 실패" in hb


# ── 8. DB 경로 결정 (실거래/페이퍼 격리) ────────────────────────────

def test_resolve_db_path_explicit_arg_wins(monkeypatch):
    """명시적 db_path 인자가 환경변수보다 우선한다."""
    monkeypatch.setenv("DB_PATH", "data/env.db")
    monkeypatch.setenv("USE_TESTNET", "true")
    assert _resolve_db_path("data/explicit.db") == "data/explicit.db"


def test_resolve_db_path_env_override(monkeypatch):
    """인자가 없으면 DB_PATH 환경변수가 USE_TESTNET 기본값보다 우선한다."""
    monkeypatch.setenv("DB_PATH", "data/custom.db")
    monkeypatch.setenv("USE_TESTNET", "false")
    assert _resolve_db_path(None) == "data/custom.db"


def test_resolve_db_path_testnet_default(monkeypatch):
    """DB_PATH 미설정 + USE_TESTNET=true → 페이퍼 DB (data/bot.db)."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("USE_TESTNET", "true")
    assert _resolve_db_path(None) == "data/bot.db"


def test_resolve_db_path_live_default(monkeypatch):
    """DB_PATH 미설정 + USE_TESTNET=false → 실거래 DB (data/bot_live.db)."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("USE_TESTNET", "false")
    assert _resolve_db_path(None) == "data/bot_live.db"


def test_resolve_db_path_unset_testnet_defaults_to_live(monkeypatch):
    """DB_PATH·USE_TESTNET 모두 미설정 → 실거래 DB (모드 기본값과 일치)."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("USE_TESTNET", raising=False)
    assert _resolve_db_path(None) == "data/bot_live.db"


# ── 9. health 체크 교착 방지 (kline 신선도 프라이밍) ────────────────

async def test_iter_primes_kline_freshness_before_health_check(bot):
    """health 체크가 실패 상태여도 _iter()는 그 *전에* kline 신선도를 갱신한다.

    이 프라이밍이 없으면 health 체크 실패 시 _iter()가 조기 return 하면서
    신선도 갱신 코드(Layer 1+2)에 영영 도달하지 못해 "WS kline 미수신"이
    무한 반복되는 교착이 발생한다.
    """
    bot.collector.get_candles = AsyncMock(return_value=[{"close": 100.0}])
    unhealthy = SimpleNamespace(
        healthy=False, critical=False, issues=["WS kline 수신 이력 없음"],
    )
    bot.health = MagicMock()
    bot.health.check = MagicMock(return_value=unhealthy)

    await bot._iter()

    # 핵심: 조기 return 했더라도 그 전에 신선도가 갱신되어야 한다 (교착 방지)
    bot.health.record_ws_kline_received.assert_called_once()
    sent = [c.args[0] for c in bot.telegram.send.call_args_list]
    assert any("시스템 이상" in m for m in sent)


async def test_iter_priming_skips_record_when_no_candles(bot):
    """프라이밍 캔들 폴링이 빈 결과면 record_ws_kline_received 를 호출하지 않는다."""
    bot.collector.get_candles = AsyncMock(return_value=[])
    unhealthy = SimpleNamespace(healthy=False, critical=False, issues=["x"])
    bot.health = MagicMock()
    bot.health.check = MagicMock(return_value=unhealthy)

    await bot._iter()

    bot.health.record_ws_kline_received.assert_not_called()


async def test_iter_priming_survives_candle_fetch_failure(bot):
    """프라이밍 캔들 폴링이 예외를 던져도 _iter()는 중단되지 않는다."""
    bot.collector.get_candles = AsyncMock(side_effect=Exception("network down"))
    unhealthy = SimpleNamespace(healthy=False, critical=False, issues=["x"])
    bot.health = MagicMock()
    bot.health.check = MagicMock(return_value=unhealthy)

    await bot._iter()  # 예외가 밖으로 전파되지 않아야 함

    bot.health.record_ws_kline_received.assert_not_called()
