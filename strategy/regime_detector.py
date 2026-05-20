"""
strategy/regime_detector.py
=====================================================================
RegimeDetector — v3.1 시장 레짐 감지 엔진

v3.0 대비 변경:
  - 안정성 룰 추가: TREND/RANGING 전환은 3봉 연속 조건 만족 후 확정
  - 펀딩비 입력 추가: HIGH_VOL 트리거에 funding 절댓값 통합
  - MacroEventAnalyzer 통합: 이벤트 윈도우 시 HIGH_VOL 강제

v3.1.2 정정 (2건, 사용자 승인 — 세션 5):
  ① 안정성 룰 통과 분기에서 regime_changed가 항상 False였던 버그
  ② HIGH_VOL 분기에서 _confirmed_regime 미갱신 → 알림 폭격 버그
  자세한 내용: docs/CORRECTIONS_v3.1.2.md (v3.1.2 정정 사항 누적 메모)
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

# 권장 최소 캔들 수 — 미만이면 일부 지표가 기본값으로 대체된다 (ADX/EMA/ATR 윈도우 기준).
MIN_RECOMMENDED_CANDLES = 34


# ── 상수 ──
class Regime:
    """시장 레짐 5종 상수."""
    TREND_UP   = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGING    = "RANGING"
    HIGH_VOL   = "HIGH_VOL"
    UNCERTAIN  = "UNCERTAIN"


# ── 데이터 클래스 ──
@dataclass
class RegimeState:
    """레짐 감지 결과 1회분. detect()의 반환 타입."""
    regime: str
    confidence: float                  # 0~1
    adx_4h: float
    atr_ratio_1h: float
    atr_ratio_4h: float
    bb_width_pct: float
    ema_slope: float
    funding_rate: float
    reasons: List[str] = field(default_factory=list)
    prev_regime: Optional[str] = None
    regime_changed: bool = False
    candidate_regime: Optional[str] = None   # 안정성 룰 대기 중인 후보
    candidate_streak: int = 0                # 후보 연속 충족 봉 수
    detected_at: datetime = field(default_factory=datetime.now)


# ── 보조 지표 함수 ──
def _calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Wilder ADX 계산. 데이터 부족(period+1 미만) 시 20.0(중립) 반환."""
    if len(closes) < period + 1:
        return 20.0

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(closes)):
        high, low, prev_close = highs[i], lows[i], closes[i-1]
        prev_high, prev_low = highs[i-1], lows[i-1]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        pdm = up_move if (up_move > down_move and up_move > 0) else 0
        ndm = down_move if (down_move > up_move and down_move > 0) else 0

        tr_list.append(tr)
        pdm_list.append(pdm)
        ndm_list.append(ndm)

    def wilder_smooth(lst, p):
        if len(lst) < p:
            return [sum(lst) / max(1, len(lst))]
        result = [sum(lst[:p])]
        for x in lst[p:]:
            result.append(result[-1] - result[-1] / p + x)
        return result

    atr_s = wilder_smooth(tr_list, period)
    pdm_s = wilder_smooth(pdm_list, period)
    ndm_s = wilder_smooth(ndm_list, period)

    dx_list = []
    for i in range(len(atr_s)):
        if atr_s[i] == 0:
            continue
        pdi = 100 * pdm_s[i] / atr_s[i]
        ndi = 100 * ndm_s[i] / atr_s[i]
        s = pdi + ndi
        dx = 100 * abs(pdi - ndi) / s if s > 0 else 0
        dx_list.append(dx)

    if not dx_list:
        return 20.0
    return sum(dx_list[-period:]) / min(period, len(dx_list))


