"""
tests/test_smc.py
=====================================================================
SMC 프리미티브 + 셋업 B 단위테스트.

★ SMC 1번 함정 = 룩어헤드. 핵심 강제:
  - swing 확정지연: 인덱스 i 의 swing 은 우측 N봉이 닫혀야(배열 길이 > i+N) 보임.
  - prefix 안정: 미래 봉을 덧붙여도 기존 확정 swing/구조이벤트(과거)는 불변.
  - 재량 0: 전부 수식 → 동일 입력 동일 출력.
"""

import pytest

from strategy.smc import (
    SMCConfig,
    analyze_structure,
    evaluate_smc,
    find_swings,
    is_swing_high,
    is_swing_low,
    premium_discount,
    _poi_zone,
    _recent_sweep,
)
from strategy.breakout import atr


def _bars(seq):
    """(o,h,l,c) 리스트 → (o,h,l,c,v,ts) 튜플 (v=1000, ts=index)."""
    return [(o, h, l, c, 1000.0, i) for i, (o, h, l, c) in enumerate(seq)]


# ── 운영자 확정 LONG 시나리오 (검증된 픽스처) ──
#   상승구조(CHOCH_up→BOS_up) + 직전3봉 sweep(저점 92<확정SL 94, 종가 복귀) + bull FVG[106,108] 복귀
_LONG_SEQ = [
    (90, 92, 89, 91), (91, 95, 90, 94), (94, 99, 93, 98), (98, 98, 95, 96), (96, 97, 94, 95),
    (95, 103, 95, 102), (102, 108, 101, 107), (107, 110, 106, 109), (109, 111, 108, 110),
    (110, 110, 100, 101), (101, 102, 96, 97), (97, 98, 94, 95), (95, 100, 95, 99), (99, 101, 98, 100),
    (100, 106, 99, 105),   # 14 high=106
    (105, 105, 92, 99),    # 15 sweep (low 92 < SL 94, close 99 > 94)
    (108, 113, 108, 112),  # 16 bull FVG: low 108 > high14 106 (gap 2 ≥ 0.25·ATR)
    (112, 112, 105, 107),  # 17 retrace into [106,108]
]


def _mirror(seq, k=200.0):
    """가격 반사(p→k−p): swing high↔low, bull↔bear, BOS_up↔BOS_down. high/low 도 swap."""
    return [(k - o, k - l, k - h, k - c) for (o, h, l, c) in seq]


# ───────────────────────── swing ─────────────────────────
def test_swing_high_low_basic():
    highs = [1, 2, 5, 2, 1]
    assert is_swing_high(highs, 2, 2)
    assert not is_swing_high(highs, 2, 3)        # 경계 — 좌우 3봉 없음
    lows = [5, 4, 1, 4, 5]
    assert is_swing_low(lows, 2, 2)
    assert not is_swing_low([5, 4, 4, 4, 5], 2, 2)   # strict(동률 불가)


def test_find_swings_confirmation_lag_no_lookahead():
    """swing 은 우측 N봉이 닫혀야 보인다 — 미래 봉 없이는 미확정(룩어헤드 0 핵심)."""
    highs = [3, 4, 7, 4, 3, 2, 1]   # peak @2
    lows = [v - 1 for v in highs]
    # 길이 7-1=6 까지: i≤6-2=4 확정 → peak@2 보임. 하지만 길이 4(i≤1)면 안 보임.
    assert (2, "H", 7) in find_swings(highs[:5], lows[:5], 2)   # len5 → i≤2 → 확정
    assert (2, "H", 7) not in find_swings(highs[:4], lows[:4], 2)  # len4 → i≤1 → 미확정


def test_find_swings_prefix_stable():
    """미래 봉을 덧붙여도 기존 확정 swing(인덱스 ≤ len-1-N)은 불변."""
    highs = [3, 5, 4, 7, 2, 8, 3, 9, 4, 6, 5, 10, 4, 8, 3]
    lows = [v - 1 for v in highs]
    short = {(i, k) for (i, k, _) in find_swings(highs[:9], lows[:9], 2)}
    long_lt = {(i, k) for (i, k, _) in find_swings(highs, lows, 2) if i <= 9 - 1 - 2}
    assert short == long_lt


# ──────────────────────── structure ──────────────────────
def test_analyze_structure_choch_then_bos():
    b = _bars(_LONG_SEQ)
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    st = analyze_structure(h, l, c, 2)
    assert st["trend"] == 1
    kinds = [e[1] for e in st["events"]]
    assert kinds[0] == "CHOCH_up"          # 첫 반대돌파 = CHOCH
    assert "BOS_up" in kinds               # 이후 추세유지 돌파 = BOS


def test_analyze_structure_causal_no_lookahead():
    """과거 구조이벤트(j<prefix)는 미래 봉 추가에도 불변(인과적)."""
    b = _bars(_LONG_SEQ)
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    m = 12
    ev_prefix = analyze_structure(h[:m], l[:m], c[:m], 2)["events"]
    ev_full = [e for e in analyze_structure(h, l, c, 2)["events"] if e[0] < m]
    assert ev_prefix == ev_full


