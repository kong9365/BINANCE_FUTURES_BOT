"""
tests/test_new_hypotheses.py
=====================================================================
새 가설 3종 평가기 단위테스트 (검증 전용 — faber / tsmom_ens / mean_rev).

검증:
  faber      : close>SMA→LONG / close<SMA→SHORT / long_only 차단 / 센티넬 TP·유한 SL
  tsmom_ens  : 1/3/6(테스트 2/3/5)봉 수익부호 합 부호 → 방향
  mean_rev   : SMA 추세필터 하 RSI(2) 과매도 → LONG (sanity: 헬퍼로 조건 자가검증)
  R 분모     : risk_usdt = |entry−sl|/entry·size (계획 stop 기준, 청산 무관)
  C2/센티넬  : faber 엔진 end-to-end → CostGuard 무veto(거래≥1) + TP 청산 0건

룩어헤드: 평가기는 _get_candles_until(ts 이전 마감봉)만 본다(엔진 보장).
"""

from __future__ import annotations

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine
from strategy import breakout as bo
from strategy.regime_detector import Regime


class _RS:
    """regime_state 스텁 — 평가기는 .regime/.confidence 만 사용."""

    regime = Regime.TREND_UP
    confidence = 0.7


def _df_from_closes(closes, freq="1D"):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq=freq, tz="UTC")
    rows = [{"open": float(c), "high": c + 0.5, "low": c - 0.5, "close": float(c),
             "volume": 1000.0} for c in closes]
    return pd.DataFrame(rows, index=idx)


def _eng_with(df, **cfg_kw):
    # taker 체결 → 진입 open[ts] 확정(결정적). risk-사이징 0.5%, 펀딩 off.
    cfg = BacktestConfig(entry_fill_model="taker", risk_per_trade_pct=0.005,
                         apply_funding=False, **cfg_kw)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 86400.0
    return eng


# ── faber ───────────────────────────────────────────────────────────────
def test_evaluate_faber_long_short_and_sentinel():
    up = _df_from_closes([10 + i for i in range(10)])          # 상승 → close>SMA(5)
    eng = _eng_with(up, strategy="faber", faber_sma_period=5, faber_atr_period=3)
    sig = eng._evaluate_faber("X", up.index[9], _RS(), 1000.0)
    assert sig is not None and sig.action == "LONG" and sig.setup_tag == "faber_long"
    assert sig.sl_price < sig.entry_price                       # 유한 재난 SL
    assert sig.tp_price >= sig.entry_price * 999                # 곱셈 센티넬 TP

    down = _df_from_closes([30 - i for i in range(10)])
    eng2 = _eng_with(down, strategy="faber", faber_sma_period=5, faber_atr_period=3)
    sig2 = eng2._evaluate_faber("X", down.index[9], _RS(), 1000.0)
    assert sig2 is not None and sig2.action == "SHORT"

    eng3 = _eng_with(down, strategy="faber", faber_sma_period=5, faber_atr_period=3,
                     long_only=True)
    assert eng3._evaluate_faber("X", down.index[9], _RS(), 1000.0) is None  # 롱전용 차단


# ── tsmom_ens ─────────────────────────────────────────────────────────────
def test_evaluate_tsmom_ens_direction():
    up = _df_from_closes([10 + i for i in range(12)])
    eng = _eng_with(up, strategy="tsmom_ens", tsmom_lookbacks=(2, 3, 5),
                    tsmom_atr_period=3)
    sig = eng._evaluate_tsmom_ens("X", up.index[11], _RS(), 1000.0)
    assert sig is not None and sig.action == "LONG" and sig.setup_tag == "tsmom_ens_long"

    down = _df_from_closes([30 - i for i in range(12)])
    eng2 = _eng_with(down, strategy="tsmom_ens", tsmom_lookbacks=(2, 3, 5),
                     tsmom_atr_period=3)
    sig2 = eng2._evaluate_tsmom_ens("X", down.index[11], _RS(), 1000.0)
    assert sig2 is not None and sig2.action == "SHORT"


