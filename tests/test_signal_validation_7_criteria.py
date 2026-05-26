"""
tests/test_signal_validation_7_criteria.py
=====================================================================
운영자 권장 7기준 검증 — 특히 top3_excluded_pf (ZEC 단일 행운 방어).

근거:
  - backtesting/signal_validation.py
  - docs/HANDOFF.md (1d 돌파 top8 PF 2.20 → ZEC 단일 97% → 사실 PF≈1.0)
  - 운영자 결정 (2026-05-26): 추세 전략은 win rate < 0.55 도 OK
=====================================================================
"""

from __future__ import annotations

import pytest

from backtesting.signal_validation import validate_setup, DEFAULT_THRESHOLDS


def _make_trade(symbol: str, pnl_r: float, pnl_pct: float = None) -> dict:
    return {
        "symbol": symbol,
        "pnl_r": pnl_r,
        "pnl_pct": pnl_pct if pnl_pct is not None else pnl_r * 1.0,
        "is_win": pnl_r > 0,
    }


def test_passes_all_7_criteria():
    """7기준 모두 통과하는 trades — 10종목 균등 분배 (각 종목 50% win)."""
    trades = []
    symbols = [f"SYM{i}" for i in range(10)]  # 10 종목
    # 각 종목 25 trades — 같은 종목 내에서 50% win / 50% loss 균등
    for sym in symbols:
        for j in range(25):
            if j % 2 == 0:
                trades.append(_make_trade(sym, pnl_r=1.8))   # win
            else:
                trades.append(_make_trade(sym, pnl_r=-1.0))  # loss
    # 250 trades. 각 종목 13 winners + 12 losers (또는 12 + 13). 균등.
    report = validate_setup(trades, mode="trend")
    assert report.passed, f"failed: {report.failed_criteria}, metrics: {report.metrics}"
    assert report.metrics["pf"] >= 1.25
    assert report.metrics["top3_excluded_pf"] >= 1.0


def test_fail_min_n():
    """n < 200 → fail."""
    trades = [_make_trade("SYM1", 1.5)] * 100
    report = validate_setup(trades, mode="trend")
    assert "min_n" in report.failed_criteria


def test_fail_pf():
    """PF < 1.25 → fail."""
    trades = [
        _make_trade(f"SYM{i % 5}", -1.0) for i in range(150)
    ] + [
        _make_trade(f"SYM{i % 5}", 1.0) for i in range(150)
    ]
    report = validate_setup(trades, mode="trend")
    # PF = 150/150 = 1.0 < 1.25
    assert "min_pf" in report.failed_criteria


def test_zec_single_symbol_pattern_caught():
    """HANDOFF 2026-05-22 A2-②: ZEC 단일 97% 기여 → top3_excluded_pf 차단.

    250 trades 중 ZEC 가 양수 PnL 의 대부분을 차지하면
    top3_excluded_pf < 1.0 → fail.
    """
    trades = []
    # ZEC: 100 trades 중 80개 큰 승
    for i in range(100):
        if i < 80:
            trades.append(_make_trade("ZEC", pnl_r=3.0, pnl_pct=5.0))  # 큰 승
        else:
            trades.append(_make_trade("ZEC", pnl_r=-1.0, pnl_pct=-1.0))
    # 다른 9 종목: 150 trades 거의 break-even
    for i in range(150):
        sym = f"SYM{i % 9}"
        if i % 2 == 0:
            trades.append(_make_trade(sym, pnl_r=1.05))
        else:
            trades.append(_make_trade(sym, pnl_r=-1.0))
    report = validate_setup(trades, mode="trend")
    # ZEC 가 top1, single_symbol_max > 25% 또는 top3_excluded_pf 가 낮을 것
    assert (
        "max_single_symbol_pct" in report.failed_criteria
        or "min_top3_excluded_pf" in report.failed_criteria
    ), (
        f"ZEC 단일종목 패턴 미검출. metrics={report.metrics} "
        f"failed={report.failed_criteria}"
    )


