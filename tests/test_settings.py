"""
tests/test_settings.py
=====================================================================
config/settings.py 의 모든 dataclass 기본값 검증.

근거: docs/SPEC_v3.1.md §14-1, docs/SPEC_v3.1_APPENDIX_E.md §E-6
=====================================================================
"""

from __future__ import annotations

import os

from config.settings import (
    BacktestConfig,
    CapitalManagerConfig,
    CostGuardConfig,
    HealthMonitorConfig,
    MacroEventConfig,
    PairWhitelistConfig,
    RegimeConfig,
    RegimeTradingParams,
    LiveProbeConfig,
    RiskRules,
    SizingConfig,
    SystemConfig,
    TradeExecutorConfig,
    WeeklyAnalystConfig,
    BACKTEST_CONFIG,
    CAPITAL_MANAGER_CONFIG,
    COST_GUARD_CONFIG,
    HEALTH_MONITOR_CONFIG,
    MACRO_EVENT_CONFIG,
    PAIR_WHITELIST_CONFIG,
    REGIME_CONFIG,
    REGIME_TRADING_PARAMS,
    RISK_RULES,
    SIZING_CONFIG,
    SYSTEM_CONFIG,
    TRADE_EXECUTOR_CONFIG,
    LIVE_PROBE_CONFIG,
    WEEKLY_ANALYST_CONFIG,
)


# ── SystemConfig ──
def test_system_config_defaults():
    c = SystemConfig()
    assert c.db_path == "data/bot.db"
    assert c.live_db_path == "data/bot_live.db"
    assert c.log_dir == "logs"
    assert c.main_loop_interval_s == 30
    assert c.health_critical_cooldown_s == 60
    assert c.health_recovery_interval_s == 30
    assert c.min_notional_usdt == 5.0
    assert c.use_testnet is False
    assert isinstance(SYSTEM_CONFIG, SystemConfig)


# ── RegimeConfig ──
def test_regime_config_defaults():
    c = RegimeConfig()
    assert c.adx_trend_threshold == 25.0
    assert c.adx_ranging_threshold == 20.0
    assert c.atr_high_vol_ratio == 2.0
    assert c.atr_high_vol_ratio_4h == 1.8
    assert c.atr_low_ratio == 0.9
    assert c.bb_ranging_width_pct == 3.5
    assert c.ema_slope_trend_threshold == 0.05
    assert c.funding_high_vol_abs == 0.0008
    assert c.funding_trend_max_abs == 0.0005
    assert c.extreme_candle_pct == 3.0
    assert c.stability_streak_required == 3
    assert c.first_run_immediate is True
    assert c.update_interval_seconds == 60
    assert c.notify_on_change is True
    assert isinstance(REGIME_CONFIG, RegimeConfig)


# ── CostGuardConfig ──
def test_cost_guard_config_defaults():
    c = CostGuardConfig()
    assert c.taker_fee_rate == 0.00045
    assert c.maker_fee_rate == 0.00018
    assert c.slippage_tier_1 == 0.00050
    assert c.slippage_tier_2 == 0.00100
    assert c.slippage_tier_3 == 0.00150
    assert c.default_win_rate == 0.45
    assert c.min_samples_for_empirical == 10
    assert c.min_expected_value == 0.0
    assert c.entry_is_maker is True
    assert c.exit_is_taker is True
    assert isinstance(COST_GUARD_CONFIG, CostGuardConfig)


# ── SizingConfig ──
def test_sizing_config_defaults():
    c = SizingConfig()
    assert c.min_size_pct == 0.02
    assert c.absolute_max_pct == 0.12
    assert c.confidence_min_factor == 0.5
    assert c.kelly_under_1k == 0.25
    assert c.kelly_1k_to_5k == 0.50
    assert c.kelly_above_5k == 0.50
    assert isinstance(SIZING_CONFIG, SizingConfig)


# ── RegimeTradingParams ──
def test_regime_trading_params_defaults():
    c = RegimeTradingParams()
    assert c.allow_entry is True
    assert c.max_leverage == 5
    assert c.required_quality == 50
    assert c.max_daily_trades == 5
    assert c.position_pct_cap == 0.10
    assert c.max_hold_minutes == 120


def test_regime_trading_params_all_five_regimes():
    expected = {"TREND_UP", "TREND_DOWN", "RANGING", "UNCERTAIN", "HIGH_VOL"}
    assert set(REGIME_TRADING_PARAMS.keys()) == expected
    for params in REGIME_TRADING_PARAMS.values():
        assert isinstance(params, RegimeTradingParams)


