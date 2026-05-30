"""
tests/test_backtest_engine.py
=====================================================================
BacktestEngine 단위 테스트 — 명세서 §10 Phase 0 백테스트 검증.

  1. 인위적 상승추세 (+0.5%/봉) → 레짐 TREND_UP 분류, 양의 수익 (비용 차감 후)
  2. 인위적 하락추세 (-0.5%/봉) → 레짐 TREND_DOWN 분류, SHORT 거래로 양의 수익
  3. 인위적 횡보 (±0.1% 변동)   → 레짐 RANGING 분류, 무거래, 수익 ≈ 0
  4. 룩어헤드 차단 검증:
     (a) _get_candles_until() 반환값은 항상 ts < cur_ts (현재 봉 제외)
     (b) prefix 불변성 — 뒤에 봉을 추가해도 그 전에 청산 완료된 거래의
         자본 곡선은 완전히 동일 (가장 견고한 룩어헤드 검증)

타임프레임 명시:
  task 명세의 "BTCUSDT 30일 daily candle"을, RegimeDetector 의 EMA(period+2=22봉)
  /ADX(15봉)/atr_ratio(34봉) 워밍업 + 안정성 룰(3봉 연속) 확보를 위해
  120 daily 봉(봉 간격 1일)으로 확장한다. 이는 결정의 견고성을 위한 확장이며
  +0.5%/봉, -0.5%/봉, ±0.1% 변동이라는 시나리오 본질은 동일하다.

가짜 캔들은 make_candles()로 결정론적 생성 (random 미사용, 재현 가능).
외부 호출 없음 (Binance/OpenAI mock 불필요 — 엔진은 순수 계산).
=====================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from backtesting.backtest_engine import BacktestConfig, BacktestEngine


# ── 결정론적 캔들 생성 헬퍼 ──
TS0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
INTERVAL = timedelta(days=1)


def make_candles(direction, n=120, base_price=50000.0, atr_pct=0.5):
    """방향성 일봉 캔들을 결정론적으로 생성 → pandas DataFrame.

    index   = UTC tz-aware DatetimeIndex (1일 간격)
    columns = [open, high, low, close, volume, funding_rate]

    Args:
        direction: "up" | "down" | "sideways"
          - "up"       : 매 봉 +step 상승 (ADX≈100, EMA 기울기 양수 → TREND_UP)
          - "down"     : 매 봉 -step 하락 (→ TREND_DOWN)
          - "sideways" : 진폭이 점차 축소되는 횡보 (ADX≈20, BB폭≈0, atr_ratio<0.9
                         → RANGING)
        n: 봉 개수.
        base_price: 기준 시작가.
        atr_pct: 봉당 종가 이동폭 (% of base_price).

    Returns:
        pandas.DataFrame.
    """
    step = base_price * atr_pct / 100.0
    rows = []
    index = []
    price = base_price
    for i in range(n):
        ts = TS0 + i * INTERVAL
        if direction == "up":
            o = price
            c = price + step
            h = c + step * 0.15
            l = o - step * 0.15
            price = c
        elif direction == "down":
            o = price
            c = price - step
            h = o + step * 0.15
            l = c - step * 0.15
            price = c
        elif direction == "sideways":
            # 진폭이 i에 따라 선형 축소 → 최근 ATR < 과거 ATR → atr_ratio < 1
            amp = step * 0.2 * (1.0 - 0.7 * i / max(1, n - 1))
            sign = 1.0 if i % 2 == 0 else -1.0
            o = base_price
            c = base_price + sign * amp * 0.1
            h = base_price + amp
            l = base_price - amp
        else:
            raise ValueError(f"unknown direction: {direction}")
        rows.append((o, h, l, c, 1000.0, 0.0))
        index.append(ts)

    return pd.DataFrame(
        rows,
        index=pd.DatetimeIndex(index),
        columns=["open", "high", "low", "close", "volume", "funding_rate"],
    )


def make_candles_with_oi(direction="up", n=40, base_price=50000.0, atr_pct=1.0,
                         oi_base=1_000_000.0, oi_step_pct=4.0):
    """OHLCV + open_interest 컬럼 DataFrame. OI 는 매 봉 oi_step_pct% 복리 변화."""
    df = make_candles(direction, n=n, base_price=base_price, atr_pct=atr_pct).copy()
    oi = []
    val = oi_base
    for _ in range(n):
        oi.append(val)
        val *= (1.0 + oi_step_pct / 100.0)
    df["open_interest"] = oi
    return df


def _default_config():
    """BTCUSDT 단일 페어 기본 BacktestConfig."""
    return BacktestConfig(pairs=["BTCUSDT"])


def _oi_surge_config():
    """oi_surge 전략 — 테스트용 완화 임계(OI 3% / 가격 0.5%)."""
    return BacktestConfig(
        pairs=["BTCUSDT"], strategy="oi_surge",
        oi_change_threshold_pct=3.0, price_change_threshold_pct=0.5,
        oi_lookback_bars=1,
    )


# ── OI-급증 전략 (라이브 OIScanner 재현) ────────────────────────────

def test_oi_surge_strategy_generates_trades():
    """OI +4%/봉 + 가격 상승 → oi_surge_long 거래 발생."""
    candles = make_candles_with_oi("up", n=40, atr_pct=1.0, oi_step_pct=4.0)
    result = BacktestEngine(_oi_surge_config()).run({"BTCUSDT": candles})
    assert result.total_trades > 0
    assert "oi_surge_long" in result.setup_stats
    assert set(result.setup_stats) <= {"oi_surge_long", "oi_surge_short"}


def test_oi_surge_flat_oi_no_trades():
    """OI 변화 0(임계 미달) → 거래 0건."""
    candles = make_candles_with_oi("up", n=40, atr_pct=1.0, oi_step_pct=0.0)
    result = BacktestEngine(_oi_surge_config()).run({"BTCUSDT": candles})
    assert result.total_trades == 0


def test_oi_surge_missing_oi_column_no_trades():
    """open_interest 컬럼이 없으면 oi_surge 는 무거래(크래시 없음)."""
    candles = make_candles("up", n=40, atr_pct=1.0)   # OI 컬럼 없음
    result = BacktestEngine(_oi_surge_config()).run({"BTCUSDT": candles})
    assert result.total_trades == 0


def test_oi_surge_short_on_downtrend():
    """OI 급증 + 가격 하락 → oi_surge_short 방향."""
    candles = make_candles_with_oi("down", n=40, atr_pct=1.0, oi_step_pct=4.0)
    result = BacktestEngine(_oi_surge_config()).run({"BTCUSDT": candles})
    assert result.total_trades > 0
    assert "oi_surge_short" in result.setup_stats


# ── 시나리오 1: 상승추세 → TREND_UP, 양의 수익 ──
def test_scenario_1_uptrend_trend_up_positive_return():
    candles = make_candles("up", n=120, atr_pct=0.5)
    engine = BacktestEngine(_default_config())
    result = engine.run({"BTCUSDT": candles})

    # 레짐이 TREND_UP 으로 분류됨
    assert "TREND_UP" in result.regime_stats
    assert result.regime_stats["TREND_UP"]["bars"] > 0

    # 거래가 발생함
    assert result.total_trades > 0

    # LONG 셋업만 발생 (상승추세이므로)
    assert set(result.setup_stats.keys()) == {"trend_follow_long"}

    # 비용 차감 후에도 양의 수익
    assert result.total_return_pct > 0

    # 일관된 상승추세 → 승률 매우 높음 (권장 tolerance ≥ 0.85)
    assert result.win_rate >= 0.85

    # 자본 곡선은 매 봉 1포인트
    assert len(result.equity_curve) == len(candles)
    assert len(result.drawdown_curve) == len(candles)


# ── 시나리오 2: 하락추세 → TREND_DOWN, SHORT 거래로 양의 수익 ──
def test_scenario_2_downtrend_trend_down_short_positive_return():
    candles = make_candles("down", n=120, atr_pct=0.5)
    engine = BacktestEngine(_default_config())
    result = engine.run({"BTCUSDT": candles})

    # 레짐이 TREND_DOWN 으로 분류됨
    assert "TREND_DOWN" in result.regime_stats
    assert result.regime_stats["TREND_DOWN"]["bars"] > 0

    # 거래가 발생하고 SHORT 셋업만 존재
    assert result.total_trades > 0
    assert set(result.setup_stats.keys()) == {"trend_follow_short"}

    # SHORT 거래로 비용 차감 후에도 양의 수익
    assert result.total_return_pct > 0
    assert result.win_rate >= 0.85


# ── 시나리오 3: 횡보 → RANGING, 무거래, 수익 ≈ 0 ──
def test_scenario_3_ranging_no_trades_zero_return():
    candles = make_candles("sideways", n=120)
    engine = BacktestEngine(_default_config())
    result = engine.run({"BTCUSDT": candles})

    # 레짐이 RANGING 으로 분류됨 (워밍업 후)
    assert "RANGING" in result.regime_stats
    assert result.regime_stats["RANGING"]["bars"] > 0

    # 횡보 레짐은 무거래 → 거래 0건, 수익 정확히 0
    assert result.total_trades == 0
    assert result.total_return_pct == 0.0
    assert result.setup_stats == {}

    # 자본 곡선은 시종 initial_capital 유지
    assert all(
        v == result.config.initial_capital for _, v in result.equity_curve
    )


# ── 시나리오 4-a: _get_candles_until() 은 현재 봉을 절대 포함하지 않음 ──
def test_scenario_4a_get_candles_until_excludes_current_bar():
    df = make_candles("up", n=10)
    engine = BacktestEngine(_default_config())
    # run() 없이 슬라이스 메서드만 직접 검증
    engine._candles_by_pair = {"BTCUSDT": df}
    idx = df.index

    # 기존 인덱스값을 cur_ts 로 주면 그 봉(및 이후)은 모두 제외
    cur = idx[5]
    candles = engine._get_candles_until("BTCUSDT", "4h", cur)
    assert len(candles) == 5
    assert all(c[5] < cur for c in candles), "현재/미래 봉이 누설됨 — 룩어헤드 버그"

    # 마지막 인덱스 → 마지막 봉 1개 제외 (n-1개)
    candles_last = engine._get_candles_until("BTCUSDT", "4h", idx[-1])
    assert len(candles_last) == len(df) - 1
    assert all(c[5] < idx[-1] for c in candles_last)

    # 모든 데이터보다 이후의 ts → 전체 봉 반환
    after_all = idx[-1] + INTERVAL
    assert len(engine._get_candles_until("BTCUSDT", "4h", after_all)) == len(df)

    # 모든 데이터보다 이전의 ts → 빈 리스트
    before_all = idx[0] - INTERVAL
    assert engine._get_candles_until("BTCUSDT", "4h", before_all) == []


# ── 시나리오 4-b: prefix 불변성 — 미래 봉 추가가 과거 결과를 바꾸지 않음 ──
def test_scenario_4b_prefix_invariance_no_lookahead():
    """전체 데이터로 돌린 백테스트와, 앞부분만 잘라 돌린 백테스트를 비교한다.

    엔진이 미래 데이터를 일절 참조하지 않는다면, 잘린 지점보다 충분히 이전에
    청산이 끝난 구간의 자본 곡선은 두 실행에서 완전히 동일해야 한다.
    (시간 스톱 최대 보유 24봉을 감안해 30봉 마진을 둔다.)
    """
    full = make_candles("up", n=120, atr_pct=0.5)
    prefix_len = 90
    prefix = full.iloc[:prefix_len]

    result_full = BacktestEngine(_default_config()).run({"BTCUSDT": full})
    result_prefix = BacktestEngine(_default_config()).run({"BTCUSDT": prefix})

    # 잘린 지점(index 89)에서 30봉 마진 → cutoff = index 60
    cutoff = full.index[60]

    full_eq = {ts: v for ts, v in result_full.equity_curve if ts <= cutoff}
    prefix_eq = {ts: v for ts, v in result_prefix.equity_curve if ts <= cutoff}

    # 비교 대상 타임스탬프 집합이 같아야 함
    assert full_eq.keys() == prefix_eq.keys()
    assert len(full_eq) > 0

    # 테스트가 무의미하지 않도록 — 실제로 거래가 발생했어야 함
    assert result_full.total_trades > 0

    # cutoff 이전 구간 자본 곡선은 완전히 동일 (룩어헤드 0)
    for ts in full_eq:
        assert abs(full_eq[ts] - prefix_eq[ts]) < 1e-9, (
            f"룩어헤드 의심: {ts} 시점 자본이 미래 데이터 유무에 따라 달라짐 "
            f"(full={full_eq[ts]}, prefix={prefix_eq[ts]})"
        )


# =====================================================================
# B-6 [B-3] — 진입 체결 모델 (post_only / taker / maker_open)
# 조건①: ts=진입봉, limit=close[ts-1](신호봉 종가) 핀.
# 조건②: post_only 진입가 = limit(≠open[ts]), maker, 무슬리피지.
# =====================================================================

TS_ENTRY = TS0 + INTERVAL   # 진입봉 ts (pos 1; pos 0 = 신호봉)


def _two_bar_df(prev_close, o, h, l, c):
    """bar0=신호봉(close=prev_close), bar1=진입봉 ts(OHLC 명시)."""
    idx = pd.DatetimeIndex([TS0, TS_ENTRY])
    rows = [
        (prev_close, prev_close, prev_close, prev_close, 1000.0, 0.0),
        (o, h, l, c, 1000.0, 0.0),
    ]
    return pd.DataFrame(
        rows, index=idx,
        columns=["open", "high", "low", "close", "volume", "funding_rate"],
    )


def _engine_with(df, model):
    eng = BacktestEngine(BacktestConfig(pairs=["X"], entry_fill_model=model))
    eng._candles_by_pair = {"X": df}
    return eng


def test_b6_post_only_limit_is_prev_close_and_entry_is_limit():
    """조건①②: limit=close[ts-1]=100, 진입봉이 닿으면 체결가=limit(open 101 아님)."""
    df = _two_bar_df(prev_close=100.0, o=101.0, h=102.0, l=99.5, c=101.0)  # 갭업이나 저점이 limit 터치
    filled, entry = _engine_with(df, "post_only")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is True
    assert entry == 100.0          # = close[ts-1] (라이브 주문가), open[ts]=101 아님


def test_b6_post_only_no_fill_on_gap_away_long():
    """B-3 핵심: 롱이 갭업으로 달아나 저점이 limit 위 → 미체결(승자 놓침)."""
    df = _two_bar_df(prev_close=100.0, o=102.0, h=103.0, l=101.0, c=102.0)  # low 101 > limit 100
    filled, entry = _engine_with(df, "post_only")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is False
    assert entry is None


def test_b6_post_only_boundary_low_eq_limit_fills():
    """경계: low[ts] == limit → 체결(≤)."""
    df = _two_bar_df(prev_close=100.0, o=101.0, h=102.0, l=100.0, c=101.0)
    filled, entry = _engine_with(df, "post_only")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is True and entry == 100.0


def test_b6_post_only_short_symmetry():
    """숏 대칭: high≥limit 체결 / 갭다운으로 high<limit 미체결."""
    fill_df = _two_bar_df(prev_close=100.0, o=99.0, h=100.5, l=98.0, c=99.0)   # high 100.5 ≥ 100
    f1, e1 = _engine_with(fill_df, "post_only")._resolve_entry_fill("X", TS_ENTRY, "SHORT")
    assert f1 is True and e1 == 100.0
    gap_df = _two_bar_df(prev_close=100.0, o=98.0, h=99.0, l=97.0, c=98.0)     # high 99 < 100
    f2, e2 = _engine_with(gap_df, "post_only")._resolve_entry_fill("X", TS_ENTRY, "SHORT")
    assert f2 is False and e2 is None


def test_b6_maker_open_legacy_always_fills_at_open():
    """legacy: 저점이 limit 위든 아니든 봉 open 에 확정 체결."""
    df = _two_bar_df(prev_close=100.0, o=105.0, h=106.0, l=104.0, c=105.0)
    filled, entry = _engine_with(df, "maker_open")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is True and entry == 105.0    # open[ts], 체결 무조건


def test_b6_taker_fills_at_open():
    """taker: 봉 open 확정 체결(maker_open 과 동일 가격, 수수료만 다름)."""
    df = _two_bar_df(prev_close=100.0, o=105.0, h=106.0, l=104.0, c=105.0)
    filled, entry = _engine_with(df, "taker")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is True and entry == 105.0


def test_b6_post_only_first_bar_no_prev_close_skips():
    """엣지: 진입봉이 첫 봉(직전 마감봉 없음) → 주문가 미정 → 미체결."""
    df = _two_bar_df(prev_close=100.0, o=99.0, h=100.0, l=98.0, c=99.0)
    filled, entry = _engine_with(df, "post_only")._resolve_entry_fill("X", TS0, "LONG")
    assert filled is False and entry is None


def test_b6_post_only_default_model():
    """기본값이 라이브 일치(post_only)인지 — config default 핀."""
    assert BacktestConfig(pairs=["X"]).entry_fill_model == "post_only"


def test_b6_post_only_strict_skips_on_open_gap_up():
    """post_only_strict(하한): 시초가가 limit 위로 갭하면 미체결(달리는 진입 배제)."""
    # open 101 > limit 100 → 미체결 (low 가 limit 을 터치해도 strict 는 시초가 기준)
    df = _two_bar_df(prev_close=100.0, o=101.0, h=102.0, l=99.0, c=101.5)
    filled, entry = _engine_with(df, "post_only_strict")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert filled is False and entry is None
    # open 99.5 ≤ limit 100 → 체결 at limit
    df2 = _two_bar_df(prev_close=100.0, o=99.5, h=101.0, l=99.0, c=100.5)
    f2, e2 = _engine_with(df2, "post_only_strict")._resolve_entry_fill("X", TS_ENTRY, "LONG")
    assert f2 is True and e2 == 100.0


# =====================================================================
# Phase C 후보 "개선된 돌파" — 게이트 단위테스트 (룩어헤드 포함)
# =====================================================================


def test_pc_btc_risk_on_uptrend_true():
    eng = BacktestEngine(BacktestConfig(pairs=["BTCUSDT"], macro_btc_ema_period=5))
    btc = make_candles("up", n=20)
    eng._candles_by_pair = {"BTCUSDT": btc}
    assert eng._btc_risk_on(btc.index[15]) is True       # 상승 → close>EMA5 = risk-on


def test_pc_btc_risk_off_downtrend_false():
    eng = BacktestEngine(BacktestConfig(pairs=["BTCUSDT"], macro_btc_ema_period=5))
    btc = make_candles("down", n=20)
    eng._candles_by_pair = {"BTCUSDT": btc}
    assert eng._btc_risk_on(btc.index[15]) is False       # 하락 → risk-off


def test_pc_btc_risk_on_lookahead_free():
    """index < ts 만 — 표본 부족(EMA5 미만)이면 보수적 False."""
    eng = BacktestEngine(BacktestConfig(pairs=["BTCUSDT"], macro_btc_ema_period=5))
    btc = make_candles("up", n=20)
    eng._candles_by_pair = {"BTCUSDT": btc}
    assert eng._btc_risk_on(btc.index[3]) is False        # <ts 표본 3 < 6 → False


def test_pc_btc_risk_on_no_btc_data_false():
    eng = BacktestEngine(BacktestConfig(pairs=["X"], macro_btc_ema_period=5))
    eng._candles_by_pair = {"X": make_candles("up", n=20)}   # BTC 없음
    assert eng._btc_risk_on(TS0 + 10 * INTERVAL) is False


def test_pc_volume_confirm_pass_and_block():
    eng = BacktestEngine(BacktestConfig(
        pairs=["X"], volume_confirm_mult=1.5, volume_confirm_bars=3))
    ok = [(1, 1, 1, 1, 100, 0)] * 3 + [(1, 1, 1, 1, 200, 0)]   # 200 > 1.5×100
    assert eng._passes_volume_confirm(ok) is True
    no = [(1, 1, 1, 1, 100, 0)] * 3 + [(1, 1, 1, 1, 120, 0)]   # 120 < 150
    assert eng._passes_volume_confirm(no) is False


def test_pc_volume_confirm_off_always_true():
    eng = BacktestEngine(BacktestConfig(pairs=["X"], volume_confirm_mult=0.0))
    assert eng._passes_volume_confirm([(1, 1, 1, 1, 1, 0)]) is True


def test_pc_universe_excludes_protected():
    eng = BacktestEngine(BacktestConfig(pairs=["BTCUSDT"], min_trailing_volume_usd=1.0))
    eng._candles_by_pair = {"BTCUSDT": make_candles("up", n=40)}
    assert eng._passes_universe_filter("BTCUSDT", TS0 + 35 * INTERVAL) is False


def test_pc_universe_volume_threshold():
    df = make_candles("up", n=40)   # close~50000 × vol 1000 = 거래대금 ~50M/봉
    eng_hi = BacktestEngine(BacktestConfig(
        pairs=["X"], min_trailing_volume_usd=1e9, trailing_volume_bars=10))
    eng_hi._candles_by_pair = {"X": df}
    assert eng_hi._passes_universe_filter("X", df.index[35]) is False   # 50M < 1B
    eng_lo = BacktestEngine(BacktestConfig(
        pairs=["X"], min_trailing_volume_usd=1e6, trailing_volume_bars=10))
    eng_lo._candles_by_pair = {"X": df}
    assert eng_lo._passes_universe_filter("X", df.index[35]) is True    # 50M ≥ 1M


def test_pc_risk_based_sizing_half_pct():
    """risk 0.5%: notional 산출 시 stop 도달 손실이 정확히 0.5%×equity (risk≠notional)."""
    eng = BacktestEngine(BacktestConfig(pairs=["X"], risk_per_trade_pct=0.005))
    notional = eng._size_position(10000.0, 100.0, 98.0, None)   # stop_dist 2
    assert abs(notional - 2500.0) < 1e-6                        # 0.005×10000×100/2
    loss_at_stop = (notional / 100.0) * (100.0 - 98.0)
    assert abs(loss_at_stop - 0.005 * 10000.0) < 1e-6          # = 50 = 0.5% 계좌


def test_pc_risk_sizing_zero_stop_returns_zero():
    eng = BacktestEngine(BacktestConfig(pairs=["X"], risk_per_trade_pct=0.005))
    assert eng._size_position(10000.0, 100.0, 100.0, None) == 0.0   # stop_dist 0


def test_pc_breakout_full_flow_risk_sizing_no_crash():
    """strategy=breakout + Phase C 플래그(risk-sizing/concurrent/trail) end-to-end.

    회귀 가드: 진입 평가→사이징→시뮬레이션이 크래시 없이 result 반환(과거 sizing
    변수 누락 NameError 재발 방지). risk_per_trade_pct>0 → 모든 거래 notional>0.
    """
    candles = make_candles("up", n=250, atr_pct=1.0)   # 상승 → 돌파 신호 발생
    cfg = BacktestConfig(
        pairs=["X"], strategy="breakout",
        risk_per_trade_pct=0.005, max_concurrent_positions=5,
        breakout_trail_exit=True, breakout_trail_atr_mult=3.0,
        breakout_atr_stop=2.0,
    )
    result = BacktestEngine(cfg).run({"X": candles})
    assert result.total_trades >= 0                   # 크래시 없이 완료
    assert isinstance(result.trades, list)
    for t in result.trades:
        assert t.size_usdt > 0                        # risk-based notional 산출됨