def _calc_ema_slope(closes: List[float], period: int = 20) -> float:
    """마지막 2개 EMA의 변화율(% per 봉). 데이터 부족 시 0.0 반환."""
    if len(closes) < period + 2:
        return 0.0

    def ema(data: List[float], p: int) -> float:
        k = 2 / (p + 1)
        e = data[0]
        for d in data[1:]:
            e = d * k + e * (1 - k)
        return e

    e1 = ema(closes[-(period+2):-1], period)
    e2 = ema(closes[-(period+1):], period)
    return (e2 - e1) / e1 * 100 if e1 > 0 else 0.0


def _calc_bb_width_pct(closes: List[float], period: int = 20, stddev: float = 2.0) -> float:
    """볼린저밴드 폭 / 중심값(%). 데이터 부족 시 5.0(중립) 반환."""
    if len(closes) < period:
        return 5.0
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    return (2 * stddev * std / mid * 100) if mid > 0 else 5.0


def _calc_atr_ratio(highs, lows, closes, period: int = 14, lookback: int = 20) -> float:
    """현재 ATR / lookback 평균 ATR. 데이터 부족 시 1.0(중립) 반환."""
    if len(closes) < period + lookback:
        return 1.0

    atrs = []
    for i in range(1, len(closes)):
        atrs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        ))

    if len(atrs) < lookback + period:
        return 1.0

    current_atr = sum(atrs[-period:]) / period
    baseline = sum(atrs[-(lookback+period):-period]) / lookback
    return current_atr / baseline if baseline > 0 else 1.0


def _has_extreme_candle(highs, lows, opens, closes, pct: float = 3.0) -> bool:
    """최근 1봉이 ±pct% 이상 단일 캔들인지 여부. 데이터 없으면 False."""
    if not closes or not opens:
        return False
    move = abs(closes[-1] - opens[-1]) / opens[-1] * 100 if opens[-1] > 0 else 0
    return move >= pct


