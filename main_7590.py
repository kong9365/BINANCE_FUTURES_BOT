"""
main_7590.py
=====================================================================
MainBot — Binance USDT-M Futures 단타 자동매매 봇 v3.1.1 통합 진입점

근거:
  - docs/SPEC_v3.1.md §8-8 (통합 흐름: __init__ / _main_loop / _iter /
    _handle_signal / WS 콜백)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-5 (CapitalManager 통합, 시작 시퀀스)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-7 (capital_initial / 자본 상태 컬럼)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-8 (API 키 권한 검증)

v3.1.1 핵심:
  - 자본 조회는 전부 CapitalManager.get_snapshot() 경유 (Spot 격리)
  - 사이즈 계산은 available_balance 기준 + 마진 부족 추가 검증
  - protected_symbols (BTCUSDT/ETHUSDT/HOLOUSDT/LYNUSDT)는 PairWhitelist 가
    최우선 차단 — 봇이 절대 거래하지 않음
  - 시작 시퀀스: 초기 자본 기록 + 보호 종목 알림 + API 권한 검증

설계 메모:
  - 명세서 §8-8 의사코드를 기존 모듈의 실제 시그니처에 맞춰 구현했다
    (risk_manager.check_all() 은 인자 없는 async, capital_manager.get_snapshot()
    은 async, oi_scanner.scan() 은 async 등).
  - 모든 외부 의존성(collector / openai / telegram)은 생성자 주입이 가능하다
    (tests/test_main_integration.py 가 mock 으로 전체 흐름을 검증).
  - 보안: API 키/시크릿은 코드·로그·예외 어디에도 출력하지 않는다 (TIER 1 #4).
=====================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 미설치 — .env 자동 로드 비활성화
    load_dotenv = None

from analytics.expectancy import ExpectancyAnalyzer
from analytics.macro_event_analyzer import MacroEventAnalyzer
from analytics.shadow_mode import ShadowRecorder

# ── 분석 / 운영 ──
from analytics.weekly_report import WeeklyGPTAnalyst
from config.settings import (
    CAPITAL_MANAGER_CONFIG,
    COST_GUARD_CONFIG,
    HEALTH_MONITOR_CONFIG,
    LIVE_PROBE_CONFIG,
    MACRO_EVENT_CONFIG,
    PAIR_WHITELIST_CONFIG,
    REGIME_CONFIG,
    REGIME_TRADING_PARAMS,
    RISK_RULES,
    SIZING_CONFIG,
    SYSTEM_CONFIG,
    TRADE_EXECUTOR_CONFIG,
    WEEKLY_ANALYST_CONFIG,
)

# ── 신규 v3.1.1 모듈 ──
from data.capital_manager import CapitalManager
from data.collector import BinanceDataCollector
from data.oi_scanner import OIScanner

# ── DB / 설정 ──
from db.init_db import init_db
from ops.system_health_monitor import SystemHealthMonitor
from sizing.dynamic_sizer import DynamicPositionSizer
from strategy.cost_guard import CostGuard
from strategy.oi_filter import GRADE_C_DANGER, OIFilter
from strategy.pair_whitelist import PairWhitelist
from strategy.quality_gate import QualityGate

# ── 전략 / 사이징 ──
from strategy.regime_detector import Regime, RegimeDetector
from trading.executor import TradeExecutor
from trading.exit_plan import ExitPlanController

# ── 거래 ──
from trading.risk_manager import RiskManager

logger = logging.getLogger(__name__)

# 메인 루프 데이터 기준 페어 (레짐 감지·시장 컨텍스트용 — BTC 시장 전체 대표)
_CONTEXT_SYMBOL = "BTCUSDT"
_SHADOW_STRATEGY = "v3_1_main"


# =====================================================================
# Telegram 알림
# =====================================================================
class _NullNotifier:
    """Telegram 미설정 시 폴백 — 메시지를 로그로만 남긴다."""

    async def send(self, text: str) -> None:
        logger.info("[Telegram:disabled] %s", text)


class TelegramNotifier:
    """python-telegram-bot 기반 알림 전송기.

    전송 실패는 삼킨다 (알림 실패가 메인 루프를 막지 않도록).
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.chat_id = chat_id
        # 지연 import: telegram 미설치 환경에서도 모듈 import 가 깨지지 않게 한다.
        from telegram import Bot

        self._bot = Bot(token=bot_token)

    async def send(self, text: str) -> None:
        try:
            await self._bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            logger.warning("[Telegram] 전송 실패: %s", e)


def _build_notifier() -> TelegramNotifier | _NullNotifier:
    """.env 의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 로 알림기를 생성한다.

    토큰이 없거나 생성 실패 시 _NullNotifier 로 폴백한다.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("[Telegram] 토큰/chat_id 미설정 — 알림 비활성화")
        return _NullNotifier()
    try:
        return TelegramNotifier(token, chat_id)
    except Exception as e:
        logger.warning("[Telegram] 알림기 생성 실패(%s) — 비활성화", type(e).__name__)
        return _NullNotifier()


def _build_openai_client():
    """.env 의 OPENAI_API_KEY 로 openai 클라이언트를 생성한다 (없으면 None).

    WeeklyGPTAnalyst 전용 — 실시간 의사결정에는 사용하지 않는다 (TIER 3).
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        logger.warning("[OpenAI] OPENAI_API_KEY 미설정 — 주간 분석 비활성화")
        return None
    try:
        from openai import OpenAI

        return OpenAI(api_key=key)
    except Exception as e:
        logger.warning("[OpenAI] 클라이언트 생성 실패(%s)", type(e).__name__)
        return None


def _resolve_db_path(db_path: str | None) -> str:
    """사용할 sqlite DB 경로를 결정한다.

    우선순위:
      1. 명시적 db_path 인자 (테스트·직접 주입)
      2. 환경변수 DB_PATH (운영자 명시 override)
      3. USE_TESTNET 에 따른 기본값 — true → SYSTEM_CONFIG.db_path(페이퍼),
         false/미설정 → SYSTEM_CONFIG.live_db_path(실거래)

    실거래와 페이퍼 데이터를 분리해 초기 자본·MDD 기준이 섞이지 않게 한다.
    """
    if db_path is not None:
        return db_path
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    use_testnet = os.environ.get("USE_TESTNET", "").strip().lower() in (
        "1", "true", "yes",
    )
    return SYSTEM_CONFIG.db_path if use_testnet else SYSTEM_CONFIG.live_db_path


