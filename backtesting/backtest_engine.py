"""
backtesting/backtest_engine.py
=====================================================================
BacktestEngine — 시간순 이벤트 루프 기반 백테스트 엔진 (§10-2, §10-5)

Phase 0 검증의 핵심. 단 한 줄의 룩어헤드(look-ahead) 버그도 백테스트
전체를 무효화하므로 다음 5단계 차단을 절대 원칙으로 한다 (§10-1 원칙 ⑤):

  1. _get_candles_until() 은 df.index < ts (strict less-than) 만 반환.
     현재 봉(ts)은 마감 전이므로 절대 제외.
  2. 레짐 감지·시그널 평가는 _get_candles_until 결과(마감봉)만 입력으로 받는다.
     ATR 도 마감봉으로만 계산.
  3. 진입 체결가 = 봉 ts 의 open 만 사용 (봉 시작 시점에 알 수 있는 값).
     봉 ts 의 high/low/close 는 시그널 평가에 절대 미사용.
  4. TP/SL 도달 체크는 진입봉 ts 부터 순방향 스캔 (실제 가격 진행).
     pandas.shift(-1) 등 미래 참조 일절 없음. 한 봉 내 TP·SL 동시 충족 시
     SL 우선 (보수적 가정).
  5. 펀딩비는 보유 중인 각 봉의 funding_rate 만 반영 (발표·실현 시점 이후).

비용 적용 (§10-1 원칙 ①, §3 비용 명세):
  진입 maker_fee + 청산 taker_fee + 양측 slippage_by_tier + 펀딩비.

기존 모듈 통합:
  RegimeDetector(§8-1) / CostGuard(§8-2) / DynamicPositionSizer(§8-3).

명세서: docs/SPEC_v3.1.md §10-2, §10-5
=====================================================================

세션 11 정정 (2건, 사용자 승인 — 세션 11):
  ① §10-2 BacktestConfig.start_date / end_date 는 default 없는 required 필드이나,
     세션 11 지시("BacktestConfig 기본값 사용")를 만족하려면 BacktestConfig() 가
     인자 없이 생성 가능해야 한다. §10-3 예시 기간(2022-01-01 ~ 2026-04-30)을
     기본값으로 부여했다. run() 은 candles_by_pair 만 사용하므로 무해하며,
     start/end_date 는 walk_forward 윈도우 생성에만 쓰인다.
  ② §10-5 골격은 _get_candles_until(candles, tf, ts) 시그니처와
     walk_forward / _generate_windows 를 BacktestEngine 메서드로 둔다. 세션 11
     설계 지시에 따라 _get_candles_until(symbol, tf, ts) 로 조정하고,
     walk_forward / _generate_windows 는 backtesting/walk_forward.py 모듈
     함수로 분리했다 (순환 import 회피 + 단일 책임).
  자세한 내용: docs/CORRECTIONS_v3.1.2.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from sizing.dynamic_sizer import DynamicPositionSizer
from strategy.breakout import BreakoutConfig, ema, evaluate_breakout
from strategy.smc import SMCConfig, evaluate_smc
from strategy.cost_guard import CostGuard
from strategy.regime_detector import Regime, RegimeDetector

logger = logging.getLogger(__name__)


# ── 모듈 상수 ──
ATR_PERIOD = 14                       # ATR 계산 기간 (봉)
TP_ATR_MULT = 2.0                     # TP = 진입 ± 2 · ATR
SL_ATR_MULT = 1.0                     # SL = 진입 ∓ 1 · ATR
DEFAULT_TIME_STOP_BARS = 24           # 시간 스톱: 최대 보유 봉 수
SECONDS_PER_YEAR = 365.0 * 24 * 3600  # 연환산 / 샤프 비율용
FUNDING_INTERVAL_SECONDS = 8 * 3600   # 펀딩비 정산 주기 (8시간)
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

# 페어별 슬리피지 Tier (그 외는 기본 2)
PAIR_TIERS: Dict[str, int] = {"BTCUSDT": 1, "ETHUSDT": 1}

# 보호종목 — 백테스트 유니버스에서 항상 제외 (CLAUDE.md TIER 1). BTCUSDT 는
# 매크로 게이트(200EMA) 입력으로 데이터만 쓰고 거래 대상에선 빠진다.
_PROTECTED_SYMBOLS = frozenset(
    {"BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"}
)

# 백테스트 사이징용 보수적 사전값 (실측 데이터 없음 — §8-3 default 정책과 정합)
_DEFAULT_BACKTEST_WIN_RATE = 0.45
_DEFAULT_BACKTEST_WIN_R = 2.0
_DEFAULT_BACKTEST_LOSS_R = 1.0


# ─────────────────────────────────────────────────────
# 데이터 클래스 (§10-2)
# ─────────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    """백테스트 설정. 필드 구성은 명세서 §10-2 동일.

    세션 11 정정 ①: start_date / end_date 에 §10-3 예시 기간을 기본값으로 부여
    (§10-2 원문은 default 없는 required 필드). 자세한 내용은 모듈 docstring 참조.
    """

    start_date: datetime = field(
        default_factory=lambda: datetime(2022, 1, 1, tzinfo=timezone.utc)
    )
    end_date: datetime = field(
        default_factory=lambda: datetime(2026, 4, 30, tzinfo=timezone.utc)
    )
    initial_capital: float = 1000.0
    pairs: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])

    # 전략 선택: "trend_follow"(레짐 추세추종, 기존) / "oi_surge"(라이브 OIScanner 재현)
    #          / "breakout"(Donchian/ATR 돌파 + ADX·200EMA 레짐 게이트, P1)
    strategy: str = "trend_follow"
    # oi_surge 전용 — 라이브 OIScanner 와 동일 의미. open_interest 컬럼 필요.
    oi_change_threshold_pct: float = 5.0       # OI 변화율 하한(%)
    price_change_threshold_pct: float = 2.0    # 가격 변화율 하한(절댓값, %)
    oi_lookback_bars: int = 1                  # 현재 vs N봉 전 OI/가격 비교

    # breakout 전용 — strategy/breakout.BreakoutConfig 로 매핑(단일 소스 공유).
    breakout_donchian: int = 20                # 진입 채널 기간
    breakout_adx_min: float = 25.0             # 추세 게이트(ADX 하한)
    breakout_ema: int = 200                    # 장기 추세 편향 EMA
    breakout_atr_stop: float = 2.0             # 손절 = 진입 ∓ N·ATR
    breakout_atr_target: float = 4.0           # 목표 = 진입 ± N·ATR

    # 돌파 청산 정교화(opt-in, 기본 off → 기존 TP/SL/시간스톱 동작 불변).
    # True 면 고정 TP 대신 ATR 샹들리에 트레일(수익을 끝까지 끌되 되돌림에 청산).
    breakout_trail_exit: bool = False
    breakout_trail_atr_mult: float = 3.0       # 트레일 = 최고가 − N·ATR (LONG)

    # 최대 보유 봉 수(시간 스톱). 돌파처럼 추세추종은 길게 끌 수 있어 설정화.
    time_stop_bars: int = DEFAULT_TIME_STOP_BARS

    # 비용 가정 (보수적, §10-2)
    maker_fee: float = 0.00018
    taker_fee: float = 0.00045
    slippage_tier_1: float = 0.00050
    slippage_tier_2: float = 0.00100
    slippage_tier_3: float = 0.00150

    # B-6 [B-3]: 진입 체결 모델 — 라이브(GTX post-only)와 백테스트를 같은 게임으로.
    #   "post_only" (기본·라이브 일치): 신호봉 종가(close[ts-1])에 resting LIMIT.
    #       진입봉 ts 가 그 가격에 닿으면(롱 low≤limit / 숏 high≥limit) limit 에 체결
    #       (maker, 무슬리피지). 안 닿으면 미체결→스킵. ⚠ 봉≫15초라 체결률 *상한*
    #       추정(방향 판단용, 정밀 캘리브레이션은 B-8 testnet).
    #   "taker": 봉 ts open 확정 체결 + taker 수수료 + 양측 슬리피지 (D-1 프리뷰).
    #   "maker_open": 기존 가정(봉 ts open 확정 체결 + maker 진입). 비교 보존용 (legacy).
    entry_fill_model: str = "post_only"

    # ── Phase C 후보: "개선된 돌파" 게이트 (모두 기본 off → 기존 동작 불변) ──
    # 사전확정 단일값(스윕 금지). breakout 전략에만 적용.
    long_only: bool = False                    # True: SHORT 신호 배제(롱 전용)
    volume_confirm_mult: float = 0.0           # >0: 돌파봉 vol > mult×직전 N봉 평균 이어야 진입
    volume_confirm_bars: int = 20              # 거래량 평균 lookback
    macro_btc_ema_period: int = 0              # >0: BTC close[<ts] > BTC EMA 일 때만 신규 롱(risk-on)
    min_trailing_volume_usd: float = 0.0       # >0: 직전 N봉 평균 거래대금 미만 종목 제외
    trailing_volume_bars: int = 30             # 거래대금 랭킹 lookback
    # risk-based 사이징: >0 이면 Kelly 대신 *계좌 risk* 고정.
    # notional = risk_per_trade_pct × equity × entry / |entry−stop|  (stop 도달=이 risk 손실)
    risk_per_trade_pct: float = 0.0            # 0.005 = 코인당 0.5% 계좌 risk (A1 정합)
    max_concurrent_positions: int = 0          # >0: 동시 오픈 포지션 cap(없으면 하루 1회 기존)

    # ── SMC 후보 (strategy=="smc" 일 때만). 사전확정 단일값(스윕 금지) ──
    smc_swing_n: int = 2                        # fractal 좌우 봉수(5봉), i+N 이후 확정
    smc_fvg_atr_mult: float = 0.25             # 최소 FVG 갭 ≥ 0.25·ATR
    smc_sweep_lookback: int = 3                # 직전 K봉 내 liquidity sweep
    smc_poi_lookback: int = 10                 # FVG/OB(point-of-interest) 신선도 윈도우
    smc_atr_period: int = 14
    smc_sl_atr_buffer: float = 0.1             # SL = sweep 극값 ∓ 0.1·ATR

    # 백테스트 시 펀딩비 적용 여부
    apply_funding: bool = True

    # Walk-Forward 설정
    in_sample_months: int = 12
    out_sample_months: int = 3
    step_months: int = 1

    def slippage_for_tier(self, tier: int) -> float:
        """Tier(1/2/3)별 편도 슬리피지 반환. 미등록 tier 는 가장 보수적인 tier 3."""
        return {
            1: self.slippage_tier_1,
            2: self.slippage_tier_2,
            3: self.slippage_tier_3,
        }.get(tier, self.slippage_tier_3)


@dataclass
class BacktestResult:
    """백테스트 집계 결과. 필드 구성은 명세서 §10-2 동일."""

    config: BacktestConfig
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    avg_win_R: float
    avg_loss_R: float
    expectancy_R: float
    total_return_pct: float
    annual_return_pct: float

    # 레짐별 성과 — 거래 0건인 봉도 bars 카운트로 포함된다.
    regime_stats: Dict[str, Dict[str, float]]

    # 셋업별 성과
    setup_stats: Dict[str, Dict[str, float]]

    # 시계열
    equity_curve: List[Tuple[datetime, float]]
    drawdown_curve: List[Tuple[datetime, float]]

    # 개별 거래 (거래당 gross/cost/net edge·분포 분석용). 기본 빈 리스트.
    trades: List["Trade"] = field(default_factory=list)


@dataclass
class Signal:
    """_evaluate_signal() 이 반환하는 진입 신호 1건.

    entry_price 는 진입봉 ts 의 open (룩어헤드 차단 단계 3).
    tp_price / sl_price 는 마감봉 ATR 기반.
    """

    symbol: str
    action: str                  # "LONG" | "SHORT"
    setup_tag: str
    regime: str
    confidence: float
    entry_ts: Any                # 진입봉 timestamp (DataFrame index 값)
    entry_price: float           # 진입봉 open
    tp_price: float
    sl_price: float
    atr: float
    pair_tier: int
    size_usdt: float             # 명목 가치 (DynamicPositionSizer 산출)


@dataclass
class Trade:
    """_simulate_trade() 가 반환하는 체결·청산 완료 거래 1건."""

    symbol: str
    setup_tag: str
    regime: str
    action: str
    entry_ts: Any
    exit_ts: Any
    entry_price: float
    exit_price: float
    tp_price: float
    sl_price: float
    size_usdt: float
    risk_usdt: float             # 계획 손실폭 (entry→SL) × notional
    gross_pnl_usd: float         # 가격 변동 손익 (비용 차감 전)
    fees_usd: float              # 수수료 + 슬리피지
    funding_usd: float           # 순 펀딩비 (양수=순지불, 음수=순수령)
    pnl_usd: float               # 최종 순손익
    pnl_R: float                 # pnl_usd / risk_usdt
    exit_reason: str             # "TP" | "SL" | "TIME_STOP" | "DATA_END"
    bars_held: int


# ─────────────────────────────────────────────────────
# 백테스트 엔진 (§10-5)
# ─────────────────────────────────────────────────────
class BacktestEngine:
    """시간순 이벤트 루프 백테스트 엔진.

    사용:
        config = BacktestConfig(pairs=["BTCUSDT"])
        engine = BacktestEngine(config)
        result = engine.run({"BTCUSDT": df})   # df: index=timestamp, OHLCV(+funding_rate)

    전략 로직 (§10-5 골격은 _evaluate_signal 본문을 비워 둠 → 보수적 추세추종으로
    구현): TREND_UP → LONG, TREND_DOWN → SHORT, 그 외(RANGING/UNCERTAIN/HIGH_VOL)
    → 무거래. 하루 1회 · 심볼별 중복 진입 금지.
    """

    def __init__(self, config: BacktestConfig) -> None:
        """엔진 초기화. RegimeDetector / CostGuard / DynamicPositionSizer 생성."""
        self.config = config
        self.regime_detector = RegimeDetector()
        self.cost_guard = CostGuard(
            taker_fee_rate=config.taker_fee,
            maker_fee_rate=config.maker_fee,
        )
        self.sizer = DynamicPositionSizer()
        # 돌파 전략 파라미터(라이브와 동일 로직 공유). stateless 라 _reset 불필요.
        self.breakout_cfg = BreakoutConfig(
            donchian_entry=config.breakout_donchian,
            adx_trend_min=config.breakout_adx_min,
            ema_period=config.breakout_ema,
            atr_stop_mult=config.breakout_atr_stop,
            atr_target_mult=config.breakout_atr_target,
        )
        # SMC 파라미터(strategy=="smc"). stateless 라 _reset 불필요.
        self.smc_cfg = SMCConfig(
            swing_n=config.smc_swing_n,
            fvg_atr_mult=config.smc_fvg_atr_mult,
            sweep_lookback=config.smc_sweep_lookback,
            poi_lookback=config.smc_poi_lookback,
            atr_period=config.smc_atr_period,
            sl_atr_buffer=config.smc_sl_atr_buffer,
        )

        # run() 내부 상태 (run() 진입 시 _reset 로 초기화)
        self._candles_by_pair: Dict[str, pd.DataFrame] = {}
        self._regime_ref: str = "BTCUSDT"
        self._bar_seconds: float = 86400.0

    def _reset(self) -> None:
        """run() 재호출 시 모듈 상태를 새로 만들어 멱등성 보장.

        RegimeDetector 는 streak / _confirmed_regime 등 내부 상태를 누적하므로
        같은 엔진으로 run() 을 두 번 호출하면 두 번째가 오염된다.
        """
        self.regime_detector = RegimeDetector()
        self.cost_guard = CostGuard(
            taker_fee_rate=self.config.taker_fee,
            maker_fee_rate=self.config.maker_fee,
        )
        self.sizer = DynamicPositionSizer()

    # ── 메인 실행 루프 ──
    def run(self, candles_by_pair: Dict[str, pd.DataFrame]) -> BacktestResult:
        """전체 백테스트 실행 (§10-5).

        Args:
            candles_by_pair: {symbol: DataFrame}. DataFrame 은 index=timestamp
                (UTC tz-aware 권장), columns=[open, high, low, close, volume,
                funding_rate(옵션)].

        Returns:
            BacktestResult: 집계 통계 + 시계열.

        Raises:
            ValueError: candles_by_pair 가 비었거나 필수 컬럼이 누락된 경우.
        """
        if not candles_by_pair:
            raise ValueError("candles_by_pair 가 비어 있습니다.")

        self._reset()
        self._candles_by_pair = {
            sym: self._normalize_df(sym, df) for sym, df in candles_by_pair.items()
        }

        # 레짐 기준 페어: BTCUSDT 우선 (§10-5 골격과 동일), 없으면 첫 페어.
        self._regime_ref = (
            "BTCUSDT" if "BTCUSDT" in self._candles_by_pair
            else next(iter(self._candles_by_pair))
        )
        self._bar_seconds = self._infer_bar_seconds(
            self._candles_by_pair[self._regime_ref]
        )

        # 거래 가능 페어: config.pairs ∩ 입력 페어
        tradable = [p for p in self.config.pairs if p in self._candles_by_pair]
        if not tradable:
            logger.warning(
                "[Backtest] config.pairs %s 중 입력 데이터에 존재하는 페어 없음 — "
                "무거래 결과 반환", self.config.pairs,
            )

        equity = self.config.initial_capital
        equity_curve: List[Tuple[datetime, float]] = []
        trades: List[Trade] = []
        regime_bar_count: Dict[str, int] = {}

        # 심볼별 중복 진입 차단 ts (직전 거래 청산 ts 까지 진입 금지)
        blocked_until: Dict[str, Optional[Any]] = {p: None for p in tradable}
        # 하루 1회 한도 카운터
        trades_by_day: Dict[date, int] = {}
        # Phase C: 동시 오픈 포지션 exit_ts 추적 (max_concurrent_positions cap 용)
        open_exits: List[Any] = []

        for ts in self._iter_timestamps():
            # 만기 도래분 정리(동시보유 카운트용) — exit_ts ≤ ts 면 청산됨
            if self.config.max_concurrent_positions > 0:
                open_exits = [e for e in open_exits if e > ts]
            # ── 1. 룩어헤드 차단 슬라이스 (단계 1·2) ──
            ref_candles = self._get_candles_until(self._regime_ref, "4h", ts)
            funding = self._get_funding_at(self._regime_ref, ts)

            # ── 2. 레짐 감지 ──
            # 본 엔진은 페어별 단일 DataFrame 을 쓰므로 §10-5 골격과 동일하게
            # 4h / 1h 자리에 동일 슬라이스를 전달한다.
            regime_state = self.regime_detector.detect(
                ref_candles, ref_candles, funding,
            )
            regime_bar_count[regime_state.regime] = (
                regime_bar_count.get(regime_state.regime, 0) + 1
            )

            if regime_state.regime == Regime.HIGH_VOL:
                # HIGH_VOL: 진입 차단, 자본만 기록 (§10-5)
                equity_curve.append(
                    (self._to_dt(ts), self._mark_to_market(equity, [], ts))
                )
                continue

            # ── 3. 각 페어 시그널 평가 ──
            day = self._day_key(ts)
            for symbol in tradable:
                # Phase C: 동적 mid-liquidity 유니버스 (off 면 전체 통과)
                if (self.config.min_trailing_volume_usd > 0
                        and not self._passes_universe_filter(symbol, ts)):
                    continue
                # 심볼별 중복 진입 차단
                if blocked_until[symbol] is not None and ts <= blocked_until[symbol]:
                    continue
                # 진입 한도: max_concurrent_positions>0 면 동시보유 cap, 아니면 하루 1회(기존)
                if self.config.max_concurrent_positions > 0:
                    if len(open_exits) >= self.config.max_concurrent_positions:
                        break
                elif trades_by_day.get(day, 0) >= 1:
                    break

                signal = self._evaluate_signal(symbol, ts, regime_state, equity)
                if signal is None:
                    continue

                # ── 4. CostGuard — 비용 차감 기대값 게이트 (§8-2) ──
                cost_check = self.cost_guard.check(
                    setup_tag=signal.setup_tag,
                    entry_price=signal.entry_price,
                    tp_price=signal.tp_price,
                    sl_price=signal.sl_price,
                    position_usdt=signal.size_usdt,
                    action=signal.action,
                    pair_tier=signal.pair_tier,
                )
                if not cost_check.passed:
                    continue

                # ── 5. 가상 진입·청산 시뮬레이션 ──
                trade = self._simulate_trade(signal)
                if trade is None:
                    continue

                trades.append(trade)
                equity += trade.pnl_usd
                blocked_until[symbol] = trade.exit_ts
                trades_by_day[day] = trades_by_day.get(day, 0) + 1
                open_exits.append(trade.exit_ts)   # Phase C 동시보유 카운트

            equity_curve.append(
                (self._to_dt(ts), self._mark_to_market(equity, [], ts))
            )

        return self._compute_result(trades, equity_curve, regime_bar_count)

    # ── 룩어헤드 차단 핵심 (단계 1) ──
    def _get_candles_until(self, symbol: str, tf: str, ts: Any) -> List[tuple]:
        """ts 이전(strict less-than) 마감봉만 (o,h,l,c,v,timestamp) 튜플로 반환.

        룩어헤드 차단의 1차 방어선. 현재 봉(ts)은 아직 마감되지 않았으므로
        절대 포함하지 않는다.

        Args:
            symbol: 페어 심볼.
            tf: 타임프레임 라벨. §10-5 골격 시그니처 유지용 — 본 엔진은 페어별
                단일 DataFrame 을 쓰므로 "4h"/"1h" 모두 동일 슬라이스를 반환한다.
            ts: 현재(평가) 시점 timestamp. 이 값 미만의 봉만 반환.

        Returns:
            (open, high, low, close, volume, timestamp) 튜플 리스트 (시간 오름차순).
            데이터가 없으면 빈 리스트.
        """
        df = self._candles_by_pair.get(symbol)
        if df is None or len(df) == 0:
            return []
        # 핵심: strict less-than. ts 봉 제외.
        sub = df[df.index < ts]
        return [
            (
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                float(r.volume),
                r.Index,
            )
            for r in sub.itertuples(index=True)
        ]

    def _get_funding_at(self, symbol: str, ts: Any) -> float:
        """ts 이전 가장 최근 마감봉의 funding_rate 반환 (발표·실현 시점 이후).

        룩어헤드 차단 단계 5. funding_rate 컬럼이 없으면 0.0.
        """
        df = self._candles_by_pair.get(symbol)
        if df is None or "funding_rate" not in df.columns:
            return 0.0
        sub = df.loc[df.index < ts, "funding_rate"]
        if len(sub) == 0:
            return 0.0
        return float(sub.iloc[-1])

    # ── 시그널 평가 (단계 2·3) ──
    def _resolve_entry_fill(
        self, symbol: str, ts: Any, action: str,
    ) -> Tuple[bool, Optional[float]]:
        """B-6 [B-3]: 진입 체결 모델에 따라 (체결여부, 체결가)를 반환한다.

        - maker_open/taker: 봉 ts 의 open 에 확정 체결 (체결률 100%).
        - post_only: 신호봉 종가(직전 마감봉 close = close[ts-1] = 라이브 주문가)에
          resting LIMIT. 진입봉 ts 가 그 가격에 닿으면(롱 low≤limit / 숏 high≥limit)
          limit 에 체결, 안 닿으면 미체결(False)→스킵. ⚠ 봉(1d/1h)≫라이브 15초라
          봉 내 저점이 거의 항상 limit 을 터치 → 체결률 *상한*(over-fill) 추정.
        - post_only_strict: *하한* — 시초가(open[ts])가 limit 너머로 갭하면 미체결
          (롱 open>limit / 숏 open<limit). 갭으로 달아난 진입을 더 보수적으로 배제.
          ⚠ 연속(24/7) 크립토는 open[ts]≈close[ts-1] 라 상·하한 모두 봉 granularity
          한계가 있다. 정밀 체결률은 B-8(testnet 실체결)에서만 측정 가능.
        """
        df = self._candles_by_pair[symbol]
        if ts not in df.index:
            return False, None
        model = self.config.entry_fill_model
        if model in ("maker_open", "taker"):
            ep = float(df.at[ts, "open"])
            return (ep > 0), (ep if ep > 0 else None)
        # post_only / post_only_strict — 직전 마감봉 종가에 resting LIMIT
        pos = df.index.get_loc(ts)
        if pos <= 0:                       # 직전 마감봉 없음 → 주문가 미정 → 스킵
            return False, None
        limit = float(df.iloc[pos - 1]["close"])   # close[ts-1]
        if limit <= 0:
            return False, None
        if model == "post_only_strict":
            # 하한: 시초가가 limit 너머로 갭하면 미체결(달리는 진입 배제)
            op = float(df.at[ts, "open"])
            filled = (op <= limit) if action == "LONG" else (op >= limit)
        elif action == "LONG":
            filled = float(df.at[ts, "low"]) <= limit
        else:  # SHORT
            filled = float(df.at[ts, "high"]) >= limit
        return (filled, limit if filled else None)

    def _btc_risk_on(self, ts: Any) -> bool:
        """BTC 매크로 게이트 — BTC 직전 마감 close > BTC EMA(≤직전봉) 면 risk-on.

        룩어헤드 0(`index < ts` 만). BTC 데이터/표본 부족 시 보수적으로 False(진입 금지).
        """
        period = self.config.macro_btc_ema_period
        btc = self._candles_by_pair.get("BTCUSDT")
        if btc is None or period <= 0:
            return False
        closes = btc.loc[btc.index < ts, "close"]
        if len(closes) < period + 1:
            return False
        e = ema([float(x) for x in closes.values], period)
        if e is None:
            return False
        return float(closes.iloc[-1]) > e

    def _passes_volume_confirm(self, closed: list) -> bool:
        """거래량 동반 돌파 — 돌파봉(마지막 마감봉) vol > mult × 직전 N봉 평균.

        config.volume_confirm_mult ≤ 0 이면 항상 통과(off). 표본 부족 시 False.
        closed 는 (o,h,l,c,v,ts) 튜플 리스트 → 거래량 index 4.
        """
        mult = self.config.volume_confirm_mult
        if mult <= 0:
            return True
        n = self.config.volume_confirm_bars
        if len(closed) < n + 1:
            return False
        vols = [float(c[4]) for c in closed]
        avg_prior = sum(vols[-(n + 1):-1]) / n
        if avg_prior <= 0:
            return False
        return vols[-1] > mult * avg_prior

    def _passes_universe_filter(self, symbol: str, ts: Any) -> bool:
        """동적 mid-liquidity 유니버스 — 보호종목 제외 + 직전 N봉 평균 거래대금 하한.

        거래대금 = close×volume, `index < ts` 만(룩어헤드 0).
        config.min_trailing_volume_usd ≤ 0 이면 보호종목 제외만 적용.
        """
        if symbol in _PROTECTED_SYMBOLS:
            return False
        thr = self.config.min_trailing_volume_usd
        if thr <= 0:
            return True
        df = self._candles_by_pair.get(symbol)
        if df is None:
            return False
        prior = df.loc[df.index < ts]
        n = self.config.trailing_volume_bars
        if len(prior) < n:
            return False
        window = prior.iloc[-n:]
        avg_qv = float((window["close"] * window["volume"]).mean())
        return avg_qv >= thr

    def _size_position(
        self, equity: float, entry_price: float, sl_price: float, regime_state,
    ) -> float:
        """포지션 *명목(notional, USDT)* 산출.

        config.risk_per_trade_pct > 0 면 *계좌 risk* 고정:
            notional = risk_pct × equity × entry / |entry−sl|
        → stop(=sl) 도달 시 손실이 정확히 risk_pct×equity (A1 정합). 0 이면 Kelly(기존).
        주의: 반환값은 risk 가 아니라 **명목 크기**다(혼동 금지).
        """
        if self.config.risk_per_trade_pct > 0:
            stop_dist = abs(entry_price - sl_price)
            if stop_dist <= 0 or entry_price <= 0:
                return 0.0
            return self.config.risk_per_trade_pct * equity * entry_price / stop_dist
        sizing = self.sizer.calculate(
            capital=equity,
            win_rate=_DEFAULT_BACKTEST_WIN_RATE,
            avg_win_R=_DEFAULT_BACKTEST_WIN_R,
            avg_loss_R=_DEFAULT_BACKTEST_LOSS_R,
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            sample_count=0,
        )
        return sizing.size_usdt

    def _evaluate_signal(
        self,
        symbol: str,
        ts: Any,
        regime_state,
        equity: float,
    ) -> Optional[Signal]:
        """진입 신호 평가. 거래 불가 시 None.

        strategy="oi_surge" 면 라이브 OIScanner 와 동일한 OI 급증 신호를 재현하고,
        그 외(기본 "trend_follow")는 레짐 추세추종을 쓴다.
        ATR 은 ts 이전 마감봉으로만 계산하고, 진입가는 봉 ts 의 open 만 사용한다
        (룩어헤드 차단 단계 2·3).
        """
        if self.config.strategy == "oi_surge":
            return self._evaluate_oi_surge(symbol, ts, regime_state, equity)
        if self.config.strategy == "breakout":
            return self._evaluate_breakout(symbol, ts, regime_state, equity)
        if self.config.strategy == "smc":
            return self._evaluate_smc(symbol, ts, regime_state, equity)

        if regime_state.regime == Regime.TREND_UP:
            action = "LONG"
        elif regime_state.regime == Regime.TREND_DOWN:
            action = "SHORT"
        else:
            return None

        # 마감봉만으로 ATR 계산 (룩어헤드 차단)
        closed = self._get_candles_until(symbol, "1h", ts)
        if len(closed) < ATR_PERIOD + 1:
            return None
        atr = self._calc_atr(closed, ATR_PERIOD)
        if atr <= 0:
            return None

        # B-6: 진입 체결 모델(post_only 기본). 미체결이면 스킵(라이브 정합).
        filled, entry_price = self._resolve_entry_fill(symbol, ts, action)
        if not filled:
            return None

        if action == "LONG":
            tp_price = entry_price + TP_ATR_MULT * atr
            sl_price = entry_price - SL_ATR_MULT * atr
        else:  # SHORT
            tp_price = entry_price - TP_ATR_MULT * atr
            sl_price = entry_price + SL_ATR_MULT * atr
        if sl_price <= 0 or tp_price <= 0:
            return None

        pair_tier = PAIR_TIERS.get(symbol, 2)

        # 포지션 사이징 (§8-3). 실측 데이터 없음 → 보수적 사전값 + sample_count=0.
        sizing = self.sizer.calculate(
            capital=equity,
            win_rate=_DEFAULT_BACKTEST_WIN_RATE,
            avg_win_R=_DEFAULT_BACKTEST_WIN_R,
            avg_loss_R=_DEFAULT_BACKTEST_LOSS_R,
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            sample_count=0,
        )
        if sizing.size_usdt <= 0:
            return None

        return Signal(
            symbol=symbol,
            action=action,
            setup_tag=f"trend_follow_{action.lower()}",
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            entry_ts=ts,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            atr=atr,
            pair_tier=pair_tier,
            size_usdt=sizing.size_usdt,
        )

    def _evaluate_oi_surge(
        self, symbol: str, ts: Any, regime_state, equity: float
    ) -> Optional[Signal]:
        """라이브 OIScanner 재현 — OI 급증 + 가격 변동 동시 충족 시 진입(룩어헤드 차단).

        OI/가격 변화율은 ts 이전 마감봉(open_interest/close)만으로 lookback_bars 창
        기준 계산한다. 방향은 가격 모멘텀 부호(상승→LONG, 하락→SHORT). 진입가는 봉
        ts 의 open. TP/SL 은 마감봉 ATR 기반.
        """
        df = self._candles_by_pair[symbol]
        if "open_interest" not in df.columns:
            return None

        closed = df[df.index < ts]
        lb = self.config.oi_lookback_bars
        if len(closed) < lb + 1 or len(closed) < ATR_PERIOD + 1:
            return None

        oi_now = float(closed["open_interest"].iloc[-1])
        oi_base = float(closed["open_interest"].iloc[-1 - lb])
        if oi_base <= 0:
            return None
        oi_change_pct = (oi_now - oi_base) / oi_base * 100.0

        close_now = float(closed["close"].iloc[-1])
        close_base = float(closed["close"].iloc[-1 - lb])
        if close_base <= 0:
            return None
        price_change_pct = (close_now - close_base) / close_base * 100.0

        if (oi_change_pct < self.config.oi_change_threshold_pct
                or abs(price_change_pct) < self.config.price_change_threshold_pct):
            return None

        action = "LONG" if price_change_pct > 0 else "SHORT"

        atr = self._calc_atr(self._get_candles_until(symbol, "1h", ts), ATR_PERIOD)
        if atr <= 0:
            return None
        # B-6: 진입 체결 모델(post_only 기본). 미체결이면 스킵(라이브 정합).
        filled, entry_price = self._resolve_entry_fill(symbol, ts, action)
        if not filled:
            return None

        if action == "LONG":
            tp_price = entry_price + TP_ATR_MULT * atr
            sl_price = entry_price - SL_ATR_MULT * atr
        else:  # SHORT
            tp_price = entry_price - TP_ATR_MULT * atr
            sl_price = entry_price + SL_ATR_MULT * atr
        if sl_price <= 0 or tp_price <= 0:
            return None

        pair_tier = PAIR_TIERS.get(symbol, 2)
        sizing = self.sizer.calculate(
            capital=equity,
            win_rate=_DEFAULT_BACKTEST_WIN_RATE,
            avg_win_R=_DEFAULT_BACKTEST_WIN_R,
            avg_loss_R=_DEFAULT_BACKTEST_LOSS_R,
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            sample_count=0,
        )
        if sizing.size_usdt <= 0:
            return None

        return Signal(
            symbol=symbol,
            action=action,
            setup_tag=f"oi_surge_{action.lower()}",
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            entry_ts=ts,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            atr=atr,
            pair_tier=pair_tier,
            size_usdt=sizing.size_usdt,
        )

    def _evaluate_breakout(
        self, symbol: str, ts: Any, regime_state, equity: float
    ) -> Optional[Signal]:
        """Donchian/ATR 돌파 + ADX·200EMA 레짐 게이트 (strategy/breakout 공유 로직).

        신호 판정은 ts 이전 마감봉만으로(룩어헤드 차단), 진입가는 봉 ts 의 open.
        TP/SL 은 ATR 배수(BreakoutConfig.atr_target_mult / atr_stop_mult)로 산출 —
        저승률·고R(우측꼬리) 추세추종 프로파일.
        """
        closed = self._get_candles_until(symbol, "1h", ts)
        sig = evaluate_breakout(closed, self.breakout_cfg)
        if sig is None:
            return None

        # ── Phase C 게이트 (config 플래그 off 면 모두 통과 = 기존 동작) ──
        if self.config.long_only and sig.action != "LONG":
            return None                                  # 롱 전용
        # 매크로 게이트(symmetric): macro_btc_ema_period>0 일 때
        #   LONG  = BTC risk-on(close>200EMA, BTC_RISK_OFF==OFF) 일 때만
        #   SHORT = BTC risk-off(close≤200EMA, BTC_RISK_OFF==ON) 일 때만
        # long_only=True 면 SHORT 는 위에서 이미 차단 → Phase C 동작 불변(LONG 분기 동일).
        if self.config.macro_btc_ema_period > 0:
            risk_on = self._btc_risk_on(ts)
            if sig.action == "LONG" and not risk_on:
                return None                              # BTC risk-off → 신규 롱 금지
            if sig.action == "SHORT" and risk_on:
                return None                              # BTC risk-on → 신규 숏 금지
        if not self._passes_volume_confirm(closed):
            return None                                  # 거래량 동반 미충족

        # B-6: 진입 체결 모델(post_only 기본). 미체결이면 스킵(라이브 정합).
        filled, entry_price = self._resolve_entry_fill(symbol, ts, sig.action)
        if not filled:
            return None

        atr = sig.atr
        if sig.action == "LONG":
            tp_price = entry_price + self.breakout_cfg.atr_target_mult * atr
            sl_price = entry_price - self.breakout_cfg.atr_stop_mult * atr
        else:  # SHORT
            tp_price = entry_price - self.breakout_cfg.atr_target_mult * atr
            sl_price = entry_price + self.breakout_cfg.atr_stop_mult * atr
        if sl_price <= 0 or tp_price <= 0:
            return None

        pair_tier = PAIR_TIERS.get(symbol, 2)
        size_usdt = self._size_position(equity, entry_price, sl_price, regime_state)
        if size_usdt <= 0:
            return None

        return Signal(
            symbol=symbol,
            action=sig.action,
            setup_tag=f"breakout_{sig.action.lower()}",
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            entry_ts=ts,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            atr=atr,
            pair_tier=pair_tier,
            size_usdt=size_usdt,
        )

    def _evaluate_smc(
        self, symbol: str, ts: Any, regime_state, equity: float
    ) -> Optional[Signal]:
        """SMC 셋업 B(구조+sweep+FVG/OB 복귀) — strategy/smc 공유 로직(룩어헤드 0).

        신호 판정은 ts 이전 마감봉만으로. 진입가는 _resolve_entry_fill(post_only 기본).
        청산 X(구조기반): SL = sweep 극값 ∓ 0.1·ATR, TP = 활성 범위 반대측(SH/SL).
        _simulate_trade 의 고정 sl/tp 로 재사용(트레일 off — setup_tag 가 breakout 아님).
        """
        closed = self._get_candles_until(symbol, "1d", ts)
        sig = evaluate_smc(closed, self.smc_cfg)
        if sig is None:
            return None

        # B-6: 진입 체결 모델(post_only 기본). 미체결이면 스킵(라이브 정합).
        filled, entry_price = self._resolve_entry_fill(symbol, ts, sig.action)
        if not filled:
            return None

        buf = self.smc_cfg.sl_atr_buffer * sig.atr
        if sig.action == "LONG":
            sl_price = sig.sweep_extreme - buf
            tp_price = sig.tp_level
            ok = sl_price < entry_price < tp_price        # 구조 R:R > 0 보장
        else:  # SHORT
            sl_price = sig.sweep_extreme + buf
            tp_price = sig.tp_level
            ok = tp_price < entry_price < sl_price
        if not ok or sl_price <= 0 or tp_price <= 0:
            return None

        size_usdt = self._size_position(equity, entry_price, sl_price, regime_state)
        if size_usdt <= 0:
            return None

        return Signal(
            symbol=symbol,
            action=sig.action,
            setup_tag=f"smc_{sig.action.lower()}",
            regime=regime_state.regime,
            confidence=regime_state.confidence,
            entry_ts=ts,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            atr=sig.atr,
            pair_tier=PAIR_TIERS.get(symbol, 2),
            size_usdt=size_usdt,
        )

    # ── 거래 시뮬레이션 (단계 4·5) ──
    def _simulate_trade(self, signal: Signal) -> Optional[Trade]:
        """진입봉 ts 부터 순방향 스캔하여 TP/SL/시간스톱/데이터끝 청산을 결정.

        룩어헤드 없음: 미래 봉을 미리 들여다보지 않고 봉을 하나씩 진행하며
        실제 OHLC 로 도달 여부를 판정한다. 한 봉 내 TP·SL 동시 충족 시 SL 우선.
        """
        df = self._candles_by_pair[signal.symbol]
        idx_list = df.index
        try:
            start_pos = idx_list.get_loc(signal.entry_ts)
        except KeyError:
            return None
        if not isinstance(start_pos, int):
            # 중복 인덱스 등 비정상 — 보수적으로 거래 포기
            return None

        notional = signal.size_usdt
        funding_total = 0.0
        has_funding = "funding_rate" in df.columns
        funding_periods = (
            self._bar_seconds / FUNDING_INTERVAL_SECONDS
            if self._bar_seconds > 0 else 1.0
        )

        exit_price: Optional[float] = None
        exit_ts: Any = None
        exit_reason: Optional[str] = None
        bars_held = 0

        # 돌파 청산 정교화(opt-in): ATR 샹들리에 트레일 — 고정 TP 대신 수익을
        # 끝까지 끌되 되돌림(최고가−N·ATR)에 청산. 초기 손절(sl_price)에서 시작해
        # 유리한 방향으로만 ratchet. 기존 전략/기본값은 trail_on=False 로 불변.
        trail_on = (
            self.config.breakout_trail_exit
            and signal.setup_tag.startswith("breakout")
            and signal.atr > 0
        )
        trail_mult = self.config.breakout_trail_atr_mult
        trail_stop = signal.sl_price
        extreme = signal.entry_price

        for pos in range(start_pos, len(idx_list)):
            ts = idx_list[pos]
            row = df.iloc[pos]
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            bars_held = pos - start_pos + 1

            # 펀딩비: 보유 중인 이 봉의 funding_rate 만 반영 (단계 5).
            # LONG 은 funding>0 일 때 지불(비용+), SHORT 는 수령(비용-).
            if self.config.apply_funding and has_funding:
                fr = float(row["funding_rate"])
                funding_this = fr * notional * funding_periods
                funding_total += (
                    funding_this if signal.action == "LONG" else -funding_this
                )

            # TP/SL 체크 — 한 봉 내 동시 충족 시 SL 우선 (보수적).
            if signal.action == "LONG":
                if trail_on:
                    extreme = max(extreme, high)
                    trail_stop = max(trail_stop, extreme - trail_mult * signal.atr)
                    if low <= trail_stop:
                        exit_price = trail_stop
                        exit_reason = "TRAIL" if trail_stop > signal.sl_price else "SL"
                        exit_ts = ts
                        break
                else:
                    if low <= signal.sl_price:
                        exit_price, exit_reason, exit_ts = signal.sl_price, "SL", ts
                        break
                    if high >= signal.tp_price:
                        exit_price, exit_reason, exit_ts = signal.tp_price, "TP", ts
                        break
            else:  # SHORT
                if trail_on:
                    extreme = min(extreme, low)
                    trail_stop = min(trail_stop, extreme + trail_mult * signal.atr)
                    if high >= trail_stop:
                        exit_price = trail_stop
                        exit_reason = "TRAIL" if trail_stop < signal.sl_price else "SL"
                        exit_ts = ts
                        break
                else:
                    if high >= signal.sl_price:
                        exit_price, exit_reason, exit_ts = signal.sl_price, "SL", ts
                        break
                    if low <= signal.tp_price:
                        exit_price, exit_reason, exit_ts = signal.tp_price, "TP", ts
                        break

            # 시간 스톱 (config.time_stop_bars, 기본 DEFAULT_TIME_STOP_BARS)
            if bars_held >= self.config.time_stop_bars:
                exit_price, exit_reason, exit_ts = close, "TIME_STOP", ts
                break

        # 데이터 끝까지 미청산 → 마지막 봉 종가로 청산
        if exit_price is None:
            last_pos = len(idx_list) - 1
            exit_ts = idx_list[last_pos]
            exit_price = float(df.iloc[last_pos]["close"])
            exit_reason = "DATA_END"
            bars_held = last_pos - start_pos + 1

        # ── 손익 계산 ──
        if signal.action == "LONG":
            gross_pct = (exit_price - signal.entry_price) / signal.entry_price
        else:  # SHORT
            gross_pct = (signal.entry_price - exit_price) / signal.entry_price
        gross_pnl = gross_pct * notional

        # 비용: 진입 체결 모델별 분기 (B-6, §10-1 원칙 ①). 청산은 항상 taker + 슬리피지.
        #   post_only : 진입 maker + 슬리피지 0 (resting limit 체결) → maker+taker+1slip
        #   maker_open: 진입 maker + 진입 슬리피지       (legacy)    → maker+taker+2slip
        #   taker     : 진입 taker + 진입 슬리피지                    → taker+taker+2slip
        slip = self.config.slippage_for_tier(signal.pair_tier)
        model = self.config.entry_fill_model
        if model == "taker":
            entry_fee_rate = self.config.taker_fee
            entry_slip = slip
        elif model in ("post_only", "post_only_strict"):
            entry_fee_rate = self.config.maker_fee
            entry_slip = 0.0
        else:  # maker_open (legacy)
            entry_fee_rate = self.config.maker_fee
            entry_slip = slip
        fees = (
            entry_fee_rate + self.config.taker_fee + entry_slip + slip
        ) * notional

        pnl = gross_pnl - fees - funding_total

        risk_usdt = (
            abs(signal.entry_price - signal.sl_price)
            / signal.entry_price * notional
        )
        pnl_R = pnl / risk_usdt if risk_usdt > 0 else 0.0

        return Trade(
            symbol=signal.symbol,
            setup_tag=signal.setup_tag,
            regime=signal.regime,
            action=signal.action,
            entry_ts=signal.entry_ts,
            exit_ts=exit_ts,
            entry_price=round(signal.entry_price, 8),
            exit_price=round(exit_price, 8),
            tp_price=round(signal.tp_price, 8),
            sl_price=round(signal.sl_price, 8),
            size_usdt=round(notional, 8),
            risk_usdt=round(risk_usdt, 8),
            gross_pnl_usd=round(gross_pnl, 8),
            fees_usd=round(fees, 8),
            funding_usd=round(funding_total, 8),
            pnl_usd=round(pnl, 8),
            pnl_R=round(pnl_R, 6),
            exit_reason=exit_reason,
            bars_held=bars_held,
        )

    def _mark_to_market(
        self,
        equity: float,
        open_positions: list,
        ts: Any,
    ) -> float:
        """현재 자본 + 미실현 손익 반환.

        본 엔진은 _simulate_trade 가 거래를 진입~청산까지 동기적으로 완결하므로
        루프 시점에 잔존하는 미청산 포지션이 없다 → open_positions 는 항상 빈
        리스트이고 미실현 손익은 0. 멀티바 비동기 포지션 추적으로 확장할 경우
        이 메서드가 미실현 손익을 합산하는 지점이 된다.

        Args:
            equity: 실현 기준 현재 자본.
            open_positions: 미청산 포지션 리스트 (현 모델에서는 항상 []).
            ts: 평가 시점 (시그니처 보존용; 현 모델 미사용).

        Returns:
            평가 자본 (현 모델에서는 equity 그대로).
        """
        if not open_positions:
            return equity
        # 확장 지점: open_positions 각각의 미실현 손익을 ts 시점 가격으로 합산.
        return equity

    # ── 결과 집계 ──
    def _compute_result(
        self,
        trades: List[Trade],
        equity_curve: List[Tuple[datetime, float]],
        regime_bar_count: Dict[str, int],
    ) -> BacktestResult:
        """거래 리스트·자본 곡선으로 BacktestResult 통계 산출."""
        initial = self.config.initial_capital
        final = equity_curve[-1][1] if equity_curve else initial
        total_return_pct = (
            (final - initial) / initial * 100 if initial > 0 else 0.0
        )

        total_trades = len(trades)
        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades else 0.0

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")   # 손실 거래 0건 — 과적합 신호일 수 있음 (§10-4)
        else:
            profit_factor = 0.0

        avg_win_R = (
            sum(t.pnl_R for t in wins) / winning_trades if winning_trades else 0.0
        )
        avg_loss_R = (
            sum(t.pnl_R for t in losses) / losing_trades if losing_trades else 0.0
        )
        expectancy_R = (
            sum(t.pnl_R for t in trades) / total_trades if total_trades else 0.0
        )

        # 봉별 수익률 (자본 곡선 기반)
        eq_vals = [v for _, v in equity_curve]
        bar_returns: List[float] = []
        for i in range(1, len(eq_vals)):
            prev = eq_vals[i - 1]
            bar_returns.append((eq_vals[i] - prev) / prev if prev > 0 else 0.0)

        periods_per_year = (
            SECONDS_PER_YEAR / self._bar_seconds if self._bar_seconds > 0 else 365.0
        )
        sharpe = self._sharpe(bar_returns, periods_per_year)
        sortino = self._sortino(bar_returns, periods_per_year)

        # MDD + drawdown 곡선
        drawdown_curve: List[Tuple[datetime, float]] = []
        max_dd = 0.0
        peak = eq_vals[0] if eq_vals else initial
        for dt_pt, v in equity_curve:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0.0
            drawdown_curve.append((dt_pt, round(dd, 6)))
            max_dd = max(max_dd, dd)

        annual_return_pct = self._annualize(initial, final, equity_curve)

        # 레짐별 성과 — 거래 0건인 레짐도 bars 카운트로 포함
        regime_stats: Dict[str, Dict[str, float]] = {}
        for regime, bars in regime_bar_count.items():
            r_trades = [t for t in trades if t.regime == regime]
            r_wins = [t for t in r_trades if t.pnl_usd > 0]
            regime_stats[regime] = {
                "bars": float(bars),
                "trades": float(len(r_trades)),
                "wins": float(len(r_wins)),
                "win_rate": (
                    round(len(r_wins) / len(r_trades), 4) if r_trades else 0.0
                ),
                "pnl": round(sum(t.pnl_usd for t in r_trades), 6),
            }

        # 셋업별 성과
        setup_stats: Dict[str, Dict[str, float]] = {}
        for tag in sorted({t.setup_tag for t in trades}):
            s_trades = [t for t in trades if t.setup_tag == tag]
            s_wins = [t for t in s_trades if t.pnl_usd > 0]
            setup_stats[tag] = {
                "trades": float(len(s_trades)),
                "wins": float(len(s_wins)),
                "win_rate": (
                    round(len(s_wins) / len(s_trades), 4) if s_trades else 0.0
                ),
                "pnl": round(sum(t.pnl_usd for t in s_trades), 6),
                "expectancy_R": round(
                    sum(t.pnl_R for t in s_trades) / len(s_trades), 6
                ) if s_trades else 0.0,
            }

        return BacktestResult(
            config=self.config,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=round(win_rate, 4),
            profit_factor=(
                round(profit_factor, 4)
                if profit_factor != float("inf") else float("inf")
            ),
            sharpe_ratio=round(sharpe, 4) if sharpe != float("inf") else float("inf"),
            sortino_ratio=(
                round(sortino, 4) if sortino != float("inf") else float("inf")
            ),
            max_drawdown_pct=round(max_dd, 4),
            avg_win_R=round(avg_win_R, 4),
            avg_loss_R=round(avg_loss_R, 4),
            expectancy_R=round(expectancy_R, 4),
            total_return_pct=round(total_return_pct, 4),
            annual_return_pct=round(annual_return_pct, 4),
            regime_stats=regime_stats,
            setup_stats=setup_stats,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            trades=trades,
        )

    # ── 통계 헬퍼 ──
    @staticmethod
    def _sharpe(returns: List[float], periods_per_year: float) -> float:
        """봉별 수익률 → 연환산 Sharpe Ratio. 표본 부족/무변동 시 0.0."""
        n = len(returns)
        if n < 2:
            return 0.0
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std = var ** 0.5
        if std == 0:
            return 0.0
        return (mean / std) * (periods_per_year ** 0.5)

    @staticmethod
    def _sortino(returns: List[float], periods_per_year: float) -> float:
        """봉별 수익률 → 연환산 Sortino Ratio (하방 편차 기준).

        하방 수익률이 0건이면: 평균이 양수일 때 inf, 그 외 0.0.
        """
        n = len(returns)
        if n < 2:
            return 0.0
        mean = sum(returns) / n
        downside = [r for r in returns if r < 0]
        if not downside:
            return float("inf") if mean > 0 else 0.0
        dvar = sum(r ** 2 for r in downside) / n
        dstd = dvar ** 0.5
        if dstd == 0:
            return 0.0
        return (mean / dstd) * (periods_per_year ** 0.5)

    @staticmethod
    def _annualize(
        initial: float,
        final: float,
        equity_curve: List[Tuple[datetime, float]],
    ) -> float:
        """총수익을 자본 곡선 기간 기준으로 연복리 환산 (%)."""
        if initial <= 0 or final <= 0 or len(equity_curve) < 2:
            return 0.0
        seconds = (equity_curve[-1][0] - equity_curve[0][0]).total_seconds()
        if seconds <= 0:
            return 0.0
        years = seconds / SECONDS_PER_YEAR
        if years <= 0:
            return 0.0
        return ((final / initial) ** (1.0 / years) - 1.0) * 100.0

    @staticmethod
    def _calc_atr(candles: List[tuple], period: int) -> float:
        """마감봉 튜플 리스트로 단순 ATR(최근 period TR 평균) 계산.

        candles: (open, high, low, close, volume, timestamp) 튜플 리스트.
        period+1 봉 미만이면 0.0 반환.
        """
        if len(candles) < period + 1:
            return 0.0
        trs: List[float] = []
        for i in range(1, len(candles)):
            high = candles[i][1]
            low = candles[i][2]
            prev_close = candles[i - 1][3]
            trs.append(
                max(high - low, abs(high - prev_close), abs(low - prev_close))
            )
        return sum(trs[-period:]) / period

    # ── 타임스탬프 / DataFrame 유틸 ──
    def _iter_timestamps(self) -> List[Any]:
        """모든 페어 인덱스의 합집합을 시간 오름차순으로 반환."""
        all_ts: set = set()
        for df in self._candles_by_pair.values():
            all_ts.update(df.index.tolist())
        return sorted(all_ts)

    @staticmethod
    def _normalize_df(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """필수 컬럼 검증 + 인덱스 오름차순 정렬."""
        missing = set(REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"{symbol}: 필수 컬럼 누락 {sorted(missing)}")
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
        return df

    @staticmethod
    def _infer_bar_seconds(df: pd.DataFrame) -> float:
        """인덱스 간격의 중앙값(초)으로 봉 길이 추정. 추정 불가 시 86400(1일)."""
        if len(df) < 2:
            return 86400.0
        idx = df.index
        diffs: List[float] = []
        for i in range(1, min(len(idx), 50)):
            delta = idx[i] - idx[i - 1]
            if hasattr(delta, "total_seconds"):
                diffs.append(delta.total_seconds())
            else:
                # 정수(ms) 인덱스 가정
                diffs.append(float(delta) / 1000.0)
        if not diffs:
            return 86400.0
        diffs.sort()
        mid = diffs[len(diffs) // 2]
        return mid if mid > 0 else 86400.0

    @staticmethod
    def _to_dt(ts: Any) -> datetime:
        """timestamp(다양한 타입)를 UTC tz-aware datetime 으로 변환."""
        if isinstance(ts, pd.Timestamp):
            dt = ts.to_pydatetime()
        elif isinstance(ts, datetime):
            dt = ts
        else:
            # 정수(ms) 가정
            return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _day_key(ts: Any) -> date:
        """하루 1회 한도 카운터용 날짜 키."""
        return BacktestEngine._to_dt(ts).date()
