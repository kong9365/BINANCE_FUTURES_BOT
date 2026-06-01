"""
tests/test_alert.py
=====================================================================
monitoring/alert.py 단위테스트 — 3블록 + "엣지없음 경고 항상" + *방향확률 문구 부재*.
"""

from __future__ import annotations

from monitoring.alert import EDGE_WARNING, format_alert
from monitoring.observer import Bar, classify, compute_indicators


def _bars(n=220, trend="up", last_vol_mult=3.0, last_taker=0.70):
    bars = []
    for i in range(n):
        c = (100.0 + i * 0.5) if trend == "up" else (100.0 + (0.5 if i % 2 else -0.5))
        bars.append(Bar(open=c, high=c + 0.3, low=c - 0.3, close=c,
                        volume=1000.0, taker_buy=500.0, ts=i))
    bars[-1].volume = 1000.0 * last_vol_mult
    bars[-1].taker_buy = bars[-1].volume * last_taker
    return bars


def test_alert_three_blocks_warning_and_no_probability():
    obs = classify(compute_indicators(_bars(trend="up"), oi_now=106.0, oi_prev=100.0))
    assert obs.grade == "STRONG"
    msg = format_alert(obs, "ADAUSDT")
    assert msg is not None
    assert "【1." in msg and "【2." in msg and "【3." in msg       # 3블록
    assert EDGE_WARNING in msg                                    # 경고 항상
    assert "R:R" in msg and "SL" in msg and "TP" in msg           # 손익구조
    # ★ 가짜확률 금지 — 방향확률/적중률 문구가 없어야 한다
    assert "확률" not in msg and "적중" not in msg


def test_alert_none_when_no_signal():
    obs = classify(compute_indicators(_bars(trend="ranging")))    # 추세없음 → NONE
    assert format_alert(obs, "X") is None
