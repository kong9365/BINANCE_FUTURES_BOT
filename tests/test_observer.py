"""
tests/test_observer.py
=====================================================================
monitoring/observer.py 단위테스트 — 4지표 산출 + STRONG/WEAK/NONE 분류.

검증: 거래량배수·taker비율·추세방향·OI변화 산출 / STRONG(전부 강) / WEAK(≥3 완화)
      / NONE(추세없음·정렬부족) / 룩어헤드0(마감봉만) / *방향확률 미산출*(가짜확률 금지).
"""

from __future__ import annotations

import dataclasses

from monitoring.observer import Bar, Observation, classify, compute_indicators


def _bars(n=220, trend="up", last_vol_mult=3.0, last_taker=0.70):
    bars = []
    for i in range(n):
        if trend == "up":
            c = 100.0 + i * 0.5
        elif trend == "down":
            c = 200.0 - i * 0.5
        else:  # ranging
            c = 100.0 + (0.5 if i % 2 else -0.5)
        bars.append(Bar(open=c, high=c + 0.3, low=c - 0.3, close=c,
                        volume=1000.0, taker_buy=500.0, ts=i))
    last = bars[-1]
    last.volume = 1000.0 * last_vol_mult
    last.taker_buy = last.volume * last_taker
    return bars


# ── 지표 산출 ──────────────────────────────────────────────────────────────
def test_compute_insufficient_bars_none():
    assert compute_indicators(_bars(n=50)) is None     # EMA200 등 부족


def test_compute_basic_uptrend():
    st = compute_indicators(_bars(trend="up", last_vol_mult=3.0, last_taker=0.70))
    assert st is not None
    assert st.trend_dir == "LONG"
    assert abs(st.vol_ratio - 3.0) < 1e-9               # 3000/1000
    assert abs(st.taker_ratio - 0.70) < 1e-9
    assert st.oi_change_pct is None                     # OI 미전달 → None
    assert st.atr_val > 0


def test_compute_downtrend_and_oi():
    st = compute_indicators(_bars(trend="down", last_taker=0.30),
                            oi_now=106.0, oi_prev=100.0)
    assert st.trend_dir == "SHORT"
    assert abs(st.oi_change_pct - 6.0) < 1e-9           # +6%


def test_compute_ranging_trend_none():
    st = compute_indicators(_bars(trend="ranging"))
    assert st is not None and st.trend_dir is None      # 추세 앵커 없음


# ── 분류 ────────────────────────────────────────────────────────────────────
def test_classify_strong_all_aligned():
    st = compute_indicators(_bars(trend="up", last_vol_mult=3.0, last_taker=0.70),
                            oi_now=106.0, oi_prev=100.0)        # OI +6% ≥5
    obs = classify(st)
    assert obs.grade == "STRONG" and obs.direction == "LONG"
    assert obs.aligned["volume"] and obs.aligned["taker"] and obs.aligned["oi"]


def test_classify_weak_three_aligned_no_oi():
    # 거래량 1.7x(≥1.5 약, <2.0 강) + taker 0.57(≥0.55 약, <0.60 강), OI 없음 → WEAK
    st = compute_indicators(_bars(trend="up", last_vol_mult=1.7, last_taker=0.57))
    obs = classify(st)
    assert obs.grade == "WEAK" and obs.direction == "LONG"


def test_classify_none_when_alignment_insufficient():
    # 추세 LONG 이나 taker 0.50(미정렬) + 거래량 1.7x → 추세+거래량=2 < 3 → NONE
    st = compute_indicators(_bars(trend="up", last_vol_mult=1.7, last_taker=0.50))
    assert classify(st).grade == "NONE"


def test_classify_none_when_no_trend():
    st = compute_indicators(_bars(trend="ranging"))
    assert classify(st).grade == "NONE"


def test_no_probability_field():
    """관찰 보고 — 방향확률/적중률 필드가 없어야 한다(가짜확률 금지)."""
    fields = {f.name for f in dataclasses.fields(Observation)}
    assert fields == {"grade", "direction", "aligned", "state"}
    assert not any("prob" in f.lower() or "확률" in f for f in fields)