def _build_cmc_client():
    """.env 의 CMC_API_KEY 로 CoinMarketCap 클라이언트를 생성한다 (없으면 None).

    PairWhitelist 의 Tier 3 시총 검증 전용 — 미설정 시 PairWhitelist 는
    Binance 데이터만으로 동작한다 (시총 검증 스킵).
    """
    key = os.environ.get("CMC_API_KEY")
    if not key:
        logger.info("[CMC] CMC_API_KEY 미설정 — PairWhitelist 시총 검증 비활성화")
        return None
    try:
        from data.cmc_client import CMCClient

        return CMCClient(key)
    except Exception as e:  # noqa: BLE001 — 키 노출 방지 위해 유형만 로깅
        logger.warning("[CMC] 클라이언트 생성 실패(%s)", type(e).__name__)
        return None


# =====================================================================
# MainBot
# =====================================================================
class MainBot:
    """v3.1.1 통합 메인 봇.

    사용:
        bot = MainBot(dry_run=True)
        await bot.start()
    """

    def __init__(
        self,
        dry_run: bool = False,
        db_path: str | None = None,
        collector: BinanceDataCollector | None = None,
        openai_client=None,
        telegram=None,
        cmc_client=None,
    ) -> None:
        """봇 초기화 — 14개 모듈 조립.

        Args:
            dry_run: True 면 거래소 주문을 스킵하고 DB 기록만 수행 (페이퍼).
            db_path: sqlite3 DB 경로. None 이면 환경변수 DB_PATH 또는
                SYSTEM_CONFIG.db_path.
            collector: BinanceDataCollector 주입 (테스트용). None 이면 .env 기반 생성.
            openai_client: openai 클라이언트 주입. None 이면 .env 기반 생성.
            telegram: 알림기 주입 (async send 보유). None 이면 .env 기반 생성.
            cmc_client: CoinMarketCap 클라이언트 주입. None 이면 .env 기반 생성
                (CMC_API_KEY 미설정 시 결과적으로 None — 시총 검증 비활성화).
        """
        self.dry_run = dry_run
        self.db_path = _resolve_db_path(db_path)
        self._running = False

        # ── 외부 의존성 ──
        self.collector = collector or BinanceDataCollector()
        self.binance = self.collector.client
        self.openai = openai_client if openai_client is not None else _build_openai_client()
        self.telegram = telegram if telegram is not None else _build_notifier()
        self.cmc = cmc_client if cmc_client is not None else _build_cmc_client()

        # ── v3.1.1: CapitalManager (자본 조회 단일 창구) ──
        self.capital_manager = CapitalManager(
            binance_client=self.binance,
            cache_ttl_seconds=CAPITAL_MANAGER_CONFIG.cache_ttl_seconds,
            forbid_spot_access=CAPITAL_MANAGER_CONFIG.forbid_spot_access,
        )

        # ── 데이터 / 스캔 ──
        self.oi_scanner = OIScanner(self.collector)

        # ── 전략 / 사이징 ──
        self.regime_detector = RegimeDetector(
            adx_trend_threshold=REGIME_CONFIG.adx_trend_threshold,
            adx_ranging_threshold=REGIME_CONFIG.adx_ranging_threshold,
            atr_high_vol_ratio=REGIME_CONFIG.atr_high_vol_ratio,
            atr_high_vol_ratio_4h=REGIME_CONFIG.atr_high_vol_ratio_4h,
            atr_low_ratio=REGIME_CONFIG.atr_low_ratio,
            bb_ranging_width_pct=REGIME_CONFIG.bb_ranging_width_pct,
            ema_slope_trend_threshold=REGIME_CONFIG.ema_slope_trend_threshold,
            funding_high_vol_abs=REGIME_CONFIG.funding_high_vol_abs,
            funding_trend_max_abs=REGIME_CONFIG.funding_trend_max_abs,
            extreme_candle_pct=REGIME_CONFIG.extreme_candle_pct,
            stability_streak_required=REGIME_CONFIG.stability_streak_required,
            first_run_immediate=REGIME_CONFIG.first_run_immediate,
        )
        self.cost_guard = CostGuard(
            taker_fee_rate=COST_GUARD_CONFIG.taker_fee_rate,
            maker_fee_rate=COST_GUARD_CONFIG.maker_fee_rate,
            default_win_rate=COST_GUARD_CONFIG.default_win_rate,
            min_expected_value=COST_GUARD_CONFIG.min_expected_value,
            entry_is_maker=COST_GUARD_CONFIG.entry_is_maker,
            exit_is_taker=COST_GUARD_CONFIG.exit_is_taker,
        )
        self.oi_filter = OIFilter()
        self.quality_gate = QualityGate()
        self.sizer = DynamicPositionSizer(
            min_size_pct=SIZING_CONFIG.min_size_pct,
            absolute_max_pct=SIZING_CONFIG.absolute_max_pct,
            confidence_min_factor=SIZING_CONFIG.confidence_min_factor,
        )

        # ── v3.1.1: PairWhitelist (protected_symbols + CMC 시총 검증 주입) ──
        self.pair_wl = PairWhitelist(
            binance_client=self.binance,
            cmc_client=self.cmc,
            protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols,
            min_listing_age_days=PAIR_WHITELIST_CONFIG.min_listing_age_days,
            min_market_cap_rank=PAIR_WHITELIST_CONFIG.min_market_cap_rank,
            min_volume_24h_usd=PAIR_WHITELIST_CONFIG.min_volume_24h_usd,
            max_avg_funding_7d_abs=PAIR_WHITELIST_CONFIG.max_avg_funding_7d_abs,
            tier_3_max_concurrent=PAIR_WHITELIST_CONFIG.tier_3_max_concurrent,
        )

        # ── 분석 / 운영 ──
        # 감사 M5: live(not dry_run)에서는 거시 캘린더 부재/오류 시 신규 진입을
        # 차단한다(FOMC/CPI 를 통과 거래하지 않도록). 페이퍼/테스트는 경고만.
        self.macro_analyzer = MacroEventAnalyzer(
            calendar_path=MACRO_EVENT_CONFIG.calendar_path,
            reload_interval_seconds=MACRO_EVENT_CONFIG.reload_interval_seconds,
            block_on_missing_calendar=not dry_run,
        )
        self.health = SystemHealthMonitor(
            binance_client=self.binance,
            ws_kline_max_age_s=HEALTH_MONITOR_CONFIG.ws_kline_max_age_s,
            ws_kline_critical_age_s=HEALTH_MONITOR_CONFIG.ws_kline_critical_age_s,
            ws_user_max_age_s=HEALTH_MONITOR_CONFIG.ws_user_max_age_s,
            rest_latency_warning_ms=HEALTH_MONITOR_CONFIG.rest_latency_warning_ms,
            rest_latency_critical_ms=HEALTH_MONITOR_CONFIG.rest_latency_critical_ms,
            rest_error_rate_warning=HEALTH_MONITOR_CONFIG.rest_error_rate_warning,
            rest_error_rate_critical=HEALTH_MONITOR_CONFIG.rest_error_rate_critical,
            time_diff_warning_s=HEALTH_MONITOR_CONFIG.time_diff_warning_s,
            time_diff_critical_s=HEALTH_MONITOR_CONFIG.time_diff_critical_s,
            rest_history_size=HEALTH_MONITOR_CONFIG.rest_history_size,
            time_check_interval_s=HEALTH_MONITOR_CONFIG.time_check_interval_s,
        )
        self.expectancy = ExpectancyAnalyzer(db_path=self.db_path)
        self.shadow = ShadowRecorder(db_path=self.db_path)
        self.weekly_analyst = WeeklyGPTAnalyst(
            openai_client=self.openai,
            expectancy_analyzer=self.expectancy,
            db_path=self.db_path,
            model=WEEKLY_ANALYST_CONFIG.model,
            report_dir=WEEKLY_ANALYST_CONFIG.report_dir,
            max_tokens=WEEKLY_ANALYST_CONFIG.max_tokens,
            temperature=WEEKLY_ANALYST_CONFIG.temperature,
            timeout_seconds=WEEKLY_ANALYST_CONFIG.timeout_seconds,
        )

        # ── v3.1.1: RiskManager (capital_manager 주입) ──
        self.risk_manager = RiskManager(
            db_path=self.db_path,
            capital_manager=self.capital_manager,
        )

        # ── 거래 실행 / 청산 관리 ──
        # C-1: TRADE_EXECUTOR_CONFIG 를 명시 주입 (운영자가 settings 에서 조정 가능).
        # 기본값은 executor.__init__ 기본값과 동일하므로 기존 동작 불변.
        self.executor = TradeExecutor(
            self.binance,
            db_path=self.db_path,
            dry_run=dry_run,
            fill_timeout_s=TRADE_EXECUTOR_CONFIG.fill_timeout_s,
            fill_poll_interval_s=TRADE_EXECUTOR_CONFIG.fill_poll_interval_s,
            exchange_info_ttl_s=TRADE_EXECUTOR_CONFIG.exchange_info_ttl_s,
            place_take_profit=TRADE_EXECUTOR_CONFIG.place_take_profit,
            working_type=TRADE_EXECUTOR_CONFIG.working_type,
            price_protect=TRADE_EXECUTOR_CONFIG.price_protect,
            block_hedge_mode=TRADE_EXECUTOR_CONFIG.block_hedge_mode,
            # Protected Existing Position Coexist Mode — 보호종목 기존 보유분 보존
            protected_symbols=PAIR_WHITELIST_CONFIG.protected_symbols,
        )
        # default_max_hold_minutes 는 폴백값 — 실제로는 _handle_signal 이
        # 진입 시 params.max_hold_minutes 로 매번 명시 주입한다.
        self.exit_plan = ExitPlanController(self.executor)

        # ── WS 콜백 등록 (§8-8-4) ──
        self.collector.register_kline_callback(self._on_ws_kline)
        self.collector.register_user_callback(self._on_ws_user_data)

        # ── 상태 ──
        self._last_weekly_run: datetime | None = None
        self._current_regime_state = None

        # ── 하트비트 상태 (Telegram 주기 알림) ──
        self._heartbeat_interval_s = int(
            os.environ.get("HEARTBEAT_INTERVAL_S")
            or SYSTEM_CONFIG.heartbeat_interval_s
        )
        self._started_at: datetime | None = None
        self._last_heartbeat: datetime | None = None
        self._loop_count = 0
        self._last_candidate_count = 0

        logger.info(
            "[MainBot] 초기화 완료 (dry_run=%s, db=%s, 보호종목=%s)",
            dry_run, self.db_path, sorted(self.pair_wl.protected_symbols),
        )

    # =================================================================
    # 시작 시퀀스 (부록 E-5-3 + E-8-2)
    # =================================================================
    async def start(self) -> None:
        """봇 시작 시퀀스 — DB 초기화 → API 권한 검증 → 초기 자본 기록 →
        보호 종목 알림 → 페어 갱신 → 메인 루프."""
        self._started_at = datetime.now(timezone.utc)
        logger.info("[Start] v3.1.1 봇 시작 시퀀스 개시 (dry_run=%s)", self.dry_run)

        # 1) DB 초기화 (멱등)
        try:
            init_db(self.db_path)
        except Exception as e:
            logger.error("[Start] DB 초기화 실패: %s", e)
            raise

        # 2) API 키 권한 검증 (부록 E-8-2)
        await self._verify_api_key_permissions()

        # 3) 초기 자본 조회 (initial_balance 기록)
        try:
            initial_snapshot = await self.capital_manager.get_snapshot(force_refresh=True)
        except Exception as e:
            logger.error("[Start] 초기 자본 조회 실패: %s", e)
            raise

        logger.info(
            "[Start] 초기 자본: wallet=$%.2f, available=$%.2f, locked_margin=$%.2f",
            initial_snapshot.wallet_balance,
            initial_snapshot.available_balance,
            initial_snapshot.locked_margin,
        )

        # 4) 초기 자본 영속화 (부록 E-7-2 — 재시작 시 복원)
        self._sync_initial_capital(initial_snapshot)

        # 4-b) live 계좌 사전 점검 (Protected Existing Position Coexist Mode)
        #     기존 포지션/주문/algo 가 모두 보호종목이면 공존 허용, 비보호 잔존 시 차단.
        #     dry_run 은 거래소 조회를 하지 않는다(API 미호출 원칙).
        if not self.dry_run:
            await self._live_account_preflight()

        # 5) 보호 종목 확인 + Telegram 알림 (부록 E-5-3)
        if self.pair_wl.protected_symbols:
            await self.telegram.send(
                "🛡️ 보호 종목 활성:\n"
                + "\n".join(f"- {s}" for s in sorted(self.pair_wl.protected_symbols))
                + "\n이 페어들은 봇이 절대 거래하지 않습니다."
            )

        # 6) 페어 화이트리스트 초기 갱신
        try:
            self.pair_wl.refresh()
        except Exception as e:
            logger.error("[Start] 페어 화이트리스트 갱신 실패: %s", e)

        # 7) WS 스트림 시작 (스텁 — 드라이런/테스트는 REST 폴링)
        self.collector.start_ws()

        # 7-b) 데이터 신선도 프라이밍 — REST 폴링 모드에서 첫 _iter 의 health
        # 체크가 'kline 수신 이력 없음' 으로 실패하지 않도록 1회 폴링 후 기록한다.
        try:
            primer = await self.collector.get_candles(_CONTEXT_SYMBOL, "4h", 1)
            if primer:
                self.health.record_ws_kline_received()
        except Exception as e:  # noqa: BLE001 — 프라이밍 실패해도 루프는 진행
            logger.warning("[Start] 데이터 신선도 프라이밍 실패: %s", e)

        await self.telegram.send(
            f"🚀 봇 가동 시작 (v3.1.1, {'DRY-RUN' if self.dry_run else 'LIVE'})"
        )

        # 8) 메인 루프
        await self._main_loop()

    async def _verify_api_key_permissions(self) -> None:
        """API 키 권한 검증 — Spot 권한이 활성화돼 있으면 경고 (부록 E-8-2).

        Spot account 조회를 시도하여 성공하면 Spot 권한이 켜져 있는 것이므로
        보안 강화를 위해 비활성화를 권고한다 (보호 자산 격리).
        """
        if self.binance is None:
            logger.warning("[Security] binance 클라이언트 없음 — 권한 검증 스킵")
            return
        try:
            await asyncio.to_thread(self.binance.get_account)
            # 여기 도달 = Spot 권한 있음 → 경고
            await self.telegram.send(
                "⚠️ API 키에 Spot 권한이 활성화되어 있습니다. "
                "보안 강화를 위해 비활성화를 권장합니다 (부록 E-8)."
            )
            logger.warning("[Security] Spot 권한 활성화 감지")
        except Exception as e:
            msg = str(e).lower()
            if "403" in msg or "invalid" in msg or "permission" in msg or "-2015" in msg:
                logger.info("[Security] Spot 권한 없음 (정상 — 보호 자산 격리)")
            else:
                logger.warning("[Security] 권한 확인 불가: %s", e)

    def _sync_initial_capital(self, snapshot) -> None:
        """초기 자본을 DB(capital_initial)와 동기화한다 (부록 E-7-2).

        - 유효한(0 초과) 활성 행이 있으면 그 값으로 CapitalManager 의 초기 자본을
          복원한다 (재시작 대응).
        - 활성 행이 0 이하면(자금 입금 전에 가동돼 잘못 기록된 경우) 비활성화하고
          현재 스냅샷으로 재기록한다.
        - 활성 행이 없으면 현재 wallet_balance 를 신규 기록한다.
        - 단, 현재 wallet_balance 가 0 이하면 기록하지 않는다 (유효한 시작점이
          아니므로 — 자금 입금 후 재시작 시 정상 기록된다).
        """
        if not CAPITAL_MANAGER_CONFIG.persist_initial_capital:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT initial_wallet_balance FROM capital_initial "
                    "WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                ).fetchone()

                # 유효한 활성 행 → 복원하고 종료
                if row is not None and float(row[0]) > 0:
                    self.capital_manager.set_initial_capital(float(row[0]))
                    logger.info("[Start] 초기 자본 DB 복원: $%.2f", float(row[0]))
                    return

                # 무효(0 이하) 활성 행 → 비활성화 (잘못 기록된 초기 자본 정리)
                if row is not None:
                    conn.execute(
                        "UPDATE capital_initial SET is_active = 0 WHERE is_active = 1"
                    )
                    logger.warning(
                        "[Start] 무효한 초기 자본 기록($%.2f) 비활성화 — 재기록 시도",
                        float(row[0]),
                    )

                # 현재 스냅샷이 유효하면 신규 기록
                if snapshot.wallet_balance > 0:
                    conn.execute(
                        "INSERT INTO capital_initial "
                        "(recorded_at, initial_wallet_balance, note, is_active) "
                        "VALUES (?, ?, ?, 1)",
                        (
                            datetime.now(timezone.utc).isoformat(),
                            snapshot.wallet_balance,
                            "최초 가동",
                        ),
                    )
                    conn.commit()
                    logger.info(
                        "[Start] 초기 자본 DB 신규 기록: $%.2f",
                        snapshot.wallet_balance,
                    )
                else:
                    conn.commit()  # 비활성화만 반영
                    logger.warning(
                        "[Start] 현재 wallet_balance $%.2f — 초기 자본 미기록 "
                        "(자금 입금 후 재시작 시 기록됨)",
                        snapshot.wallet_balance,
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.error("[Start] 초기 자본 영속화 실패: %s", e)

    # =================================================================
    # 메인 루프
    # =================================================================
    async def _main_loop(self) -> None:
        """v3.1.1 통합 메인 루프 — _iter() 를 주기적으로 실행한다."""
        self._running = True
        logger.info(
            "[Main] 메인 루프 시작 (interval=%ds)",
            SYSTEM_CONFIG.main_loop_interval_s,
        )
        while self._running:
            try:
                await self._iter()
            except Exception as e:
                logger.exception("[Main] 루프 예외: %s", e)
                await self.telegram.send(
                    f"⚠️ 메인 루프 예외: {type(e).__name__}: {e}"
                )
            # 하트비트는 _iter 성공·실패와 무관하게 평가한다 (생존 신호 유지).
            await self._maybe_send_heartbeat()
            await asyncio.sleep(SYSTEM_CONFIG.main_loop_interval_s)

    async def _iter(self) -> None:
        """메인 루프 1회 — Layer 0~6 (§8-8-3 + 부록 E-5-2)."""
        self._loop_count += 1

        # ── 데이터 신선도 프라이밍 (Layer 0 이전) ──
        # WS 는 스텁이므로 REST 폴링 성공을 kline 신선도로 인정한다. 이 갱신을
        # health 체크 *이후* 의 Layer 1+2 에서만 하면, health 체크가 한 번
        # 실패하는 순간 _iter() 가 조기 return 하면서 갱신 코드에 영영 도달하지
        # 못해 "WS kline 미수신" 이 무한 반복되는 교착이 생긴다. 따라서 health
        # 체크 전에 가벼운 캔들 1개 폴링으로 신선도를 먼저 갱신한다
        # (start() 7-b 프라이밍과 동일한 패턴).
        try:
            primer = await self.collector.get_candles(_CONTEXT_SYMBOL, "4h", 1)
            if primer:
                self.health.record_ws_kline_received()
        except Exception as e:  # noqa: BLE001 — 프라이밍 실패해도 루프는 진행
            logger.warning("[Main] 데이터 신선도 프라이밍 실패: %s", e)

        # ── Layer 0: 시스템 건강 체크 ──
        health = self.health.check()
        if not health.healthy:
            msg = "⚠️ 시스템 이상:\n" + "\n".join(f"- {i}" for i in health.issues)
            await self.telegram.send(msg)
            if health.critical:
                logger.critical(
                    "[Main] CRITICAL: %s → 모든 포지션 안전 청산", health.issues
                )
                await self._close_all_positions_safe()
                await asyncio.sleep(SYSTEM_CONFIG.health_critical_cooldown_s)
            return  # 이번 iteration 신규 진입 차단

        # ── v3.1.1: 자본 스냅샷 (기존 _fetch_account_balance 대체) ──
        try:
            capital_snapshot = await self.capital_manager.get_snapshot()
        except Exception as e:
            logger.error("[Main] 자본 조회 실패: %s", e)
            return

        # ── Layer 1+2: 데이터 + 시장 컨텍스트 ──
        candles_4h = await self.collector.get_candles(_CONTEXT_SYMBOL, "4h", 100)
        candles_1h = await self.collector.get_candles(_CONTEXT_SYMBOL, "1h", 100)
        funding = await self.collector.get_funding_rate(_CONTEXT_SYMBOL)

        # REST 폴링 성공 = 데이터 신선도 신호 — health monitor 에 기록한다
        # (WS 스텁/REST 폴링 모드에서 다음 루프 health 체크가 통과하도록).
        if candles_4h:
            self.health.record_ws_kline_received()

        macro_blocked = self.macro_analyzer.is_blocked(_CONTEXT_SYMBOL)
        regime_state = self.regime_detector.detect(
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            funding_rate=funding,
            macro_blocked=macro_blocked,
        )
        self._current_regime_state = regime_state

        if regime_state.regime_changed:
            await self.telegram.send(
                f"🔄 레짐 전환: {regime_state.prev_regime} → {regime_state.regime}\n"
                f"신뢰도: {regime_state.confidence:.0%}\n"
                f"사유: {', '.join(regime_state.reasons)}"
            )
            self._db_log_regime_change(regime_state)

        # ── 기존 포지션은 ExitPlanController 가 자동 관리 (HIGH_VOL 여부 무관) ──
        await self._manage_open_positions()

        # ── HIGH_VOL 또는 거시 이벤트 → 신규 진입 차단 ──
        if regime_state.regime == Regime.HIGH_VOL or macro_blocked:
            logger.info(
                "[Main] 신규 진입 차단: regime=%s, macro=%s",
                regime_state.regime, macro_blocked,
            )
            return

        # ── Layer 3+4: 신호 + 게이트 ──
        active_pairs = self.pair_wl.get_active(
            capital=capital_snapshot.wallet_balance,
            regime=regime_state.regime,
        )
        candidates = await self.oi_scanner.scan(active_pairs)
        self._last_candidate_count = len(candidates)

        for candidate in candidates:
            await self._handle_signal(candidate, regime_state, capital_snapshot)

        # ── 주기적 배치 트리거 ──
        await self._maybe_run_weekly()
        await self._maybe_refresh_pairs(capital_snapshot)

    # =================================================================
    # 신호 처리 (부록 E-5-2)
    # =================================================================
    async def _handle_signal(self, candidate, regime_state, capital_snapshot) -> None:
        """단일 신호 처리 — 페어 → OI 필터 → 품질 → 리스크 → 사이징 →
        CostGuard → 실행 (부록 E-5-2)."""
        symbol = candidate.symbol

        # 1) 페어 화이트리스트 (protected_symbols 자동 차단)
        if not self.pair_wl.is_allowed(
            symbol,
            capital=capital_snapshot.wallet_balance,
            regime=regime_state.regime,
        ):
            logger.info("[Main] %s 페어 차단 (protected/tier/capital/regime)", symbol)
            return

        # 2) OI 필터 (레짐 컨텍스트)
        oi_result = self.oi_filter.evaluate(candidate, regime_state)
        if oi_result.grade == GRADE_C_DANGER:
            logger.info("[Filter] %s C_DANGER: %s", symbol, oi_result.reasons)
            self.shadow.record_blocked("oi_filter", candidate, oi_result)
            return

        # 3) 레짐별 임계 품질 점수
        params = REGIME_TRADING_PARAMS[regime_state.regime]
        quality = self.quality_gate.check(
            candidate, oi_result, required_score=params.required_quality
        )
        if not quality.passed:
            self.shadow.record_blocked("quality_gate", candidate, quality)
            return

        # 4) 리스크 한도 (capital_manager 자동 조회)
        if not await self.risk_manager.check_all():
            logger.info("[Risk] %s 리스크 한도 위반", symbol)
            self.shadow.record_blocked("risk_manager", candidate, None)
            return

        # 5) 사이징 — available_balance 기준 (부록 E-5-2)
        #    + LIVE_PROBE_BUDGET_USDT cap: 소액 실거래 연결 검증 동안 신규 거래
        #      예산을 제한한다. capital = min(available, live_probe_budget).
        win_rate, sample_count = self.expectancy.get_win_rate(
            quality.setup_tag, return_count=True
        )
        avg_win, avg_loss = self.expectancy.get_avg_R(quality.setup_tag)
        budget_cap = min(
            capital_snapshot.available_balance,
            LIVE_PROBE_CONFIG.live_probe_budget_usdt,
        )
        sizing = self.sizer.calculate(
            capital=budget_cap,
            win_rate=win_rate,
            avg_win_R=avg_win,
            avg_loss_R=avg_loss,
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            sample_count=sample_count,
        )
        if sizing.size_usdt < SYSTEM_CONFIG.min_notional_usdt:
            logger.info(
                "[Sizer] %s 최소 명목가치 미달: $%.2f", symbol, sizing.size_usdt
            )
            return

        # 예산/마진 초과 검증 (부록 E-5-2 + LIVE_PROBE_BUDGET cap)
        if sizing.size_usdt > budget_cap:
            logger.warning(
                "[Sizer] %s 사이즈 $%.2f > budget_cap $%.2f → 차단",
                symbol, sizing.size_usdt, budget_cap,
            )
            return

        # 6) CostGuard
        pair_tier = self.pair_wl.get_tier(symbol)
        cost_check = self.cost_guard.check(
            setup_tag=quality.setup_tag,
            entry_price=quality.entry_price,
            tp_price=quality.tp,
            sl_price=quality.sl,
            position_usdt=sizing.size_usdt,
            action=quality.action,
            pair_tier=pair_tier,
        )
        if not cost_check.passed:
            logger.info("[CostGuard] %s 차단: %s", symbol, cost_check.reason)
            self.shadow.record_blocked("cost_guard", candidate, cost_check)
            return

        # 7) 실행
        max_lev_tier = RISK_RULES.max_leverage_by_tier.get(pair_tier, 0)
        leverage = max(1, min(params.max_leverage, max_lev_tier))
        decision = {
            "symbol": symbol,
            "action": quality.action,
            "entry_price": quality.entry_price,
            "tp": quality.tp,
            "sl": quality.sl,
            "setup_tag": quality.setup_tag,
            "score": quality.score,
            "size_usdt": sizing.size_usdt,
            "leverage": leverage,
            "regime": regime_state.regime,
            "regime_confidence": regime_state.confidence,
            "cost_guard_ev": cost_check.expected_value,
            "pair_tier": pair_tier,
            "sizing_kelly_raw": sizing.kelly_raw,
            "sizing_pct": sizing.size_pct,
            # v3.1.1: 진입 시점 자본 상태 (부록 E-5-2 / E-7-1)
            "wallet_balance_at_entry": capital_snapshot.wallet_balance,
            "available_at_entry": capital_snapshot.available_balance,
            "locked_margin_at_entry": capital_snapshot.locked_margin,
        }
        result = await self.executor.enter_trade(decision)
        if result.get("success"):
            # v3.1.2: 체결 확인 후에만 추적 시작 + 거래소 보호주문 id 전달
            self.exit_plan.start_tracking(
                result["trade_id"], decision, result["quantity"],
                max_hold_minutes=params.max_hold_minutes,
                sl_order_id=result.get("sl_order_id"),
                tp_order_id=result.get("tp_order_id"),
            )
            self.shadow.record(strategy_name=_SHADOW_STRATEGY, decision=decision)
            sl_note = (
                f" 🛡️SL주문 {result['sl_order_id']}"
                if result.get("sl_order_id") else ""
            )
            await self.telegram.send(
                f"✅ 진입: {symbol} {quality.action} ${sizing.size_usdt:.2f} "
                f"@ {result.get('entry_price_actual', quality.entry_price)} "
                f"(lev {leverage}x, {regime_state.regime}){sl_note}"
            )
        elif result.get("critical"):
            # v3.1.2 critical 사유는 여러 가지 — reason 으로 운영자에게 명확히 전달:
            #   보호주문 실패 강제청산 / 고아 포지션 강제청산(A-5) /
            #   Hedge Mode 차단(A-4) / position mode 조회 실패 등.
            reason = result.get("reason", "")
            logger.critical("[Main] %s CRITICAL 진입 중단/청산: %s", symbol, reason)
            await self.telegram.send(
                f"🚨 CRITICAL: {symbol} 진입 중단 — 거래소 확인 필요 ({reason})"
            )
        else:
            logger.warning("[Main] %s 진입 실패: %s", symbol, result.get("reason"))

    # =================================================================
    # 포지션 관리 / 배치 트리거
    # =================================================================
    async def _manage_open_positions(self) -> None:
        """ExitPlanController 가 추적 중인 포지션의 청산 조건을 평가한다.

        v3.1.2: live 에서는 먼저 reconcile_closed_positions 로 거래소 보호주문
        (STOP/TP)이 폴링 사이에 체결된 포지션을 DB 마감 + 추적 해제한다.
        """
        tracked = self.exit_plan.get_tracked()
        if not tracked:
            return

        # ── 거래소 보호주문 체결분 정합 (요구 7) ──
        if not self.dry_run:
            reconciled = await self.executor.reconcile_closed_positions(tracked)
            for r in reconciled:
                self.exit_plan.stop_tracking(r["symbol"])
                await self.telegram.send(
                    f"📕 거래소 청산 감지: {r['symbol']} "
                    f"(보호주문 체결, exit={r.get('exit_price')})"
                )
            if reconciled:
                tracked = self.exit_plan.get_tracked()
                if not tracked:
                    return

        price_map: dict[str, float] = {}
        for sym in tracked:
            price = await self.collector.get_ticker_price(sym)
            if price is not None:
                price_map[sym] = price
        results = await self.exit_plan.update_all(price_map)
        for r in results:
            if r.get("closed"):
                await self.telegram.send(
                    f"📕 포지션 청산: {r['symbol']} ({', '.join(r['actions'])})"
                )

    async def _maybe_run_weekly(self) -> None:
        """매주 지정 요일/시각에 WeeklyGPTAnalyst 를 1회 실행한다 (§8-8-3)."""
        now = datetime.now(timezone.utc)
        if (now.weekday() == WEEKLY_ANALYST_CONFIG.run_day_of_week
                and now.hour == WEEKLY_ANALYST_CONFIG.run_hour
                and (self._last_weekly_run is None
                     or (now - self._last_weekly_run).days >= 6)):
            try:
                report = await self.weekly_analyst.run(
                    days=WEEKLY_ANALYST_CONFIG.lookback_days
                )
                self._last_weekly_run = now
                perf = report.performance_summary
                await self.telegram.send(
                    f"📊 주간 보고서 ({report.period_days}일)\n"
                    f"승률: {perf.get('win_rate', 0) * 100:.1f}%\n"
                    f"Expectancy: {perf.get('expectancy_R', 0):.3f}R\n"
                    f"GPT 인사이트:\n{report.gpt_insights[:300]}\n"
                    f"제안: {len(report.parameter_suggestions)}건"
                )
            except Exception as e:
                logger.error("[Weekly] 실행 실패: %s", e)

    async def _maybe_refresh_pairs(self, capital_snapshot) -> None:
        """페어 화이트리스트 갱신 주기가 지났으면 refresh 한다 (§8-8-3)."""
        if self.pair_wl.needs_refresh(
            max_age_hours=PAIR_WHITELIST_CONFIG.refresh_interval_hours
        ):
            try:
                self.pair_wl.refresh()
                active = self.pair_wl.get_active(capital_snapshot.wallet_balance)
                await self.telegram.send(
                    f"📋 페어 화이트리스트 갱신: 활성 {len(active)}"
                )
            except Exception as e:
                logger.error("[Main] 페어 갱신 실패: %s", e)

    async def _maybe_send_heartbeat(self) -> None:
        """heartbeat_interval_s 주기로 Telegram 에 봇 생존·상태 요약을 보낸다.

        _iter() 의 성공·실패와 무관하게 _main_loop 에서 매 루프 호출되며,
        마지막 전송 이후 interval 이 지난 경우에만 실제로 발송한다.
        """
        now = datetime.now(timezone.utc)
        if (self._last_heartbeat is not None
                and (now - self._last_heartbeat).total_seconds()
                < self._heartbeat_interval_s):
            return

        mode = "DRY-RUN" if self.dry_run else "LIVE"
        if getattr(self.collector, "use_testnet", False):
            mode += "/TESTNET"
        uptime = now - self._started_at if self._started_at else timedelta(0)
        regime = (
            self._current_regime_state.regime
            if self._current_regime_state is not None
            else "N/A"
        )
        open_count = len(self.exit_plan.get_tracked())

        try:
            snap = await self.capital_manager.get_snapshot()
            balance_str = (
                f"wallet ${snap.wallet_balance:.2f} / "
                f"available ${snap.available_balance:.2f}"
            )
        except Exception as e:  # noqa: BLE001 — 하트비트는 자본 조회 실패해도 발송
            logger.warning("[Heartbeat] 자본 조회 실패: %s", e)
            balance_str = "조회 실패"

        await self.telegram.send(
            f"💓 하트비트 ({mode})\n"
            f"가동 시간: {self._format_timedelta(uptime)}\n"
            f"루프 횟수: {self._loop_count}\n"
            f"레짐: {regime}\n"
            f"미청산 포지션: {open_count}\n"
            f"잔고: {balance_str}\n"
            f"직전 스캔 후보: {self._last_candidate_count}"
        )
        self._last_heartbeat = now
        logger.info(
            "[Heartbeat] 상태 전송 (uptime=%s, loop=%d)",
            self._format_timedelta(uptime), self._loop_count,
        )

    @staticmethod
    def _format_timedelta(td: timedelta) -> str:
        """timedelta 를 'Hh Mm Ss' 문자열로 변환한다."""
        total = int(td.total_seconds())
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}h {minutes}m {seconds}s"

    async def _live_account_preflight(self) -> None:
        """live 시작 전 기존 계좌 상태 점검 (Protected Existing Position Coexist Mode).

        기존 포지션/Open Orders/Open Algo Orders 를 read-only 로 조회해 분류한다.
          - 비보호종목 항목이 하나라도 있으면 시작 차단(RuntimeError, fail-closed).
          - 모두 보호종목이면 공존 허용 — "봇 비접근" 경고 후 진행.
          - 조회 실패 시 fail-closed(시작 차단).
        보호종목 기존 보유분은 봇이 절대 청산/취소하지 않으며 bot_live.db 에
        봇 포지션으로 기록하지도 않는다(거래소 조회만, INSERT 없음).
        """
        protected = set(self.pair_wl.protected_symbols)
        try:
            pos_syms = await self.executor.preflight_open_position_symbols()
            order_syms = await self.executor.preflight_open_order_symbols()
            algo_syms = await self.executor.preflight_open_algo_symbols()
        except Exception as e:
            msg = f"[Preflight] 기존 계좌 상태 조회 실패 → 시작 차단(fail-closed): {e}"
            logger.critical(msg)
            await self.telegram.send("🛑 " + msg)
            raise RuntimeError("live preflight query failed") from e

        existing = (
            [("position", s) for s in pos_syms]
            + [("order", s) for s in order_syms]
            + [("algo", s) for s in algo_syms]
        )
        non_protected = [(k, s) for (k, s) in existing if s not in protected]
        if non_protected:
            detail = ", ".join(f"{k}:{s}" for k, s in non_protected)
            msg = f"[Preflight] 비보호종목 기존 항목 존재 → 시작 차단: {detail}"
            logger.critical(msg)
            await self.telegram.send("🛑 " + msg)
            raise RuntimeError(f"non-protected existing items: {detail}")

        if existing:
            detail = ", ".join(f"{k}:{s}" for k, s in existing)
            logger.warning(
                "[Preflight] 기존 보호종목 보유분 공존(봇 비접근): %s", detail
            )
            await self.telegram.send(
                "🛡️ 기존 보호종목 보유분 공존 — 봇이 절대 건드리지 않습니다:\n" + detail
            )
        else:
            logger.info("[Preflight] 기존 포지션/주문/algo 없음 — 깨끗한 계좌")

    async def _close_all_positions_safe(self) -> None:
        """critical 상황 — 열린 포지션을 안전 청산한다 (§8-8-3).

        Protected Existing Position Coexist Mode: 보호종목 기존 보유분은 emergency
        close 대상에서 제외한다(봇이 연 비보호종목 포지션만 청산).
        """
        positions = await self.executor.get_open_positions()
        for pos in positions:
            if pos.symbol in self.pair_wl.protected_symbols:
                logger.info(
                    "[Safe Close] %s 보호종목 — 청산 제외(기존 보유분 보존)", pos.symbol
                )
                continue
            try:
                await self.executor.close_position(
                    pos.symbol, reason="system_critical"
                )
                self.exit_plan.stop_tracking(pos.symbol)
                logger.info("[Safe Close] %s 청산", pos.symbol)
            except Exception as e:
                logger.error("[Safe Close] %s 실패: %s", pos.symbol, e)

    def _db_log_regime_change(self, rs) -> None:
        """레짐 전환을 regime_history 테이블에 기록한다 (§13-2)."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO regime_history (
                        timestamp, symbol, regime, prev_regime, confidence,
                        adx_4h, atr_ratio_1h, atr_ratio_4h, bb_width_pct,
                        ema_slope, funding_rate, reasons, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto')
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        _CONTEXT_SYMBOL,
                        rs.regime, rs.prev_regime, rs.confidence,
                        rs.adx_4h, rs.atr_ratio_1h, rs.atr_ratio_4h,
                        rs.bb_width_pct, rs.ema_slope, rs.funding_rate,
                        json.dumps(rs.reasons, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[Main] regime_history 기록 실패: %s", e)

    # =================================================================
    # WebSocket 콜백 (§8-8-4)
    # =================================================================
    def _on_ws_kline(self, msg) -> None:
        """WS kline 수신 — health monitor 에 기록."""
        self.health.record_ws_kline_received()

    def _on_ws_user_data(self, msg) -> None:
        """WS userData 수신 — health monitor 에 기록."""
        self.health.record_ws_user_received()

    def _on_rest_call_complete(self, latency_ms: float, success: bool) -> None:
        """REST 호출 완료 — health monitor 에 기록."""
        self.health.record_rest_call(latency_ms, success)

    # =================================================================
    # 종료
    # =================================================================
    async def shutdown(self) -> None:
        """봇 종료 — 메인 루프 정지 + WS 정지 + 종료 알림."""
        self._running = False
        try:
            self.collector.stop_ws()
        except Exception as e:
            logger.warning("[Shutdown] WS 정지 실패: %s", e)
        await self.telegram.send("🛑 봇 종료")
        logger.info("[Shutdown] 봇 종료 완료")


# =====================================================================
# CLI 진입점
# =====================================================================
def _parse_args() -> argparse.Namespace:
    """CLI 인자 파싱 — --dry-run / --duration."""
    parser = argparse.ArgumentParser(
        description="Binance Futures Bot v3.1.1 메인 봇"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="거래소 주문 없이 DB 기록만 수행 (페이퍼)",
    )
    parser.add_argument(
        "--duration", type=int, default=0,
        help="실행 시간(초). 0이면 무한 실행 (기본값).",
    )
    return parser.parse_args()


def _handle_termination_signal(signum, frame) -> None:
    """종료 시그널(SIGTERM 등)을 KeyboardInterrupt 로 변환해 graceful shutdown 유도.

    SIGINT(Ctrl+C)는 파이썬이 기본으로 KeyboardInterrupt 를 발생시키므로 이
    핸들러는 주로 SIGTERM 대응이다 — Unix 의 `kill <pid>` / systemd stop /
    `docker stop` 은 SIGTERM 을 보낸다. KeyboardInterrupt 는 _amain 의 finally
    (shutdown) 와 main 의 except 를 거쳐 깔끔하게 종료된다.

    Windows 네이티브에서는 SIGTERM 이 즉시 종료(TerminateProcess)라 핸들러
    실행이 보장되지 않지만, 등록 자체는 안전하다 (운영 배포 환경 Linux 기준).
    """
    logger.info("[Main] 종료 시그널 수신 (signum=%s) — graceful shutdown", signum)
    raise KeyboardInterrupt()


async def _amain(args: argparse.Namespace) -> None:
    """비동기 메인 — duration 지정 시 wait_for 로 종료.

    시작 시퀀스/메인 루프에서 발생한 예외는 raw traceback 대신 한 줄 에러
    로그로 정리하고 항상 shutdown() 을 거쳐 깔끔하게 종료한다.
    """
    bot = MainBot(dry_run=args.dry_run)
    try:
        if args.duration > 0:
            try:
                await asyncio.wait_for(bot.start(), timeout=args.duration)
            except asyncio.TimeoutError:
                logger.info("[Main] duration %d초 경과 — 종료", args.duration)
        else:
            await bot.start()
    except Exception as e:  # noqa: BLE001 — 시작 실패는 traceback 없이 정리
        logger.error("[Main] 봇 실행 중단: %s: %s", type(e).__name__, e)
    finally:
        await bot.shutdown()


def _suppress_http_client_logs() -> None:
    """HTTP 클라이언트 라이브러리의 INFO 로그를 억제한다(보안).

    Telegram Bot API 토큰은 URL(https://api.telegram.org/bot<TOKEN>/...)에
    포함되므로, httpx/httpcore 가 요청 URL 을 INFO 로 찍으면 토큰이 콘솔/파일
    로그에 평문 노출된다. 두 로거를 WARNING 이상으로 올려 URL INFO 로그를
    억제한다. 알림 전송 기능 자체는 영향 없음(전송은 그대로 동작).
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    """동기 진입점 — .env 로드 → 시그널 핸들러 등록 → 로깅 설정 → 비동기 메인 실행."""
    # .env 의 BINANCE_API_KEY / OPENAI_API_KEY / TELEGRAM_* 등을 os.environ 으로
    # 로드한다. 이 호출이 없으면 collector / capital_manager 가 키를 찾지 못한다.
    if load_dotenv is not None:
        load_dotenv()
    else:
        logging.getLogger(__name__).warning(
            "[Main] python-dotenv 미설치 — .env 자동 로드 불가 "
            "(환경변수를 직접 export 하거나 python-dotenv 설치 필요)"
        )

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _suppress_http_client_logs()

    # SIGTERM → graceful shutdown (운영 배포 시 kill/systemd/docker stop 대응).
    # SIGINT(Ctrl+C)은 파이썬 기본 KeyboardInterrupt 로 이미 처리된다.
    try:
        signal.signal(signal.SIGTERM, _handle_termination_signal)
    except (ValueError, OSError, AttributeError) as e:
        # 메인 스레드가 아니거나 플랫폼 미지원 — --duration / Ctrl+C 로 대체.
        logger.debug("[Main] SIGTERM 핸들러 등록 불가: %s", e)

    args = _parse_args()
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        logger.info("[Main] 종료 신호(KeyboardInterrupt/SIGTERM) — 종료")


if __name__ == "__main__":
    main()
