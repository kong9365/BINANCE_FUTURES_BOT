"""
config 패키지 — 모든 *_CONFIG 인스턴스 및 dataclass export.

근거: docs/SPEC_v3.1.md §14, docs/SPEC_v3.1_APPENDIX_E.md §E-6
"""

from __future__ import annotations

from config.settings import (
    # dataclass 정의
    SystemConfig,
    RegimeConfig,
    CostGuardConfig,
    SizingConfig,
    RegimeTradingParams,
    RiskRules,
    WeeklyAnalystConfig,
    MacroEventConfig,
    HealthMonitorConfig,
    PairWhitelistConfig,
    BacktestConfig,
    CapitalManagerConfig,
    # 인스턴스
    SYSTEM_CONFIG,
    REGIME_CONFIG,
    COST_GUARD_CONFIG,
    SIZING_CONFIG,
    REGIME_TRADING_PARAMS,
    RISK_RULES,
    WEEKLY_ANALYST_CONFIG,
    MACRO_EVENT_CONFIG,
    HEALTH_MONITOR_CONFIG,
    PAIR_WHITELIST_CONFIG,
    BACKTEST_CONFIG,
    CAPITAL_MANAGER_CONFIG,
)

__all__ = [
    "SystemConfig",
    "RegimeConfig",
    "CostGuardConfig",
    "SizingConfig",
    "RegimeTradingParams",
    "RiskRules",
    "WeeklyAnalystConfig",
    "MacroEventConfig",
    "HealthMonitorConfig",
    "PairWhitelistConfig",
    "BacktestConfig",
    "CapitalManagerConfig",
    "SYSTEM_CONFIG",
    "REGIME_CONFIG",
    "COST_GUARD_CONFIG",
    "SIZING_CONFIG",
    "REGIME_TRADING_PARAMS",
    "RISK_RULES",
    "WEEKLY_ANALYST_CONFIG",
    "MACRO_EVENT_CONFIG",
    "HEALTH_MONITOR_CONFIG",
    "PAIR_WHITELIST_CONFIG",
    "BACKTEST_CONFIG",
    "CAPITAL_MANAGER_CONFIG",
]
