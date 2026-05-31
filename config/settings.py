"""
config/settings.py
=====================================================================
v3.1.1 전체 설정. 모든 dataclass + 모듈별 인스턴스.

근거:
  - docs/SPEC_v3.1.md §14-1 (전체 dataclass 구조)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-6 (v3.1.1 보호 자산 정책 — CapitalManagerConfig,
    PairWhitelistConfig.protected_symbols)

규칙:
  - dict/list 기본값은 field(default_factory=...) 사용 (mutable default 금지)
  - 모든 *_CONFIG 인스턴스를 모듈 레벨에서 export
=====================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

# 보호 종목 하드코딩 기본값 (운영자 수동 거래 자산 — 봇 영구 차단)
# 환경변수 PROTECTED_SYMBOLS 가 비어 있거나 오타로 빈 결과를 내면 이 값으로 폴백한다.
_DEFAULT_PROTECTED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"]


def _resolve_protected_symbols() -> List[str]:
    """PROTECTED_SYMBOLS 환경변수를 파싱하되, 빈 결과면 기본값으로 폴백한다.

    감사 H3: PROTECTED_SYMBOLS="" / 공백 / 콤마만 → 파싱 결과가 [] 가 되어
    보호 종목이 0개가 되는 무력화 위험. 빈 결과는 절대 허용하지 않고
    하드코딩 기본 리스트로 폴백 + 경고 로그.
    """
    raw = os.environ.get("PROTECTED_SYMBOLS")
    if raw is None:
        return list(_DEFAULT_PROTECTED_SYMBOLS)
    parsed = [s for s in raw.replace(" ", "").split(",") if s]
    if not parsed:
        logger.warning(
            "[Config] PROTECTED_SYMBOLS 가 비어/오타로 빈 결과 — "
            "기본 보호 종목으로 폴백: %s",
            _DEFAULT_PROTECTED_SYMBOLS,
        )
        return list(_DEFAULT_PROTECTED_SYMBOLS)
    return parsed


# ─────────────────────────────────────────────────────
# 시스템 전역 (SPEC §14-1)
# ─────────────────────────────────────────────────────
@dataclass
class SystemConfig:
    db_path: str = "data/bot.db"            # testnet/페이퍼 기본 DB (USE_TESTNET=true)
    live_db_path: str = "data/bot_live.db"  # 실거래 기본 DB (USE_TESTNET=false) — 페이퍼 데이터와 격리
    log_dir: str = "logs"
    main_loop_interval_s: int = 30          # 메인 루프 주기
    health_critical_cooldown_s: int = 60    # critical 발생 후 대기
    health_recovery_interval_s: int = 30
    min_notional_usdt: float = 5.0          # Binance 최소 명목가치
    use_testnet: bool = False               # 환경변수 USE_TESTNET 우선
    heartbeat_interval_s: int = 3600        # Telegram 하트비트 주기 (환경변수 HEARTBEAT_INTERVAL_S 우선)


SYSTEM_CONFIG = SystemConfig()


# ─────────────────────────────────────────────────────
# RegimeDetector (SPEC §14-1, §8-1)
# ─────────────────────────────────────────────────────
@dataclass
class RegimeConfig:
    # ADX
    adx_trend_threshold: float = 25.0
    adx_ranging_threshold: float = 20.0
    # ATR
    atr_high_vol_ratio: float = 2.0
    atr_high_vol_ratio_4h: float = 1.8
    atr_low_ratio: float = 0.9
    # BB
    bb_ranging_width_pct: float = 3.5
    # EMA
    ema_slope_trend_threshold: float = 0.05    # % per 봉
    # Funding
    funding_high_vol_abs: float = 0.0008       # 0.08% per 8h
    funding_trend_max_abs: float = 0.0005
    # Extreme candle
    extreme_candle_pct: float = 3.0
    # 안정성 룰 (v3.1)
    stability_streak_required: int = 3
    first_run_immediate: bool = True
    # 업데이트 주기
    update_interval_seconds: int = 60
    # 알림
    notify_on_change: bool = True


REGIME_CONFIG = RegimeConfig()


# ─────────────────────────────────────────────────────
# CostGuard (SPEC §14-1, §8-2)
# ─────────────────────────────────────────────────────
@dataclass
class CostGuardConfig:
    # 수수료 (BNB 10% 할인 적용)
    taker_fee_rate: float = 0.00045
    maker_fee_rate: float = 0.00018
    # Tier별 슬리피지 (편도)
    slippage_tier_1: float = 0.00050
    slippage_tier_2: float = 0.00100
    slippage_tier_3: float = 0.00150
    # 승률
    default_win_rate: float = 0.45             # 보수적
    min_samples_for_empirical: int = 10
    # 기대값 임계
    min_expected_value: float = 0.0
    # 진입/청산 면
    entry_is_maker: bool = True
    exit_is_taker: bool = True


COST_GUARD_CONFIG = CostGuardConfig()


# ─────────────────────────────────────────────────────
# DynamicPositionSizer (SPEC §14-1, §8-3)
# ─────────────────────────────────────────────────────
@dataclass
class SizingConfig:
    min_size_pct: float = 0.02
    absolute_max_pct: float = 0.12
    confidence_min_factor: float = 0.5
    # Kelly fraction by capital
    kelly_under_1k: float = 0.25
    kelly_1k_to_5k: float = 0.50
    kelly_above_5k: float = 0.50


SIZING_CONFIG = SizingConfig()


# ─────────────────────────────────────────────────────
# 레짐별 거래 파라미터 (SPEC §14-1)
# ─────────────────────────────────────────────────────
@dataclass
class RegimeTradingParams:
    allow_entry: bool = True
    max_leverage: int = 5
    required_quality: int = 50
    max_daily_trades: int = 5
    position_pct_cap: float = 0.10
    max_hold_minutes: int = 120


REGIME_TRADING_PARAMS: Dict[str, RegimeTradingParams] = {
    "TREND_UP": RegimeTradingParams(
        allow_entry=True, max_leverage=5, required_quality=50,
        max_daily_trades=5, position_pct_cap=0.10, max_hold_minutes=120),
    "TREND_DOWN": RegimeTradingParams(
        allow_entry=True, max_leverage=5, required_quality=55,
        max_daily_trades=4, position_pct_cap=0.10, max_hold_minutes=120),
    "RANGING": RegimeTradingParams(
        allow_entry=True, max_leverage=3, required_quality=65,
        max_daily_trades=1, position_pct_cap=0.06, max_hold_minutes=60),
    "UNCERTAIN": RegimeTradingParams(
        allow_entry=True, max_leverage=3, required_quality=60,
        max_daily_trades=2, position_pct_cap=0.05, max_hold_minutes=90),
    "HIGH_VOL": RegimeTradingParams(
        allow_entry=False, max_leverage=0, required_quality=999,
        max_daily_trades=0, position_pct_cap=0.0, max_hold_minutes=0),
}


# ─────────────────────────────────────────────────────
# 리스크 룰 (SPEC §14-1, §9 + APPENDIX E-4)
# ─────────────────────────────────────────────────────
@dataclass
class RiskRules:
    risk_per_trade_pct: float = 0.005
    max_single_trade_loss_pct: float = 0.005
    max_daily_loss_pct: float = 0.015
    max_daily_trades: int = 5
    max_total_drawdown_pct: float = 0.15
    max_consecutive_losses_warning: int = 3
    cooldown_hours_3_losses: int = 4
    max_consecutive_losses_critical: int = 5
    cooldown_hours_5_losses: int = 24
    max_consecutive_losses_terminal: int = 7
    monthly_drawdown_terminal_pct: float = 0.08
    max_concurrent_positions: int = 1
    max_concurrent_positions_above_5k: int = 2
    max_concurrent_positions_above_20k: int = 3
    min_balance_for_trade_usdt: float = 100.0
    preserve_min_balance_usdt: float = 50.0

    # Tier별 최대 레버리지
    max_leverage_by_tier: Dict[int, int] = field(default_factory=lambda: {
        1: 5, 2: 3, 3: 3, 0: 0,
    })
    # 레짐별 최대 레버리지
    max_leverage_by_regime: Dict[str, int] = field(default_factory=lambda: {
        "TREND_UP": 5, "TREND_DOWN": 5,
        "RANGING": 3, "UNCERTAIN": 3,
        "HIGH_VOL": 0,
    })


RISK_RULES = RiskRules()


# ─────────────────────────────────────────────────────
# 주간 분석 (SPEC §14-1, §8-4)
# ─────────────────────────────────────────────────────
@dataclass
class WeeklyAnalystConfig:
    enabled: bool = True
    run_day_of_week: int = 6        # 일요일 (0=월, 6=일)
    run_hour: int = 23              # UTC 23시
    lookback_days: int = 7
    report_dir: str = "reports"
    model: str = "gpt-4o-mini"
    max_tokens: int = 1500
    temperature: float = 0.3
    timeout_seconds: int = 30


WEEKLY_ANALYST_CONFIG = WeeklyAnalystConfig()


# ─────────────────────────────────────────────────────
# 거시 이벤트 (SPEC §14-1, §8-5)
# ─────────────────────────────────────────────────────
@dataclass
class MacroEventConfig:
    calendar_path: str = "config/macro_events.yaml"
    reload_interval_seconds: int = 3600
    # 차단 윈도우 (분, before/after)
    block_window_high: tuple = (-120, 120)
    block_window_medium: tuple = (-60, 60)
    block_window_low: tuple = (-30, 30)


MACRO_EVENT_CONFIG = MacroEventConfig()


# ─────────────────────────────────────────────────────
# 시스템 건강 (SPEC §14-1, §8-6)
# ─────────────────────────────────────────────────────
@dataclass
class HealthMonitorConfig:
    ws_kline_max_age_s: float = 60.0
    ws_kline_critical_age_s: float = 180.0
    ws_user_max_age_s: float = 300.0
    rest_latency_warning_ms: float = 500.0
    rest_latency_critical_ms: float = 2000.0
    rest_error_rate_warning: float = 0.10
    rest_error_rate_critical: float = 0.30
    time_diff_warning_s: float = 1.0
    time_diff_critical_s: float = 5.0
    rest_history_size: int = 100
    time_check_interval_s: int = 300


HEALTH_MONITOR_CONFIG = HealthMonitorConfig()


# ─────────────────────────────────────────────────────
# 페어 화이트리스트 (SPEC §14-1, §8-7 + APPENDIX E-6-2)
# ─────────────────────────────────────────────────────
@dataclass
class PairWhitelistConfig:
    min_listing_age_days: int = 30
    min_market_cap_rank: int = 50
    min_volume_24h_usd: float = 100_000_000
    max_avg_funding_7d_abs: float = 0.0005
    tier_3_max_concurrent: int = 1
    refresh_interval_hours: int = 24

    # v3.1.1 신규 (APPENDIX E-6-2): 보호 종목 (운영자 수동 거래 자산)
    # 환경변수 PROTECTED_SYMBOLS가 있으면 우선 사용, 없거나 빈 결과면 기본값 폴백.
    # 이 페어들은 봇이 절대 거래하지 않음 (4겹 안전망 중 코드 레벨 차단).
    # 감사 H3: 빈/오타 env 가 보호 목록을 비우지 못하도록 _resolve_protected_symbols 사용.
    protected_symbols: List[str] = field(default_factory=_resolve_protected_symbols)


PAIR_WHITELIST_CONFIG = PairWhitelistConfig()


# ─────────────────────────────────────────────────────
# 백테스트 (SPEC §14-1, §10)
# ─────────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    in_sample_months: int = 12
    out_sample_months: int = 3
    step_months: int = 1
    initial_capital: float = 1000.0
    # 보수적 비용 가정
    maker_fee: float = 0.00018
    taker_fee: float = 0.00045
    slippage_tier_1: float = 0.00050
    slippage_tier_2: float = 0.00100
    slippage_tier_3: float = 0.00150
    apply_funding: bool = True


BACKTEST_CONFIG = BacktestConfig()


# ─────────────────────────────────────────────────────
# CapitalManager (v3.1.1 신규 — APPENDIX E-6-1)
# ─────────────────────────────────────────────────────
@dataclass
class CapitalManagerConfig:
    cache_ttl_seconds: float = 10.0
    forbid_spot_access: bool = True            # Spot 호출 시 ValueError
    persist_initial_capital: bool = True       # DB에 초기 자본 저장 (재시작 시 복원)


CAPITAL_MANAGER_CONFIG = CapitalManagerConfig()


# ─────────────────────────────────────────────────────
# TradeExecutor (감사 후속 v3.1.2 — 체결 확인 + 거래소 보호 주문)
# ─────────────────────────────────────────────────────
@dataclass
class TradeExecutorConfig:
    # 진입 주문 FILLED 폴링 (감사 C2)
    fill_timeout_s: float = 15.0          # 이 시간 내 미체결이면 cancel 후 진입 스킵
    fill_poll_interval_s: float = 1.0     # futures_get_order 폴링 주기
    # exchangeInfo 심볼 필터 캐시 TTL (감사 M3/[5])
    exchange_info_ttl_s: float = 3600.0
    # 거래소 보호 주문 (감사 C1 + 2025-12-09 algoOrder 전환)
    place_take_profit: bool = True        # closePosition TAKE_PROFIT_MARKET 도 생성
    # 보호 주문 트리거 기준가 (MARK_PRICE 권장 — wick stop hunt 방지)
    working_type: str = "MARK_PRICE"      # "MARK_PRICE" / "CONTRACT_PRICE"
    # 보호 주문 가격 보호 (Binance priceProtect)
    price_protect: bool = True            # algoOrder 의 priceProtect 파라미터
    # Hedge Mode 차단 (A-4) — 이 봇은 One-way 전용. Hedge 면 live 진입 차단
    block_hedge_mode: bool = True


TRADE_EXECUTOR_CONFIG = TradeExecutorConfig()


# ─────────────────────────────────────────────────────
# LiveProbe — 소액 실거래 연결 검증 (Protected Existing Position Coexist Mode)
# ─────────────────────────────────────────────────────
def _resolve_live_probe_budget() -> float:
    """LIVE_PROBE_BUDGET_USDT 환경변수 우선, 없거나 비정상이면 300.0 로 폴백."""
    raw = os.environ.get("LIVE_PROBE_BUDGET_USDT")
    if raw is None:
        return 300.0
    try:
        v = float(raw)
    except ValueError:
        logger.warning("[Config] LIVE_PROBE_BUDGET_USDT 파싱 실패(%r) → 300.0 사용", raw)
        return 300.0
    if v <= 0:
        logger.warning("[Config] LIVE_PROBE_BUDGET_USDT <= 0 (%s) → 300.0 사용", v)
        return 300.0
    return v


@dataclass
class LiveProbeConfig:
    # 기존 보호종목 포지션/주문/algo 공존 허용 (봇은 절대 비접근 — 보존)
    allow_existing_protected_positions: bool = True
    # 신규 거래 예산 상한(USDT). 사이징 capital 을 min(available, 이 값) 으로 cap.
    # 환경변수 LIVE_PROBE_BUDGET_USDT 우선.
    live_probe_budget_usdt: float = field(default_factory=_resolve_live_probe_budget)


LIVE_PROBE_CONFIG = LiveProbeConfig()


# ─────────────────────────────────────────────────────
# Probe — 경계 있는 B-3 계측 실험 (소액 라이브, 사전확정·다조건 HALT)
# 목적: post_only 15초 역선택(B-3) 실측 + 라이브 경험. 검증 엣지 배포 아님.
# 전부 env override. PROBE_ENABLED 기본 False → off 면 행동 변화 0.
# ─────────────────────────────────────────────────────
def _resolve_probe_enabled() -> bool:
    return (os.environ.get("PROBE_ENABLED") or "").strip().lower() in ("1", "true", "yes")


def _resolve_probe_float(name: str, default: float) -> float:
    """probe env float (>=0, 음수/파싱실패 → 기본값)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        v = float(raw)
    except ValueError:
        logger.warning("[Config] %s 파싱 실패(%r) → %s 사용", name, raw, default)
        return default
    if v < 0:
        logger.warning("[Config] %s < 0 (%s) → %s 사용", name, v, default)
        return default
    return v


