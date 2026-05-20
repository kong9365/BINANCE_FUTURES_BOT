"""
tests/test_regime_detector.py
=====================================================================
RegimeDetector 단위 테스트 — 명세서 §8-1-3 필수 8개 시나리오
+ v3.1.2 정정 검증용 통합 스모크(9-a, 9-b).

  1. up×30 (4h/1h) + ADX 30+        → TREND_UP 확정 (conf=1.0)
  2. TREND_UP 확정 후 ranging 1회   → 후보 등록, _confirmed 직전 유지 (conf=0.5)
  3. ranging ×3 (ts 다름)           → RANGING 확정 전환 (regime_changed=True)
  4. ATR 폭발 (high_vol 1h)         → 즉시 HIGH_VOL, 안정성 룰 우회 (conf=1.0)
  5. funding=0.001 (0.10%)          → 즉시 HIGH_VOL (conf=0.2)
  6. macro_blocked=True             → 즉시 HIGH_VOL (conf=0.2)
  7. 캔들 5개만 입력                → UNCERTAIN, 길이 부족 경고 (conf=0.3)
  8. EMA 기울기≈0 + ADX 30          → UNCERTAIN (conf=0.3)
  9-a. HIGH_VOL 2회 연속            → 2번째 regime_changed=False (정정 ②)
  9-b. TREND_UP→HIGH_VOL→TREND_UP   → 각 전환마다 regime_changed=True (정정 ①②)

가짜 캔들은 make_candles()로 결정론적 생성 (random 미사용). 외부 호출 없음.
=====================================================================
"""

from __future__ import annotations

import logging

from strategy.regime_detector import RegimeDetector, Regime, RegimeState


# ── 결정론적 캔들 생성 헬퍼 ──
INTERVAL_4H_MS = 4 * 3600 * 1000
TS0 = 1_600_000_000_000


def make_candles(direction, n=30, base_price=50000.0, atr_pct=0.5, ts_offset=0):
    """방향성 캔들 결정론적 생성 (random 미사용, 재현 가능).

    각 캔들: (open, high, low, close, volume, timestamp)

    direction:
      "up"        — 매 봉 일정 step 상승 (일관 추세 → ADX≈100, EMA 기울기 양수)
      "down"      — 매 봉 일정 step 하락
      "sideways"  — 진폭이 점차 축소되는 횡보 (ADX≈0, BB폭 작음, atr_ratio<1)
      "high_vol"  — 초반 저변동성 → 후반 변동성 폭발 (atr_ratio≫2)

    ts_offset: 타임스탬프 시작 오프셋(ms). 안정성 룰 검증 시 호출별로
               candles_4h[-1][5]를 구분하기 위해 사용.
    """
    candles = []
    step = base_price * atr_pct / 100.0
    price = base_price
    for i in range(n):
        ts = TS0 + ts_offset + i * INTERVAL_4H_MS
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
            amp = step * (1.0 - 0.7 * i / max(1, n - 1))
            sign = 1.0 if i % 2 == 0 else -1.0
            o = base_price
            c = base_price + sign * amp * 0.1
            h = base_price + amp
            l = base_price - amp
        elif direction == "high_vol":
            # 초반 60% 저변동성 → 후반 변동성 폭발
            rng = step * 0.5 if i < int(n * 0.6) else step * 6.0
            o = price
            c = price + rng * 0.2
            h = c + rng
            l = o - rng
            price = c
        else:
            raise ValueError(f"unknown direction: {direction}")
        candles.append((o, h, l, c, 1000.0, ts))
    return candles


# ── 시나리오 1: up×30 + ADX 30+ → TREND_UP 확정 ──
def test_scenario_1_uptrend_confirms_trend_up():
    detector = RegimeDetector()
    state = detector.detect(
        make_candles("up", n=30),
        make_candles("up", n=30),
        funding_rate=0.0,
        macro_blocked=False,
    )

    assert isinstance(state, RegimeState)
    assert state.regime == Regime.TREND_UP
    assert state.confidence == 1.0                 # ADX≈100, EMA 기울기≈0.45% → 만점
    assert state.adx_4h >= 25.0
    assert detector.get_current_regime() == Regime.TREND_UP
    # 최초 가동: None → TREND_UP (정정 ① 적용 시 전환으로 인식)
    assert state.prev_regime is None
    assert state.regime_changed is True