def test_trend_mode_low_win_rate_ok():
    """추세 전략은 win rate < 0.55 도 OK (운영자 권장)."""
    # win rate 40% 지만 큰 승 / 작은 패 → PF > 1.25 가능
    trades = []
    symbols = [f"SYM{i}" for i in range(10)]
    for i in range(250):
        sym = symbols[i % 10]
        if i % 5 < 2:  # 40% 승
            trades.append(_make_trade(sym, pnl_r=3.5))
        else:  # 60% 패
            trades.append(_make_trade(sym, pnl_r=-1.0))
    report = validate_setup(trades, mode="trend")
    # win rate 40% 지만 trend mode 라 OK 여야 함
    assert "min_win_rate_mean_rev" not in report.failed_criteria
    # 다른 기준들 통과 확인 (PF, expectancy 등)


def test_mean_rev_mode_requires_win_rate():
    """mean_rev 모드는 win rate ≥ 55% 강제."""
    trades = []
    symbols = [f"SYM{i}" for i in range(10)]
    for i in range(250):
        sym = symbols[i % 10]
        if i % 5 < 2:  # 40% 승
            trades.append(_make_trade(sym, pnl_r=3.5))
        else:
            trades.append(_make_trade(sym, pnl_r=-1.0))
    report = validate_setup(trades, mode="mean_rev")
    assert "min_win_rate_mean_rev" in report.failed_criteria


def test_mdd_25_pct_limit():
    """MDD > 25% → fail."""
    # 의도적으로 큰 drawdown 만들기
    trades = []
    # 250 trades, 처음 100 winner 그 다음 100 loser (큰 drawdown)
    for i in range(250):
        if i < 100:
            trades.append(_make_trade(f"SYM{i % 5}", pnl_r=1.5, pnl_pct=3.0))
        elif i < 200:
            trades.append(_make_trade(f"SYM{i % 5}", pnl_r=-1.5, pnl_pct=-4.0))  # 큰 drawdown
        else:
            trades.append(_make_trade(f"SYM{i % 5}", pnl_r=2.0, pnl_pct=3.0))
    report = validate_setup(trades, mode="trend")
    # drawdown = 100 * 4 = 400% 정도 (의도적 큰 값)
    assert "max_mdd_pct" in report.failed_criteria


def test_single_symbol_25_pct_limit():
    """단일 종목 > 25% 기여 → fail."""
    # 단일 종목 SYM1 이 큰 비중 (50%+)
    trades = []
    # SYM1: 60% 비중의 양수 PnL
    for i in range(100):
        trades.append(_make_trade("SYM1", pnl_r=2.0, pnl_pct=3.0))
    # SYM2~10: 작은 비중
    for i in range(150):
        sym = f"SYM{i % 9 + 2}"
        trades.append(_make_trade(sym, pnl_r=0.2, pnl_pct=0.5))
    report = validate_setup(trades, mode="trend")
    # SYM1 단일 60%+ → fail
    assert "max_single_symbol_pct" in report.failed_criteria


def test_response_latency_check():
    """M6 라이브 응답지연 < 5초 강제."""
    trades = [_make_trade(f"SYM{i % 5}", 1.5 if i % 2 == 0 else -1.0)
              for i in range(250)]
    # 응답지연 6초 (5초 초과)
    report = validate_setup(
        trades, mode="trend", response_latency_p95_ms=6000.0,
    )
    assert "max_response_latency_p95_ms" in report.failed_criteria


def test_response_latency_under_5s_ok():
    """4초 < 5초 → OK."""
    trades = []
    symbols = [f"SYM{i}" for i in range(10)]
    for i in range(250):
        sym = symbols[i % 10]
        if i % 2 == 0:
            trades.append(_make_trade(sym, pnl_r=1.8))
        else:
            trades.append(_make_trade(sym, pnl_r=-1.0))
    report = validate_setup(
        trades, mode="trend", response_latency_p95_ms=4000.0,
    )
    assert "max_response_latency_p95_ms" not in report.failed_criteria


def test_empty_trades_fail():
    """trades=[] → 즉시 fail."""
    report = validate_setup([], mode="trend")
    assert not report.passed
    assert report.metrics["n"] == 0