def test_regime_trading_params_values():
    up = REGIME_TRADING_PARAMS["TREND_UP"]
    assert up.allow_entry is True and up.max_leverage == 5
    assert up.required_quality == 50 and up.max_daily_trades == 5
    assert up.position_pct_cap == 0.10 and up.max_hold_minutes == 120

    down = REGIME_TRADING_PARAMS["TREND_DOWN"]
    assert down.required_quality == 55 and down.max_daily_trades == 4

    ranging = REGIME_TRADING_PARAMS["RANGING"]
    assert ranging.max_leverage == 3 and ranging.required_quality == 65
    assert ranging.max_daily_trades == 1 and ranging.position_pct_cap == 0.06
    assert ranging.max_hold_minutes == 60

    uncertain = REGIME_TRADING_PARAMS["UNCERTAIN"]
    assert uncertain.max_leverage == 3 and uncertain.required_quality == 60
    assert uncertain.max_daily_trades == 2 and uncertain.position_pct_cap == 0.05
    assert uncertain.max_hold_minutes == 90

    high_vol = REGIME_TRADING_PARAMS["HIGH_VOL"]
    assert high_vol.allow_entry is False and high_vol.max_leverage == 0
    assert high_vol.required_quality == 999 and high_vol.max_daily_trades == 0
    assert high_vol.position_pct_cap == 0.0 and high_vol.max_hold_minutes == 0


# ── RiskRules ──
def test_risk_rules_defaults():
    c = RiskRules()
    assert c.risk_per_trade_pct == 0.005
    assert c.max_single_trade_loss_pct == 0.005
    assert c.max_daily_loss_pct == 0.015
    assert c.max_daily_trades == 5
    assert c.max_total_drawdown_pct == 0.15
    assert c.max_consecutive_losses_warning == 3
    assert c.cooldown_hours_3_losses == 4
    assert c.max_consecutive_losses_critical == 5
    assert c.cooldown_hours_5_losses == 24
    assert c.max_consecutive_losses_terminal == 7
    assert c.monthly_drawdown_terminal_pct == 0.08
    assert c.max_concurrent_positions == 1
    assert c.max_concurrent_positions_above_5k == 2
    assert c.max_concurrent_positions_above_20k == 3
    assert c.min_balance_for_trade_usdt == 100.0
    assert c.preserve_min_balance_usdt == 50.0
    assert isinstance(RISK_RULES, RiskRules)


def test_risk_rules_max_daily_loss_not_relaxed():
    # CLAUDE.md TIER 3: max_daily_loss_pct를 1.5% 초과로 완화 금지
    assert RISK_RULES.max_daily_loss_pct <= 0.015


def test_risk_rules_leverage_maps():
    c = RiskRules()
    assert c.max_leverage_by_tier == {1: 5, 2: 3, 3: 3, 0: 0}
    assert c.max_leverage_by_regime == {
        "TREND_UP": 5, "TREND_DOWN": 5,
        "RANGING": 3, "UNCERTAIN": 3, "HIGH_VOL": 0,
    }


def test_risk_rules_mutable_defaults_are_independent():
    # field(default_factory=...) 검증: 인스턴스 간 dict 공유 금지
    a = RiskRules()
    b = RiskRules()
    a.max_leverage_by_tier[1] = 999
    assert b.max_leverage_by_tier[1] == 5


# ── WeeklyAnalystConfig ──
def test_weekly_analyst_config_defaults():
    c = WeeklyAnalystConfig()
    assert c.enabled is True
    assert c.run_day_of_week == 6
    assert c.run_hour == 23
    assert c.lookback_days == 7
    assert c.report_dir == "reports"
    assert c.model == "gpt-4o-mini"
    assert c.max_tokens == 1500
    assert c.temperature == 0.3
    assert c.timeout_seconds == 30
    assert isinstance(WEEKLY_ANALYST_CONFIG, WeeklyAnalystConfig)


# ── MacroEventConfig ──
def test_macro_event_config_defaults():
    c = MacroEventConfig()
    assert c.calendar_path == "config/macro_events.yaml"
    assert c.reload_interval_seconds == 3600
    assert c.block_window_high == (-120, 120)
    assert c.block_window_medium == (-60, 60)
    assert c.block_window_low == (-30, 30)
    assert isinstance(MACRO_EVENT_CONFIG, MacroEventConfig)


# ── HealthMonitorConfig ──
def test_health_monitor_config_defaults():
    c = HealthMonitorConfig()
    assert c.ws_kline_max_age_s == 60.0
    assert c.ws_kline_critical_age_s == 180.0
    assert c.ws_user_max_age_s == 300.0
    assert c.rest_latency_warning_ms == 500.0
    assert c.rest_latency_critical_ms == 2000.0
    assert c.rest_error_rate_warning == 0.10
    assert c.rest_error_rate_critical == 0.30
    assert c.time_diff_warning_s == 1.0
    assert c.time_diff_critical_s == 5.0
    assert c.rest_history_size == 100
    assert c.time_check_interval_s == 300
    assert isinstance(HEALTH_MONITOR_CONFIG, HealthMonitorConfig)