# ── 시나리오 2: TREND_UP 확정 후 ranging 1회 → 후보 등록, 직전 레짐 유지 ──
def test_scenario_2_single_ranging_bar_registers_candidate_only():
    detector = RegimeDetector()
    detector.detect(make_candles("up", n=30), make_candles("up", n=30))
    assert detector.get_current_regime() == Regime.TREND_UP

    state = detector.detect(
        make_candles("sideways", n=30, ts_offset=1000 * INTERVAL_4H_MS),
        make_candles("sideways", n=40, ts_offset=1000 * INTERVAL_4H_MS),
    )

    # 후보(RANGING)는 등록되지만 확정 레짐은 직전(TREND_UP) 유지
    assert state.regime == Regime.TREND_UP
    assert state.confidence == 0.5                 # 전환 중 → 신뢰도 하향
    assert state.candidate_regime == Regime.RANGING
    assert state.candidate_streak == 1
    assert state.regime_changed is False
    assert detector.get_current_regime() == Regime.TREND_UP
    assert "후보 RANGING 대기 중" in state.reasons[0]


# ── 시나리오 3: ranging ×3 (ts 다름) → RANGING 확정 전환 ──
def test_scenario_3_three_ranging_bars_confirm_transition(caplog):
    detector = RegimeDetector()
    detector.detect(make_candles("up", n=30), make_candles("up", n=30))

    s1 = detector.detect(
        make_candles("sideways", n=30, ts_offset=1000 * INTERVAL_4H_MS),
        make_candles("sideways", n=40, ts_offset=1000 * INTERVAL_4H_MS),
    )
    s2 = detector.detect(
        make_candles("sideways", n=30, ts_offset=2000 * INTERVAL_4H_MS),
        make_candles("sideways", n=40, ts_offset=2000 * INTERVAL_4H_MS),
    )
    assert s1.candidate_streak == 1 and s1.regime == Regime.TREND_UP
    assert s2.candidate_streak == 2 and s2.regime == Regime.TREND_UP

    with caplog.at_level(logging.INFO, logger="strategy.regime_detector"):
        s3 = detector.detect(
            make_candles("sideways", n=30, ts_offset=3000 * INTERVAL_4H_MS),
            make_candles("sideways", n=40, ts_offset=3000 * INTERVAL_4H_MS),
        )

    assert s3.regime == Regime.RANGING
    assert detector.get_current_regime() == Regime.RANGING
    # v3.1.2 정정 ①: 안정성 룰 통과(=실제 전환)이므로 regime_changed=True
    assert s3.regime_changed is True
    assert s3.prev_regime == Regime.TREND_UP
    assert 0.7 < s3.confidence < 0.9               # 기대 ≈0.80
    assert any("안정성 룰 통과" in r.message for r in caplog.records)


# ── 시나리오 4: ATR 폭발 → 즉시 HIGH_VOL (안정성 룰 우회) ──
def test_scenario_4_atr_explosion_immediate_high_vol():
    detector = RegimeDetector()
    state = detector.detect(
        make_candles("up", n=30),
        make_candles("high_vol", n=40),        # 1H ATR ratio ≈ 5.7x
        funding_rate=0.0,
        macro_blocked=False,
    )

    assert state.regime == Regime.HIGH_VOL
    assert state.atr_ratio_1h >= 2.0
    assert state.confidence == 1.0             # min(1.0, 0.7 + (5.7-2.0)*0.5)
    assert any("1H ATR 폭발" in r for r in state.reasons)


# ── 시나리오 5: funding=0.001 → 즉시 HIGH_VOL ──
def test_scenario_5_extreme_funding_immediate_high_vol():
    detector = RegimeDetector()
    state = detector.detect(
        make_candles("up", n=30),
        make_candles("up", n=30),
        funding_rate=0.001,                    # 0.10%/8h, 임계 0.08% 초과
        macro_blocked=False,
    )

    assert state.regime == Regime.HIGH_VOL
    # atr_ratio_1h=1.0(데이터<34) → confidence = 0.7 + (1.0-2.0)*0.5 = 0.2
    assert state.confidence == 0.2
    assert any("펀딩비 극단" in r for r in state.reasons)


# ── 시나리오 6: macro_blocked=True → 즉시 HIGH_VOL ──
def test_scenario_6_macro_blocked_immediate_high_vol():
    detector = RegimeDetector()
    state = detector.detect(
        make_candles("up", n=30),
        make_candles("up", n=30),
        funding_rate=0.0,
        macro_blocked=True,
    )

    assert state.regime == Regime.HIGH_VOL
    assert state.confidence == 0.2
    assert any("거시 이벤트" in r for r in state.reasons)