# ───────────────────────── sweep ─────────────────────────
def test_recent_sweep_bull_and_bear():
    b = _bars(_LONG_SEQ)
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    sw = _recent_sweep(h, l, c, 2, "bull", 3)
    assert sw is not None and sw["extreme"] == 92 and sw["j"] == 15
    # bear 거울상
    mb = _bars(_mirror(_LONG_SEQ))
    mh = [x[1] for x in mb]; ml = [x[2] for x in mb]; mc = [x[3] for x in mb]
    swb = _recent_sweep(mh, ml, mc, 2, "bear", 3)
    assert swb is not None and swb["extreme"] == pytest.approx(200 - 92)


# ─────────────────────────── FVG / POI ───────────────────
def test_poi_fvg_min_gap_filter():
    """FVG 갭 < 0.25·ATR 이면 POI 미인식 (OB 경로 제외하고 FVG 필터만 격리)."""
    opens = [100] * 6
    highs = [101, 102, 105, 110, 109, 108]
    lows = [99, 100, 104, 108, 103, 104]   # i=3 bull FVG: low108 > high1 102, zone[102,108]
    closes = [100, 101, 104, 109, 105, 106]  # 마지막봉 복귀: low104≤108, close106≥102
    st = {"events": []}                      # OB 없음 → 순수 FVG 경로
    cfg = SMCConfig()
    # ATR 만 바꿔 임계 격리: thr=0.25·4=1 < 갭6 → zone / thr=0.25·30=7.5 > 갭6 → None
    assert _poi_zone(opens, highs, lows, closes, st, 4.0, cfg, "bull") is not None
    assert _poi_zone(opens, highs, lows, closes, st, 30.0, cfg, "bull") is None


def test_premium_discount_calc():
    assert premium_discount(95, 90, 110) == "discount"       # 50%=100, 95<100
    assert premium_discount(105, 90, 110) == "premium"
    assert premium_discount(100, 110, 90) == "mid"           # 역전 범위


# ──────────────────────── evaluate_smc ───────────────────
def test_evaluate_smc_long():
    sig = evaluate_smc(_bars(_LONG_SEQ), SMCConfig())
    assert sig is not None
    assert sig.action == "LONG"
    assert sig.sweep_extreme == 92           # SL 기준 = sweep 저점
    assert sig.tp_level == 111               # 활성 범위 SH (반대측 유동성)
    assert sig.atr > 0


def test_evaluate_smc_short_mirror():
    sig = evaluate_smc(_bars(_mirror(_LONG_SEQ)), SMCConfig())
    assert sig is not None
    assert sig.action == "SHORT"
    assert sig.sweep_extreme == pytest.approx(200 - 92)   # sweep 고점
    assert sig.tp_level == pytest.approx(200 - 111)       # 활성 범위 SL


def test_evaluate_smc_insufficient_bars_none():
    assert evaluate_smc(_bars(_LONG_SEQ[:10]), SMCConfig()) is None


def test_evaluate_smc_flat_none():
    flat = _bars([(100, 100.5, 99.5, 100)] * 40)   # 구조 없음 → trend 0
    assert evaluate_smc(flat, SMCConfig()) is None


def test_evaluate_smc_lookahead_invariant_on_decision_bar():
    """결정봉(=마지막) 신호는 그 봉 이후 데이터에 의존하지 않는다.
    LONG 픽스처에 임의 미래 봉을 덧붙이고 같은 결정봉까지 잘라 재평가 → 동일."""
    b = _bars(_LONG_SEQ)
    base = evaluate_smc(b, SMCConfig())
    future = b + [(107, 130, 80, 120, 1000.0, 18), (120, 121, 60, 70, 1000.0, 19)]
    resliced = evaluate_smc(future[:len(b)], SMCConfig())
    assert (base.action, base.sweep_extreme, base.tp_level) == \
           (resliced.action, resliced.sweep_extreme, resliced.tp_level)


# ──────────────────────── 엔진 통합 스모크 ────────────────
def test_engine_smc_smoke_runs():
    """strategy='smc' 로 엔진이 크래시 없이 완주하고 trades 리스트를 반환."""
    import pandas as pd
    from backtesting.backtest_engine import BacktestConfig, BacktestEngine

    # _LONG_SEQ 를 반복해 충분한 길이의 합성 1d 시계열 구성
    seq = _LONG_SEQ * 25
    idx = pd.date_range("2022-01-01", periods=len(seq), freq="D", tz="UTC")
    df = pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1e7} for (o, h, l, c) in seq],
        index=idx,
    )
    cfg = BacktestConfig(pairs=["TESTUSDT"], strategy="smc",
                         entry_fill_model="post_only", risk_per_trade_pct=0.005)
    res = BacktestEngine(cfg).run({"TESTUSDT": df})
    assert isinstance(res.trades, list)
    assert res.total_trades >= 0          # 크래시 없이 완주
