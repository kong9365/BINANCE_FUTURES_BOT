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

    # 비용 가정 (보수적, §10-2)
    maker_fee: float = 0.00018
    taker_fee: float = 0.00045
    slippage_tier_1: float = 0.00050
    slippage_tier_2: float = 0.00100
    slippage_tier_3: float = 0.00150

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

        for ts in self._iter_timestamps():
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
                # 심볼별 중복 진입 차단
                if blocked_until[symbol] is not None and ts <= blocked_until[symbol]:
                    continue
                # 하루 1회 한도
                if trades_by_day.get(day, 0) >= 1:
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
    def _evaluate_signal(
        self,
        symbol: str,
        ts: Any,
        regime_state,
        equity: float,
    ) -> Optional[Signal]:
        """진입 신호 평가. 거래 불가 시 None.

        전략: TREND_UP → LONG, TREND_DOWN → SHORT. 그 외 레짐은 무거래.
        ATR 은 ts 이전 마감봉으로만 계산하고, 진입가는 봉 ts 의 open 만 사용한다
        (룩어헤드 차단 단계 2·3).
        """
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

        # 진입 체결가 = 봉 ts 의 open (봉 시작 시점에 알 수 있는 값 — 룩어헤드 아님).
        # 봉 ts 의 high/low/close 는 여기서 절대 읽지 않는다.
        df = self._candles_by_pair[symbol]
        if ts not in df.index:
            return None
        entry_price = float(df.at[ts, "open"])
        if entry_price <= 0:
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
                if low <= signal.sl_price:
                    exit_price, exit_reason, exit_ts = signal.sl_price, "SL", ts
                    break
                if high >= signal.tp_price:
                    exit_price, exit_reason, exit_ts = signal.tp_price, "TP", ts
                    break
            else:  # SHORT
                if high >= signal.sl_price:
                    exit_price, exit_reason, exit_ts = signal.sl_price, "SL", ts
                    break
                if low <= signal.tp_price:
                    exit_price, exit_reason, exit_ts = signal.tp_price, "TP", ts
                    break

            # 시간 스톱
            if bars_held >= DEFAULT_TIME_STOP_BARS:
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

        # 비용: 진입 maker + 청산 taker + 양측 슬리피지 (§10-1 원칙 ①)
        slip = self.config.slippage_for_tier(signal.pair_tier)
        fees = (
            self.config.maker_fee + self.config.taker_fee + 2 * slip
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
