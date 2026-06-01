"""
tests/test_intraday_hypotheses.py
=====================================================================
단기(15m) 가설 평가기 단위테스트 (검증 전용 — vol_breakout / intra_mean_rev).

검증:
  vol_breakout   : 당일오픈 + 0.5·전일레인지 돌파 → LONG (day 경계·룩어헤드0)
  intra_mean_rev : EMA200 상승필터 하 BB(20,2σ) 하단 이탈 → LONG, TP=BB중심(SMA20)
  (① intra_breakout 은 기존 strategy="breakout" 재사용 — test_breakout 가 커버)

룩어헤드: 평가기는 _get_candles_until(ts 이전 마감봉)만 본다(엔진 보장).
"""

from __future__ import annotations

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine
from strategy import breakout as bo
from strategy.regime_detector import Regime


class _RS:
    regime = Regime.TREND_UP
    confidence = 0.7


def _mk_15m(closes, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="15min", tz="UTC")
    rows = [{"open": float(c), "high": c + 0.5, "low": c - 0.5, "close": float(c),
             "volume": 1000.0} for c in closes]
    return pd.DataFrame(rows, index=idx)


def _eng_with(df, **cfg_kw):
    cfg = BacktestConfig(entry_fill_model="taker", risk_per_trade_pct=0.005,
                         apply_funding=False, **cfg_kw)
    eng = BacktestEngine(cfg)
    eng._candles_by_pair = {"X": df}
    eng._bar_seconds = 900.0
    return eng


# ── vol_breakout (Larry Williams 변동성 돌파) ──────────────────────────────
def test_evaluate_vol_breakout_long():
    # day0(2026-01-01) 96봉 레인지 95~105 → prev_range=11. day1 00:00 open=100.
    closes = [105.0 if i % 2 else 95.0 for i in range(96)]   # bars 0..95 (전일)
    closes += [100.0, 101.0, 102.0, 103.0, 106.0, 106.5]     # bar96=00:00 open, bar100 돌파
    df = _mk_15m(closes)                                      # len 102
    eng = _eng_with(df, strategy="vol_breakout", volbreak_k=0.5, volbreak_atr_period=14)
    sig = eng._evaluate_vol_breakout("X", df.index[101], _RS(), 1000.0)
    assert sig is not None and sig.action == "LONG"          # 106 > 100 + 0.5·11 = 105.5
    assert sig.setup_tag == "vol_breakout_long"
    assert sig.sl_price < sig.entry_price < sig.tp_price


def test_evaluate_vol_breakout_no_signal_below_trigger():
    closes = [105.0 if i % 2 else 95.0 for i in range(96)]
    closes += [100.0, 101.0, 102.0, 103.0, 104.0, 104.0]     # 104 < 105.5 → 무신호
    df = _mk_15m(closes)
    eng = _eng_with(df, strategy="vol_breakout", volbreak_k=0.5, volbreak_atr_period=14)
    assert eng._evaluate_vol_breakout("X", df.index[101], _RS(), 1000.0) is None


# ── intra_mean_rev (볼린저 평균회귀) ───────────────────────────────────────
def test_evaluate_intra_mean_rev_long():
    # 완만한 상승(close>EMA200) 후 마지막 마감봉이 BB하단 이탈.
    closes = [100.0 + 0.05 * i for i in range(250)] + [110.0] + [110.5]
    df = _mk_15m(closes)
    ts = df.index[251]
    closed = closes[:251]                                    # < ts
    mid = bo.sma(closed, 20); sd = bo.stdev(closed, 20); ema200 = bo.ema(closed, 200)
    assert closed[-1] < mid - 2.0 * sd and closed[-1] > ema200   # 진입 조건(자가검증)
    eng = _eng_with(df, strategy="intra_mean_rev", intramr_bb_period=20,
                    intramr_bb_std=2.0, intramr_ema_period=200, intramr_atr_period=14)
    sig = eng._evaluate_intra_mean_rev("X", ts, _RS(), 1000.0)
    assert sig is not None and sig.action == "LONG"
    assert sig.setup_tag == "intra_mean_rev_long"
    assert abs(sig.tp_price - mid) < 1e-9                     # TP = BB중심(SMA20)@진입
    assert sig.sl_price < sig.entry_price < sig.tp_price      # 재난 SL 아래 / 목표 위


def test_evaluate_intra_mean_rev_no_signal_without_dip():
    closes = [100.0 + 0.05 * i for i in range(260)]          # 꾸준 상승, 밴드 이탈 없음
    df = _mk_15m(closes)
    eng = _eng_with(df, strategy="intra_mean_rev", intramr_bb_period=20,
                    intramr_bb_std=2.0, intramr_ema_period=200, intramr_atr_period=14)
    assert eng._evaluate_intra_mean_rev("X", df.index[259], _RS(), 1000.0) is None