# ── PairWhitelistConfig ──
def test_pair_whitelist_config_defaults():
    c = PairWhitelistConfig()
    assert c.min_listing_age_days == 30
    assert c.min_market_cap_rank == 50
    assert c.min_volume_24h_usd == 100_000_000
    assert c.max_avg_funding_7d_abs == 0.0005
    assert c.tier_3_max_concurrent == 1
    assert c.refresh_interval_hours == 24
    assert isinstance(PAIR_WHITELIST_CONFIG, PairWhitelistConfig)


_DEFAULT_PROTECTED = ["BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"]


def test_pair_whitelist_protected_symbols():
    # ★ v3.1.1 (APPENDIX E-6-2) + v3.1.2: 보호 종목 절대 룰 (CFX 재추가)
    assert PAIR_WHITELIST_CONFIG.protected_symbols == _DEFAULT_PROTECTED


def test_pair_whitelist_protected_symbols_mutable_default_independent():
    a = PairWhitelistConfig()
    b = PairWhitelistConfig()
    a.protected_symbols.append("XRPUSDT")
    assert b.protected_symbols == _DEFAULT_PROTECTED


def test_pair_whitelist_protected_symbols_env_override(monkeypatch):
    monkeypatch.setenv("PROTECTED_SYMBOLS", "BTCUSDT, SOLUSDT")
    c = PairWhitelistConfig()
    assert c.protected_symbols == ["BTCUSDT", "SOLUSDT"]


def test_pair_whitelist_protected_symbols_empty_env_falls_back(monkeypatch):
    # 감사 H3: 빈/공백/콤마만인 PROTECTED_SYMBOLS 는 기본값으로 폴백 (무력화 방지)
    for bad in ("", "   ", ",", " , "):
        monkeypatch.setenv("PROTECTED_SYMBOLS", bad)
        c = PairWhitelistConfig()
        assert c.protected_symbols == _DEFAULT_PROTECTED


# ── BacktestConfig ──
def test_backtest_config_defaults():
    c = BacktestConfig()
    assert c.in_sample_months == 12
    assert c.out_sample_months == 3
    assert c.step_months == 1
    assert c.initial_capital == 1000.0
    assert c.maker_fee == 0.00018
    assert c.taker_fee == 0.00045
    assert c.slippage_tier_1 == 0.00050
    assert c.slippage_tier_2 == 0.00100
    assert c.slippage_tier_3 == 0.00150
    assert c.apply_funding is True
    assert isinstance(BACKTEST_CONFIG, BacktestConfig)


# ── CapitalManagerConfig (★ v3.1.1 — APPENDIX E-6-1) ──
def test_capital_manager_config_defaults():
    c = CapitalManagerConfig()
    assert c.cache_ttl_seconds == 10.0
    assert c.forbid_spot_access is True
    assert c.persist_initial_capital is True
    assert isinstance(CAPITAL_MANAGER_CONFIG, CapitalManagerConfig)


# ── TradeExecutorConfig (v3.1.2 algoOrder/A-4 — C-1 main 연결 대상) ──
def test_trade_executor_config_defaults():
    c = TradeExecutorConfig()
    assert c.fill_timeout_s == 15.0
    assert c.fill_poll_interval_s == 1.0
    assert c.exchange_info_ttl_s == 3600.0
    assert c.place_take_profit is True
    assert c.working_type == "MARK_PRICE"
    assert c.price_protect is True
    assert c.block_hedge_mode is True
    assert isinstance(TRADE_EXECUTOR_CONFIG, TradeExecutorConfig)


# ── LiveProbeConfig (Protected Existing Position Coexist Mode) ──
def test_live_probe_config_defaults():
    c = LiveProbeConfig()
    assert c.allow_existing_protected_positions is True
    assert c.live_probe_budget_usdt == 300.0
    assert isinstance(LIVE_PROBE_CONFIG, LiveProbeConfig)


def test_live_probe_budget_env_override(monkeypatch):
    monkeypatch.setenv("LIVE_PROBE_BUDGET_USDT", "150")
    assert LiveProbeConfig().live_probe_budget_usdt == 150.0


def test_live_probe_budget_invalid_env_falls_back(monkeypatch):
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv("LIVE_PROBE_BUDGET_USDT", bad)
        assert LiveProbeConfig().live_probe_budget_usdt == 300.0


# ── 패키지 export ──
def test_config_package_exports_all_instances():
    import config
    for name in [
        "SYSTEM_CONFIG", "REGIME_CONFIG", "COST_GUARD_CONFIG", "SIZING_CONFIG",
        "REGIME_TRADING_PARAMS", "RISK_RULES", "WEEKLY_ANALYST_CONFIG",
        "MACRO_EVENT_CONFIG", "HEALTH_MONITOR_CONFIG", "PAIR_WHITELIST_CONFIG",
        "BACKTEST_CONFIG", "CAPITAL_MANAGER_CONFIG",
    ]:
        assert hasattr(config, name), f"config 패키지에 {name} 누락"