# ── 시나리오 7: 캔들 5개만 입력 → UNCERTAIN + 길이 부족 경고 ──
def test_scenario_7_insufficient_candles_uncertain(caplog):
    detector = RegimeDetector()
    with caplog.at_level(logging.WARNING, logger="strategy.regime_detector"):
        state = detector.detect(
            make_candles("up", n=5),
            make_candles("up", n=5),
        )

    assert state.regime == Regime.UNCERTAIN
    assert state.confidence == 0.3
    # 입력 검증 로그: 권장 길이 미만 경고
    assert any("캔들 길이 부족" in r.message for r in caplog.records)


# ── 시나리오 8: EMA 기울기≈0 + ADX 30 → UNCERTAIN ──
def test_scenario_8_flat_ema_slope_uncertain():
    detector = RegimeDetector()
    # atr_pct=0.005 → 매우 완만한 상승: ADX≈100이지만 EMA 기울기≈0.005% (<0.05%)
    state = detector.detect(
        make_candles("up", n=30, atr_pct=0.005),
        make_candles("up", n=30, atr_pct=0.005),
    )

    assert state.adx_4h >= 25.0                    # ADX는 추세 강도 만점
    assert abs(state.ema_slope) < 0.05             # 그러나 기울기는 임계 미만
    assert state.regime == Regime.UNCERTAIN
    assert state.confidence == 0.3
    assert any("EMA 기울기 불명확" in r for r in state.reasons)


# ── 시나리오 9-a: HIGH_VOL 2회 연속 → 2번째는 regime_changed=False (정정 ②) ──
def test_scenario_9a_repeated_high_vol_no_notification_flood():
    detector = RegimeDetector()

    first = detector.detect(
        make_candles("up", n=30), make_candles("up", n=30), macro_blocked=True,
    )
    second = detector.detect(
        make_candles("up", n=30), make_candles("up", n=30), macro_blocked=True,
    )

    assert first.regime == Regime.HIGH_VOL
    assert first.regime_changed is True            # None → HIGH_VOL: 1회 전환 알림
    assert second.regime == Regime.HIGH_VOL
    # v3.1.2 정정 ②: HIGH_VOL 지속 시 알림 폭격 방지 — 2번째는 전환 아님
    assert second.regime_changed is False
    assert second.prev_regime == Regime.HIGH_VOL


# ── 시나리오 9-b: TREND_UP→HIGH_VOL→TREND_UP 복귀, 각 전환마다 regime_changed=True ──
def test_scenario_9b_regime_cycle_each_transition_flagged():
    detector = RegimeDetector()

    # ① 최초: TREND_UP 확정
    s_trend = detector.detect(make_candles("up", n=30), make_candles("up", n=30))
    assert s_trend.regime == Regime.TREND_UP
    assert s_trend.regime_changed is True

    # ② TREND_UP → HIGH_VOL
    s_high = detector.detect(
        make_candles("up", n=30), make_candles("up", n=30), macro_blocked=True,
    )
    assert s_high.regime == Regime.HIGH_VOL
    assert s_high.regime_changed is True
    assert s_high.prev_regime == Regime.TREND_UP

    # ③ HIGH_VOL → TREND_UP 복귀는 안정성 룰 3봉 필요 (ts 구분 필수)
    c1 = detector.detect(make_candles("up", n=30, ts_offset=10_000 * INTERVAL_4H_MS),
                         make_candles("up", n=30))
    c2 = detector.detect(make_candles("up", n=30, ts_offset=20_000 * INTERVAL_4H_MS),
                         make_candles("up", n=30))
    assert c1.regime == Regime.HIGH_VOL and c1.regime_changed is False
    assert c2.regime == Regime.HIGH_VOL and c2.regime_changed is False

    s_back = detector.detect(make_candles("up", n=30, ts_offset=30_000 * INTERVAL_4H_MS),
                             make_candles("up", n=30))
    assert s_back.regime == Regime.TREND_UP
    # v3.1.2 정정 ①: 안정성 룰 통과로 HIGH_VOL → TREND_UP 전환 정상 인식
    assert s_back.regime_changed is True
    assert s_back.prev_regime == Regime.HIGH_VOL