# ── mean_rev ──────────────────────────────────────────────────────────────
def test_evaluate_mean_rev_long_when_oversold_in_uptrend():
    # 가파른 상승(+2) 후 동일 크기(-2) 5연속 하락 → RSI(2) 극저, 단 close>SMA(20) 유지.
    closes = [10 + 2 * i for i in range(25)] + [56, 54, 52, 50, 48] + [47]
    df = _df_from_closes(closes)
    ts = df.index[30]
    closed = closes[:30]                                        # ts(=idx30) 이전 마감봉
    assert closed[-1] > bo.sma(closed, 20)                      # 추세필터(close>SMA)
    assert bo.rsi_wilder(closed, 2) < 5.0                       # 과매도(자가검증)

    eng = _eng_with(df, strategy="mean_rev", meanrev_sma_period=20,
                    meanrev_rsi_period=2, meanrev_atr_period=14)
    sig = eng._evaluate_mean_rev("X", ts, _RS(), 1000.0)
    assert sig is not None and sig.action == "LONG" and sig.setup_tag == "mean_rev_long"
    assert sig.tp_price > sig.entry_price > sig.sl_price        # 고정 TP 위 / SL 아래


def test_evaluate_mean_rev_no_signal_without_extreme():
    up = _df_from_closes([10 + i for i in range(30)])           # 꾸준 상승 → RSI 높음
    eng = _eng_with(up, strategy="mean_rev", meanrev_sma_period=20, meanrev_rsi_period=2)
    assert eng._evaluate_mean_rev("X", up.index[29], _RS(), 1000.0) is None


# ── R 분모 (계획 stop 기준) ───────────────────────────────────────────────
def test_faber_risk_denominator_uses_planned_stop():
    up = _df_from_closes([10 + i for i in range(40)])
    eng = _eng_with(up, strategy="faber", faber_sma_period=5, faber_atr_period=14,
                    exit_ma_period=5, time_stop_bars=100000)
    sig = eng._evaluate_faber("X", up.index[20], _RS(), 1000.0)
    assert sig is not None
    trade = eng._simulate_trade(sig)
    assert trade is not None
    expected = round(abs(sig.entry_price - sig.sl_price) / sig.entry_price
                     * sig.size_usdt, 8)
    assert trade.risk_usdt == expected
    assert trade.exit_reason != "TP"                            # 센티넬 무발화


# ── C2: faber 엔진 end-to-end (CostGuard 무veto + 센티넬 TP 0건) ───────────
def _gentle_uptrend_df(n=120):
    idx = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    rows, price = [], 100.0
    for _ in range(n):
        o = price
        c = price * 1.003                                       # +0.3%/봉(HIGH_VOL 회피)
        rows.append({"open": o, "high": max(o, c) * 1.001,
                     "low": min(o, c) * 0.999, "close": c, "volume": 1000.0})
        price = c
    return pd.DataFrame(rows, index=idx)


def test_engine_faber_generates_trades_and_no_tp_exit():
    cfg = BacktestConfig(
        pairs=["TESTUSDT"], strategy="faber", faber_sma_period=20, faber_atr_period=14,
        exit_ma_period=20, time_stop_bars=100000, risk_per_trade_pct=0.005,
        max_concurrent_positions=5, apply_funding=False,
    )
    res = BacktestEngine(cfg).run({"TESTUSDT": _gentle_uptrend_df()})
    assert res.total_trades >= 1                                # CostGuard 무veto
    assert any(tag.startswith("faber_") for tag in res.setup_stats)
    faber_tp = [tr for tr in res.trades
                if tr.setup_tag.startswith("faber") and tr.exit_reason == "TP"]
    assert faber_tp == []                                       # 센티넬 TP 무발화