def _resolve_probe_int(name: str, default: int) -> int:
    """probe env int (>=0, 음수/파싱실패 → 기본값). 0 허용(테스트 강제정지 등)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("[Config] %s 파싱 실패(%r) → %s 사용", name, raw, default)
        return default
    if v < 0:
        logger.warning("[Config] %s < 0 (%s) → %s 사용", name, v, default)
        return default
    return v


@dataclass
class ProbeConfig:
    # 활성화 — 운영자 명시 opt-in(env PROBE_ENABLED). 기본 OFF(행동 변화 0).
    enabled: bool = field(default_factory=_resolve_probe_enabled)
    # 예산 cap(USDT) — 사이징 capital 상한.
    budget_usdt: float = field(default_factory=lambda: _resolve_probe_float("PROBE_BUDGET_USDT", 200.0))
    # 전면 HALT(래칭) 임계 — 손실은 양수 비율로 저장(0.15 = -15% 도달 시 HALT).
    total_loss_pct: float = field(default_factory=lambda: _resolve_probe_float("PROBE_TOTAL_LOSS_PCT", 0.15))
    daily_loss_pct: float = field(default_factory=lambda: _resolve_probe_float("PROBE_DAILY_LOSS_PCT", 0.05))
    max_consec_losses: int = field(default_factory=lambda: _resolve_probe_int("PROBE_MAX_CONSEC_LOSSES", 5))
    # 자동정지(경계 있는 실험) — 데이터 목표 도달 또는 기간 경과.
    n_fill: int = field(default_factory=lambda: _resolve_probe_int("PROBE_N_FILL", 30))
    n_unfill: int = field(default_factory=lambda: _resolve_probe_int("PROBE_N_UNFILL", 30))
    max_weeks: int = field(default_factory=lambda: _resolve_probe_int("PROBE_MAX_WEEKS", 4))
    # 동시보유 cap — ★운영자 명시 승인 완화($200서 기존 1→2). probe 모드 한정.
    max_concurrent: int = field(default_factory=lambda: _resolve_probe_int("PROBE_MAX_CONCURRENT", 2))


PROBE_CONFIG = ProbeConfig()


# ─────────────────────────────────────────────────────
# OIScanner (Layer 3 — OI 급증 후보 스캔)
# ─────────────────────────────────────────────────────
@dataclass
class OIScannerConfig:
    oi_change_threshold_pct: float = 5.0      # 후보 편입 OI 변화율 하한(%)
    price_change_threshold_pct: float = 2.0   # 후보 편입 가격 변화율 하한(절댓값,%)
    top_n: int = 10                            # 반환 최대 후보 수
    # OI 변화율 측정 윈도우 — 직전 스캔(30초) 대비가 아니라 OI 이력 기반.
    # "30초 안에 5% OI 급증"이 사실상 불가능했던 문제 교정(정량 진단 반영).
    oi_lookback_period: str = "15m"           # OI 이력 집계 주기
    oi_lookback_count: int = 1                 # 현재 vs N주기 전 비교


OISCANNER_CONFIG = OIScannerConfig()


# ─────────────────────────────────────────────────────
# BTC Risk-Off 안전 필터 (LCR 셋업 C 독립 채택, Phase 0 데이터로 검증)
# ─────────────────────────────────────────────────────
@dataclass
class BTCRiskOffConfig:
    """BTC 1h 급락 감지 시 신규 진입 자동 차단(쿨다운). 거래 엣지 아닌 *회피 룰*.

    Phase 0(2년·187종목·n=13,887): BTC 동반 급락 시 알트 LONG +4h 평균 −0.65%
    vs 단독 +0.39% — 1.03%pt 명백한 불리. 거래하지 않는 룰이라 사전확정 임계와 별개.
    """
    enabled: bool = True
    drop_threshold_pct: float = 0.012     # BTC 1h 변화 ≤ -1.2% 면 트리거
    cooldown_hours: float = 6.0           # 트리거 후 6h 신규 진입 금지


BTC_RISK_OFF_CONFIG = BTCRiskOffConfig()


# ─────────────────────────────────────────────────────
# v3.2.0 M4 — 2-tier risk hierarchy (운영자 결정 #v2-2)
# 본 import 는 governance.risk_hierarchy 의 정의를 re-export.
# 다른 *_CONFIG 와 일관성을 위해 settings 에서 노출.
# ─────────────────────────────────────────────────────
from governance.risk_hierarchy import (  # noqa: E402
    RiskHierarchyConfig as _RiskHierarchyConfig,
    RISK_HIERARCHY_CONFIG as _RISK_HIERARCHY_CONFIG,
)
RiskHierarchyConfig = _RiskHierarchyConfig
RISK_HIERARCHY_CONFIG = _RISK_HIERARCHY_CONFIG


# ─────────────────────────────────────────────────────
# v3.2.0 M7 — Shadow Agent integration (env ENABLE_SHADOW_AGENTS)
# ENABLE_SHADOW_AGENTS=true 시 main_7590._handle_signal 후 5-Agent review 자동.
# *shadow 만* — 실거래 결정 영향 X. audit_log 적재만.
# ─────────────────────────────────────────────────────
from governance.shadow_config import (  # noqa: E402
    ShadowAgentConfig as _ShadowAgentConfig,
)
ShadowAgentConfig = _ShadowAgentConfig
SHADOW_AGENT_CONFIG = _ShadowAgentConfig()


# ─────────────────────────────────────────────────────
# Microstructure Universe (Phase 2 — Tiered collection only)
# ─────────────────────────────────────────────────────
# *수집* universe 전용. 실거래 universe (PAIR_WHITELIST_CONFIG.protected_symbols 적용)
# 와 분리. 자세한 설계: data/microstructure_universe.py 모듈 docstring.
from data.microstructure_universe import (  # noqa: E402
    MicrostructureUniverseConfig as _MicrostructureUniverseConfig,
    MICROSTRUCTURE_UNIVERSE_CONFIG as _MICROSTRUCTURE_UNIVERSE_CONFIG,
)

# Re-export (config 모듈 통합 import 패턴)
MicrostructureUniverseConfig = _MicrostructureUniverseConfig
MICROSTRUCTURE_UNIVERSE_CONFIG = _MICROSTRUCTURE_UNIVERSE_CONFIG


# ─────────────────────────────────────────────────────
# 전략 선택 (P2 — 라이브 활성 매수 전략)
# ─────────────────────────────────────────────────────
@dataclass
class StrategyConfig:
    """라이브 활성 매수 전략 선택 + 돌파(Donchian/ATR) 파라미터.

    active_strategy 기본 "oi_surge"(기존 동작 유지). 환경변수 ACTIVE_STRATEGY 로
    "breakout" 선택 시 strategy/breakout(Donchian/ATR + ADX·200EMA 레짐 게이트)을
    라이브 신호원으로 사용한다. 다운스트림 게이트(PairWhitelist/보호종목/RiskManager/
    CostGuard/사이징/Executor)는 두 전략이 동일하게 통과한다.
    """

    active_strategy: str = "oi_surge"   # "oi_surge" | "breakout" | "daily_tsmom"
    breakout_interval: str = "1h"
    breakout_limit: int = 250           # ema200 + 여유(형성중 캔들 1개 제외 포함)
    breakout_donchian: int = 20
    breakout_adx_min: float = 25.0
    breakout_ema: int = 200
    breakout_atr_stop: float = 2.0
    breakout_atr_target: float = 4.0
    # v3.2.0 M15: DailyTSMOMDonchianSkill 라이브 (1d 추세추종, 6/7 CONDITIONAL).
    #   breakout(1h)과 구분 — skill.evaluate() 가 SignalDecision 생산 + registry 연결.
    daily_tsmom_interval: str = "1d"
    daily_tsmom_limit: int = 250        # ema200(min_bars=200) + 형성중 1개 제외 여유


def _resolve_active_strategy() -> str:
    """환경변수 ACTIVE_STRATEGY override (유효값만, 그 외 기본 oi_surge)."""
    import os
    v = (os.environ.get("ACTIVE_STRATEGY") or "").strip().lower()
    return v if v in ("oi_surge", "breakout", "daily_tsmom") else "oi_surge"


STRATEGY_CONFIG = StrategyConfig(active_strategy=_resolve_active_strategy())
