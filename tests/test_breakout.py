"""
tests/test_breakout.py
=====================================================================
strategy/breakout.py + BacktestEngine(strategy="breakout") 단위 테스트.

검증:
  지표:
    1. ema — 상수열 → 상수
    2. atr — 알려진 TR 평균
    3. adx — 강한 추세 高 / 횡보 低 (방향성)
    4. donchian — 직전 N봉(마지막 제외) 고/저
  신호:
    5. evaluate_breakout — 상승 돌파 → LONG
    6. evaluate_breakout — 횡보(ADX 낮음) → None
    7. evaluate_breakout — 하락 돌파 → SHORT
    8. evaluate_breakout — 봉 부족 → None
  엔진 통합:
    9. strategy="breakout" 백테스트 → breakout_long 거래 ≥1, 룩어헤드 없음(설계상)
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine, Signal
from strategy import breakout as bo


def _c(o, h, low, close, ts=0):
    return (o, h, low, close, 1000.0, ts)


# ── 지표 ──────────────────────────────────────────────────────────────
def test_ema_constant_series():
    assert bo.ema([5.0] * 10, 3) == 5.0
    assert bo.ema([1.0, 2.0], 3) is None      # 부족


def test_atr_known_value():
    highs = [10, 11, 12]
    lows = [8, 9, 10]
    closes = [9, 10, 11]
    assert bo.atr(highs, lows, closes, 2) == 2.0


def test_adx_high_for_trend_low_for_chop():
    # 강한 단조 상승 → ADX 높음
    n = 40
    highs = [100 + i for i in range(n)]
    lows = [99 + i for i in range(n)]
    closes = [99.5 + i for i in range(n)]
    trend_adx = bo.adx(highs, lows, closes, 5)
    # 톱니 횡보 → ADX 낮음
    chop_h = [101 if i % 2 else 100 for i in range(n)]
    chop_l = [99 if i % 2 else 98 for i in range(n)]
    chop_c = [100 if i % 2 else 99 for i in range(n)]
    chop_adx = bo.adx(chop_h, chop_l, chop_c, 5)
    assert trend_adx > 25.0
    assert chop_adx < trend_adx
    assert chop_adx < 25.0


def test_donchian_excludes_last_bar():
    highs = [1, 2, 3, 4, 5]
    lows = [1, 1, 1, 1, 0]
    up, low = bo.donchian(highs, lows, 3)
    assert up == 4         # max(highs[-4:-1]) = max(2,3,4)
    assert low == 1        # min(lows[-4:-1]) = min(1,1,1)
    assert bo.donchian([1, 2], [1, 1], 3) is None


# ── 신호 ──────────────────────────────────────────────────────────────
_CFG = bo.BreakoutConfig(donchian_entry=10, adx_period=5, ema_period=20,
                         atr_period=5)


def _uptrend(n=30, slope=2.0):
    out = []
    for i in range(n):
        c = 100.0 + i * slope
        out.append(_c(c, c + 0.5, c - 0.5, c, i))
    return out


def test_evaluate_breakout_long():
    sig = bo.evaluate_breakout(_uptrend(), _CFG)
    assert sig is not None and sig.action == "LONG"
    assert sig.atr > 0 and sig.adx >= _CFG.adx_trend_min


def test_evaluate_breakout_ranging_none():
    rng = []
    for i in range(40):
        c = 100.0 + (1.0 if i % 2 else -1.0)
        rng.append(_c(c, c + 0.5, c - 0.5, c, i))
    assert bo.evaluate_breakout(rng, _CFG) is None     # ADX 낮음 → 무거래


def test_evaluate_breakout_short():
    down = []
    for i in range(30):
        c = 200.0 - i * 2.0
        down.append(_c(c, c + 0.5, c - 0.5, c, i))
    sig = bo.evaluate_breakout(down, _CFG)
    assert sig is not None and sig.action == "SHORT"


def test_evaluate_breakout_insufficient_bars():
    assert bo.evaluate_breakout(_uptrend(n=5), _CFG) is None


# ── 엔진 통합 ──────────────────────────────────────────────────────────
def _uptrend_df(n=90):
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rows = []
    price = 100.0
    for _ in range(n):
        o = price
        c = price * 1.003                      # +0.3%/봉, 완만하지만 지속(HIGH_VOL 아님)
        rows.append({"open": o, "high": max(o, c) * 1.001,
                     "low": min(o, c) * 0.999, "close": c, "volume": 1000.0})
        price = c
    return pd.DataFrame(rows, index=idx)


def test_engine_breakout_generates_trades():
    cfg = BacktestConfig(
        pairs=["TESTUSDT"], strategy="breakout",
        breakout_donchian=10, breakout_adx_min=20.0, breakout_ema=20,
    )
    result = BacktestEngine(cfg).run({"TESTUSDT": _uptrend_df()})
    assert result.total_trades >= 1
    assert any(tag.startswith("breakout_") for tag in result.setup_stats)


def _sig(entry_ts, atr=1.0):
    return Signal(
        symbol="X", action="LONG", setup_tag="breakout_long", regime="TREND_UP",
        confidence=0.7, entry_ts=entry_ts, entry_price=100.0,
        tp_price=200.0, sl_price=98.0, atr=atr, pair_tier=2, size_usdt=100.0,
    )


def test_simulate_trade_trail_exit():
    """breakout_trail_exit=True → ATR 샹들리에 트레일로 청산(TRAIL)."""
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame([
        {"open": 99, "high": 99, "low": 99, "close": 99, "volume": 1},      # 진입 전
        {"open": 100, "high": 101, "low": 99.5, "close": 100.5, "volume": 1},  # 진입봉
        {"open": 100.5, "high": 105, "low": 100, "close": 104, "volume": 1},   # 상승(트레일 ratchet)
        {"open": 104, "high": 110, "low": 103, "close": 109, "volume": 1},
        {"open": 109, "high": 109, "low": 104, "close": 105, "volume": 1},     # 되돌림 → 트레일 청산
    ], index=idx)
    cfg = BacktestConfig(strategy="breakout", breakout_trail_exit=True,
                         breakout_trail_atr_mult=3.0, time_stop_bars=999,
                         apply_funding=False)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 3600.0
    trade = eng._simulate_trade(_sig(idx[1]))
    assert trade is not None
    assert trade.exit_reason == "TRAIL"


def test_simulate_trade_trail_off_uses_fixed_tp_sl():
    """trail off(기본) → 고정 TP/SL 동작 불변(회귀 가드)."""
    idx = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame([
        {"open": 99, "high": 99, "low": 99, "close": 99, "volume": 1},
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},   # 진입
        {"open": 100, "high": 100, "low": 97, "close": 97.5, "volume": 1},  # SL(98) 히트
        {"open": 97, "high": 98, "low": 96, "close": 97, "volume": 1},
    ], index=idx)
    cfg = BacktestConfig(strategy="breakout", breakout_trail_exit=False,
                         time_stop_bars=999, apply_funding=False)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 3600.0
    trade = eng._simulate_trade(_sig(idx[1]))
    assert trade.exit_reason == "SL"


# ── 신규 헬퍼 (새 가설 검증: sma / rolling_return_sign / rsi_wilder) ────────
def test_sma_last_value_and_insufficient():
    assert bo.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5      # mean(3,4)
    assert bo.sma([1.0, 2.0, 3.0], 3) == 2.0
    assert bo.sma([1.0, 2.0], 3) is None               # 부족


def test_rolling_return_sign():
    assert bo.rolling_return_sign([100.0, 101.0, 102.0], 2) == 1     # 102>100
    assert bo.rolling_return_sign([100.0, 99.0, 98.0], 2) == -1      # 98<100
    assert bo.rolling_return_sign([100.0, 50.0, 100.0], 2) == 0      # 100==100
    assert bo.rolling_return_sign([100.0], 2) == 0                   # 부족


def test_rsi_wilder_bounds_and_insufficient():
    assert bo.rsi_wilder([1.0, 2.0], 5) is None                     # 부족
    assert bo.rsi_wilder([100.0 + i for i in range(20)], 14) == 100.0   # 단조상승
    assert bo.rsi_wilder([100.0 - i for i in range(20)], 14) == 0.0     # 단조하락
    assert bo.rsi_wilder([100.0] * 20, 14) == 50.0                  # 무변 → 50


def test_rsi_wilder_parity_with_indicators():
    """순수 리스트 rsi_wilder ≡ indicators.rsi(pandas) 마지막값 (§4 드리프트 방지)."""
    from strategy.indicators import rsi as pd_rsi

    closes, p = [], 100.0
    for i in range(80):                                # 결정적 패턴(상·하·보합 혼합)
        p *= 1.0 + (0.013 if i % 3 == 0 else -0.009 if i % 3 == 1 else 0.004)
        closes.append(p)
    for period in (2, 14):
        mine = bo.rsi_wilder(closes, period)
        ref = float(pd_rsi(pd.Series(closes), period).iloc[-1])
        assert mine is not None and abs(mine - ref) < 1e-6, (period, mine, ref)


def test_stdev_sample_ddof1_and_insufficient():
    assert bo.stdev([1.0, 2.0], 3) is None             # 부족
    assert bo.stdev([5.0], 2) is None
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    ref_full = float(pd.Series(vals).std(ddof=1))      # 표본 std (BB와 동일)
    assert abs(bo.stdev(vals, len(vals)) - ref_full) < 1e-9
    ref3 = float(pd.Series(vals[-3:]).std(ddof=1))     # 마지막 3봉만
    assert abs(bo.stdev(vals, 3) - ref3) < 1e-9


# ── 엔진 MA-교차 청산 (exit_ma_period — 새 가설 Faber) ──────────────────────
def _ma_df(last_low=99.5):
    """LONG MA-교차용: 상승(SMA 위)하다 idx6 종가가 SMA(3) 아래로 교차.
    closes=[100,101,102,103,104,105,100,100]; SMA(3)@idx6=mean(104,105,100)=103>100 → cross.
    """
    idx = pd.date_range("2026-01-01", periods=8, freq="1D", tz="UTC")
    closes = [100, 101, 102, 103, 104, 105, 100, 100]
    rows = []
    for i, c in enumerate(closes):
        low = last_low if i == 6 else c - 0.5
        rows.append({"open": float(c), "high": c + 0.5, "low": low,
                     "close": float(c), "volume": 1.0})
    return pd.DataFrame(rows, index=idx)


def _ma_eng(exit_ma):
    # strategy 값은 _simulate_trade 와 무관(평가기에서만 사용) → 기본 사용.
    cfg = BacktestConfig(exit_ma_period=exit_ma, time_stop_bars=999, apply_funding=False)
    eng = BacktestEngine(cfg)
    eng._bar_seconds = 86400.0
    return eng


def test_simulate_trade_ma_exit_fires():
    """exit_ma_period>0 → 종가가 SMA 아래로 교차 시 MA_EXIT(봉 종가)."""
    df = _ma_df(last_low=99.5)                     # SL(98) 미히트
    eng = _ma_eng(3)
    eng._candles_by_pair = {"X": df}
    trade = eng._simulate_trade(_sig(df.index[3]))
    assert trade is not None
    assert trade.exit_reason == "MA_EXIT"
    assert trade.exit_ts == df.index[6] and trade.exit_price == 100.0


def test_simulate_trade_ma_exit_sl_priority():
    """같은 봉에서 SL 과 MA-교차 동시 → SL 우선(SL/TP 가 MA 앞에서 체크)."""
    df = _ma_df(last_low=97.0)                     # idx6 low 97 ≤ SL 98
    eng = _ma_eng(3)
    eng._candles_by_pair = {"X": df}
    trade = eng._simulate_trade(_sig(df.index[3]))
    assert trade.exit_reason == "SL" and trade.exit_price == 98.0


def test_simulate_trade_exit_ma_default_off():
    """exit_ma_period=0(기본) → MA_EXIT 미발생(기존 동작 불변 회귀 가드)."""
    df = _ma_df(last_low=99.5)
    eng = _ma_eng(0)
    eng._candles_by_pair = {"X": df}
    trade = eng._simulate_trade(_sig(df.index[3]))
    assert trade.exit_reason != "MA_EXIT"
    assert trade.exit_reason == "DATA_END"         # SL/TP 미히트 → 데이터끝


def test_simulate_trade_ma_exit_no_lookahead():
    """청산 이후 미래 봉(idx7)을 극단값으로 바꿔도 청산 불변 → 룩어헤드 0."""
    df = _ma_df(last_low=99.5)
    base = _ma_eng(3)
    base._candles_by_pair = {"X": df}
    b = base._simulate_trade(_sig(df.index[3]))
    df2 = df.copy()
    df2.iloc[7, :] = [9999.0, 99999.0, 1.0, 9999.0, 1.0]   # 미래 봉 교란
    eng2 = _ma_eng(3)
    eng2._candles_by_pair = {"X": df2}
    a = eng2._simulate_trade(_sig(df.index[3]))
    assert (a.exit_reason, a.exit_ts, a.exit_price) == \
           (b.exit_reason, b.exit_ts, b.exit_price) == ("MA_EXIT", df.index[6], 100.0)