# ── 메인 클래스 ──
class RegimeDetector:
    """
    시장 레짐 감지.

    사용:
      detector = RegimeDetector()
      state = detector.detect(candles_4h, candles_1h, funding_rate, macro_blocked)
      print(state.regime, state.confidence)
    """

    def __init__(
        self,
        # ADX
        adx_trend_threshold: float = 25.0,
        adx_ranging_threshold: float = 20.0,
        # ATR
        atr_high_vol_ratio: float = 2.0,            # 1H ATR ≥ 2.0x 평균 → HIGH_VOL
        atr_high_vol_ratio_4h: float = 1.8,         # 4H ATR ≥ 1.8x 평균 → HIGH_VOL
        atr_low_ratio: float = 0.9,                 # RANGING 가능 조건
        # BB
        bb_ranging_width_pct: float = 3.5,
        # EMA slope
        ema_slope_trend_threshold: float = 0.05,    # % per 봉
        # Funding
        funding_high_vol_abs: float = 0.0008,       # 0.08% per 8h 절댓값
        funding_trend_max_abs: float = 0.0005,      # TREND 조건의 펀딩 상한
        # Extreme candle
        extreme_candle_pct: float = 3.0,
        # 안정성 룰
        stability_streak_required: int = 3,         # 3봉 연속 조건 충족 시 전환
        first_run_immediate: bool = True,           # 최초 가동 시 즉시 적용
    ):
        """RegimeDetector 초기화. 모든 임계값 기본값은 명세서 §8-1-2 기준."""
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_ranging_threshold = adx_ranging_threshold
        self.atr_high_vol_ratio = atr_high_vol_ratio
        self.atr_high_vol_ratio_4h = atr_high_vol_ratio_4h
        self.atr_low_ratio = atr_low_ratio
        self.bb_ranging_width_pct = bb_ranging_width_pct
        self.ema_slope_trend_threshold = ema_slope_trend_threshold
        self.funding_high_vol_abs = funding_high_vol_abs
        self.funding_trend_max_abs = funding_trend_max_abs
        self.extreme_candle_pct = extreme_candle_pct
        self.stability_streak_required = stability_streak_required
        self.first_run_immediate = first_run_immediate

        # 내부 상태
        self._confirmed_regime: Optional[str] = None
        self._candidate_regime: Optional[str] = None
        self._candidate_streak: int = 0
        self._last_candle_timestamp: Optional[int] = None  # 마지막 4H 봉 타임스탬프

    def detect(
        self,
        candles_4h: list,            # [(open, high, low, close, volume, timestamp), ...]
        candles_1h: list,
        funding_rate: float = 0.0,
        macro_blocked: bool = False,
    ) -> RegimeState:
        """
        레짐 감지 메인 메서드.

        Args:
            candles_4h: 4시간봉 캔들 리스트 (최신 마지막)
                        각 항목: (open, high, low, close, volume, [timestamp])
            candles_1h: 1시간봉 캔들 리스트
            funding_rate: 현재 펀딩비 (0.0001 = 0.01%)
            macro_blocked: MacroEventAnalyzer.is_blocked() 결과

        Returns:
            RegimeState
        """
        reasons: List[str] = []

        # v3.1.2 정정 ①②: detect() 진입 시점의 확정 레짐을 스냅샷으로 보존.
        # 명세서 §8-1-2 _make_state는 _apply_stability/_set_high_vol이 내부 상태를
        # 갱신한 뒤(또는 갱신하지 않은 채) _confirmed_regime을 읽어 regime_changed를
        # 계산하므로, 안정성 룰 통과 시 항상 False / HIGH_VOL 지속 시 항상 True가
        # 되는 버그가 있었다. 진입 스냅샷을 기준으로 전환 분기에서 재계산한다.
        # 자세한 내용: docs/CORRECTIONS_v3.1.2.md
        prev_regime_snapshot = self._confirmed_regime

        # 데이터 파싱 (안전성: 빈 리스트 방어)
        if not candles_4h or not candles_1h:
            logger.warning("[Regime] 캔들 데이터 부족 — UNCERTAIN 반환")
            return self._make_state(
                Regime.UNCERTAIN, 0.0, 20.0, 1.0, 1.0, 5.0, 0.0, funding_rate,
                ["데이터 부족"], confirmed=False,
            )

        # 입력 검증: 권장 길이 미만이면 경고만 남기고 진행 (지표 함수가 기본값으로 대체).
        if len(candles_4h) < MIN_RECOMMENDED_CANDLES or len(candles_1h) < MIN_RECOMMENDED_CANDLES:
            logger.warning(
                "[Regime] 캔들 길이 부족: 4h=%d, 1h=%d (권장 ≥%d) — "
                "일부 지표가 기본값으로 대체되어 UNCERTAIN 가능성↑",
                len(candles_4h), len(candles_1h), MIN_RECOMMENDED_CANDLES,
            )

        h4_o = [c[0] for c in candles_4h]
        h4_h = [c[1] for c in candles_4h]
        h4_l = [c[2] for c in candles_4h]
        h4_c = [c[3] for c in candles_4h]
        h1_h = [c[1] for c in candles_1h]
        h1_l = [c[2] for c in candles_1h]
        h1_o = [c[0] for c in candles_1h]
        h1_c = [c[3] for c in candles_1h]

        # 지표 계산
        adx_4h = _calc_adx(h4_h, h4_l, h4_c, period=14)
        ema_slope = _calc_ema_slope(h4_c, period=20)
        bb_width = _calc_bb_width_pct(h4_c, period=20)
        atr_ratio_1h = _calc_atr_ratio(h1_h, h1_l, h1_c, period=14, lookback=20)
        atr_ratio_4h = _calc_atr_ratio(h4_h, h4_l, h4_c, period=14, lookback=20)
        extreme_candle = _has_extreme_candle(h1_h, h1_l, h1_o, h1_c, pct=self.extreme_candle_pct)
        funding_abs = abs(funding_rate)

        # ── Step 1: HIGH_VOL 감지 (최우선, 안정성 룰 미적용) ──
        if macro_blocked:
            reasons.append("거시 이벤트 윈도우 (MacroEvent)")
            return self._set_high_vol(prev_regime_snapshot, reasons, adx_4h, atr_ratio_1h,
                                       atr_ratio_4h, bb_width, ema_slope, funding_rate)

        if atr_ratio_1h >= self.atr_high_vol_ratio:
            reasons.append(f"1H ATR 폭발: {atr_ratio_1h:.2f}x 평균")
            return self._set_high_vol(prev_regime_snapshot, reasons, adx_4h, atr_ratio_1h,
                                       atr_ratio_4h, bb_width, ema_slope, funding_rate)

        if atr_ratio_4h >= self.atr_high_vol_ratio_4h:
            reasons.append(f"4H ATR 폭발: {atr_ratio_4h:.2f}x 평균")
            return self._set_high_vol(prev_regime_snapshot, reasons, adx_4h, atr_ratio_1h,
                                       atr_ratio_4h, bb_width, ema_slope, funding_rate)

        if funding_abs >= self.funding_high_vol_abs:
            reasons.append(f"펀딩비 극단: {funding_rate*100:.4f}%/8h")
            return self._set_high_vol(prev_regime_snapshot, reasons, adx_4h, atr_ratio_1h,
                                       atr_ratio_4h, bb_width, ema_slope, funding_rate)

        if extreme_candle:
            reasons.append(f"±{self.extreme_candle_pct}% 캔들 감지")
            return self._set_high_vol(prev_regime_snapshot, reasons, adx_4h, atr_ratio_1h,
                                       atr_ratio_4h, bb_width, ema_slope, funding_rate)

        # ── Step 2: 후보 레짐 판정 (안정성 룰 적용) ──
        candidate_regime = self._classify_non_highvol(
            adx_4h, ema_slope, bb_width, atr_ratio_1h, funding_abs, reasons,
        )

        # ── Step 3: 안정성 룰 적용 ──
        confirmed = self._apply_stability(candidate_regime, candles_4h)

        if confirmed:
            state = self._make_state(
                regime=candidate_regime,
                confidence=self._calc_confidence(
                    candidate_regime, adx_4h, atr_ratio_1h, bb_width, ema_slope, funding_abs
                ),
                adx_4h=adx_4h,
                atr_ratio_1h=atr_ratio_1h,
                atr_ratio_4h=atr_ratio_4h,
                bb_width=bb_width,
                ema_slope=ema_slope,
                funding=funding_rate,
                reasons=reasons,
                confirmed=True,
            )
            # v3.1.2 정정 ①: 명세서 §8-1-2 _apply_stability는 _make_state 호출 전에
            # _confirmed_regime을 새 레짐으로 갱신한다. 그 결과 _make_state 내부의
            # `prev = self._confirmed_regime` 이 곧 새 레짐이 되어 prev == regime →
            # regime_changed가 안정성 룰 통과(=실제 전환) 시에도 항상 False가 됐다.
            # detect() 진입 스냅샷 기준으로 prev_regime / regime_changed를 재계산한다.
            # 자세한 내용: docs/CORRECTIONS_v3.1.2.md
            state.prev_regime = prev_regime_snapshot
            state.regime_changed = (prev_regime_snapshot != candidate_regime)
            return state

        # 후보 단계: 직전 확정 레짐 유지
        current = self._confirmed_regime or Regime.UNCERTAIN
        reasons.insert(0, f"후보 {candidate_regime} 대기 중 ({self._candidate_streak}/{self.stability_streak_required})")
        return self._make_state(
            regime=current,
            confidence=0.5,  # 전환 중이므로 신뢰도 낮춤
            adx_4h=adx_4h,
            atr_ratio_1h=atr_ratio_1h,
            atr_ratio_4h=atr_ratio_4h,
            bb_width=bb_width,
            ema_slope=ema_slope,
            funding=funding_rate,
            reasons=reasons,
            confirmed=False,
            candidate_regime=candidate_regime,
            candidate_streak=self._candidate_streak,
        )

    def _classify_non_highvol(
        self, adx_4h, ema_slope, bb_width, atr_ratio_1h, funding_abs, reasons: list,
    ) -> str:
        """HIGH_VOL이 아닐 때의 후보 레짐(TREND_UP/TREND_DOWN/RANGING/UNCERTAIN) 결정."""
        # TREND
        if adx_4h >= self.adx_trend_threshold and funding_abs < self.funding_high_vol_abs:
            if ema_slope > self.ema_slope_trend_threshold:
                reasons.append(f"ADX={adx_4h:.1f}, EMA 기울기 +{ema_slope:.3f}%")
                return Regime.TREND_UP
            if ema_slope < -self.ema_slope_trend_threshold:
                reasons.append(f"ADX={adx_4h:.1f}, EMA 기울기 {ema_slope:.3f}%")
                return Regime.TREND_DOWN
            reasons.append(f"ADX={adx_4h:.1f}, EMA 기울기 불명확")
            return Regime.UNCERTAIN

        # RANGING
        if (adx_4h <= self.adx_ranging_threshold
                and bb_width <= self.bb_ranging_width_pct
                and atr_ratio_1h <= self.atr_low_ratio):
            reasons.append(f"ADX={adx_4h:.1f}, BB폭={bb_width:.2f}%, ATR비={atr_ratio_1h:.2f}x")
            return Regime.RANGING

        # 경계
        reasons.append(f"경계: ADX={adx_4h:.1f}, BB폭={bb_width:.2f}%")
        return Regime.UNCERTAIN

    def _apply_stability(self, candidate: str, candles_4h: list) -> bool:
        """
        안정성 룰 적용 (TREND/RANGING 전환은 3봉 연속 충족 후 확정).

        Returns:
            확정 전환(또는 확정 상태 유지) 여부.
        """
        # 최초 가동 시 즉시 적용
        if self._confirmed_regime is None and self.first_run_immediate:
            self._confirmed_regime = candidate
            self._candidate_regime = None
            self._candidate_streak = 0
            return True

        # 후보가 현재 확정 레짐과 같으면 안정성 룰 불필요
        if candidate == self._confirmed_regime:
            self._candidate_regime = None
            self._candidate_streak = 0
            return True   # "확정 상태 유지"

        # 새 후보 시작 또는 같은 후보 연속
        if candidate == self._candidate_regime:
            # 새 4H 봉이 마감되었는지 확인 (중복 카운트 방지)
            current_ts = candles_4h[-1][5] if len(candles_4h[-1]) >= 6 else None
            if current_ts is not None and current_ts != self._last_candle_timestamp:
                self._candidate_streak += 1
                self._last_candle_timestamp = current_ts
            elif current_ts is None:
                self._candidate_streak += 1
        else:
            self._candidate_regime = candidate
            self._candidate_streak = 1
            self._last_candle_timestamp = (
                candles_4h[-1][5] if len(candles_4h[-1]) >= 6 else None
            )

        # 임계 도달 시 확정
        if self._candidate_streak >= self.stability_streak_required:
            logger.info(
                f"[Regime] 안정성 룰 통과: {self._confirmed_regime} → {candidate} "
                f"(streak={self._candidate_streak})"
            )
            self._confirmed_regime = candidate
            self._candidate_regime = None
            self._candidate_streak = 0
            return True

        return False

    def _set_high_vol(self, prev_regime_snapshot, reasons, adx, atr_1h, atr_4h, bb_w, slope, funding) -> RegimeState:
        """HIGH_VOL 즉시 적용 (안정성 룰 미적용)."""
        state = self._make_state(
            regime=Regime.HIGH_VOL,
            confidence=min(1.0, 0.7 + (atr_1h - self.atr_high_vol_ratio) * 0.5),
            adx_4h=adx, atr_ratio_1h=atr_1h, atr_ratio_4h=atr_4h,
            bb_width=bb_w, ema_slope=slope, funding=funding,
            reasons=reasons, confirmed=True,
        )
        # v3.1.2 정정 ②: 명세서 §8-1-2 _set_high_vol은 _confirmed_regime을 갱신하지
        # 않았다. 그 결과 HIGH_VOL이 N회 연속될 때마다 _make_state가 직전 확정 레짐을
        # prev로 읽어 regime_changed=True를 반복 → main_7590.py의 Telegram 알림 폭격 +
        # regime_history 중복 로깅이 발생했다. detect() 진입 스냅샷 기준으로
        # regime_changed를 1회만 True로 만들고, _confirmed_regime을 HIGH_VOL로 갱신한다.
        # 자세한 내용: docs/CORRECTIONS_v3.1.2.md
        state.prev_regime = prev_regime_snapshot
        state.regime_changed = (prev_regime_snapshot != Regime.HIGH_VOL)
        self._confirmed_regime = Regime.HIGH_VOL
        return state

    def _calc_confidence(self, regime, adx, atr_1h, bb_w, ema_slope, funding_abs) -> float:
        """레짐별 신뢰도(0~1) 계산."""
        if regime in (Regime.TREND_UP, Regime.TREND_DOWN):
            adx_score = min(1.0, (adx - 25) / 25)
            slope_score = min(1.0, abs(ema_slope) / 0.2)
            return min(1.0, 0.5 + 0.25 * adx_score + 0.25 * slope_score)
        if regime == Regime.RANGING:
            bb_score = max(0.0, (3.5 - bb_w) / 3.5)
            atr_score = max(0.0, (0.9 - atr_1h) / 0.9)
            return min(1.0, 0.4 + 0.3 * bb_score + 0.3 * atr_score)
        if regime == Regime.HIGH_VOL:
            return 0.9
        return 0.3  # UNCERTAIN

    def _make_state(
        self,
        regime: str, confidence: float,
        adx_4h: float, atr_ratio_1h: float, atr_ratio_4h: float,
        bb_width: float, ema_slope: float, funding: float,
        reasons: list, confirmed: bool,
        candidate_regime: Optional[str] = None,
        candidate_streak: int = 0,
    ) -> RegimeState:
        """RegimeState 인스턴스 생성 (지표값 반올림 + prev/changed 기본 계산).

        주의: HIGH_VOL · 안정성 룰 통과 분기는 호출 측(detect/_set_high_vol)에서
        prev_regime / regime_changed를 진입 스냅샷 기준으로 덮어쓴다 (v3.1.2 정정).
        """
        prev = self._confirmed_regime if confirmed else None
        changed = confirmed and prev is not None and prev != regime

        # 확정 전환만 _confirmed_regime 업데이트 (위 _apply_stability에서 이미 처리)

        return RegimeState(
            regime=regime,
            confidence=round(confidence, 3),
            adx_4h=round(adx_4h, 2),
            atr_ratio_1h=round(atr_ratio_1h, 3),
            atr_ratio_4h=round(atr_ratio_4h, 3),
            bb_width_pct=round(bb_width, 3),
            ema_slope=round(ema_slope, 4),
            funding_rate=round(funding, 6),
            reasons=reasons,
            prev_regime=prev,
            regime_changed=changed,
            candidate_regime=candidate_regime,
            candidate_streak=candidate_streak,
        )

    # ── 운영자 API ──
    def force_set_regime(self, regime: str, reason: str = "manual_override") -> None:
        """운영자 강제 레짐 설정 (백테스트 무효화 표시 필요)."""
        logger.warning(f"[Regime] 수동 강제 설정: {regime} (사유: {reason})")
        self._confirmed_regime = regime
        self._candidate_regime = None
        self._candidate_streak = 0

    def get_current_regime(self) -> Optional[str]:
        """현재 확정된 레짐 반환 (아직 1회도 감지 안 됐으면 None)."""
        return self._confirmed_regime
