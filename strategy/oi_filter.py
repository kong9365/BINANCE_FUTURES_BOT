"""
strategy/oi_filter.py
=====================================================================
OIFilter — Open Interest 변화 + 레짐 컨텍스트 기반 신호 등급 판정

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 3: oi_result = oi_filter.evaluate(candidate, regime_state))
  - docs/SPEC_v3.1.md D-2 (OIFilter (레짐 컨텍스트) → SignalGrade)
  - docs/SPEC_v3.1.md §모듈표 ("OIFilter | 있음 | 유지 | regime_state 활용")

책임:
  - OIScanner 가 추린 Candidate (OI 상승 후보) 의 방향·등급 판정
  - 레짐(RegimeState) 컨텍스트로 추세 정렬 여부 평가
  - 위험 신호(C_DANGER)는 즉시 차단 등급 부여

선물 OI 해석 (OIScanner 는 OI 상승 후보만 전달):
  - 가격 ↑ + OI ↑ : 신규 롱 유입 → action=LONG
  - 가격 ↓ + OI ↑ : 신규 숏 유입 → action=SHORT

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지"로만 표기하므로 §8-8 / D-2 의 호출 계약
    기준으로 신규 작성한다.
  - evaluate() 는 외부 호출 없는 순수 로직이므로 동기 메서드다.
  - grade 는 A_STRONG / B_MODERATE / C_DANGER 3종. score(0~100)는
    QualityGate.check() 의 입력으로 사용된다 (strategy/quality_gate.py).
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from strategy.regime_detector import Regime

logger = logging.getLogger(__name__)

# 신호 등급 상수
GRADE_A_STRONG = "A_STRONG"
GRADE_B_MODERATE = "B_MODERATE"
GRADE_C_DANGER = "C_DANGER"

# 등급별 기본 점수 (QualityGate 입력)
_SCORE_A = 80
_SCORE_B_ALIGNED = 65
_SCORE_B_NEUTRAL = 55
_SCORE_C = 0


@dataclass
class OIResult:
    """OIFilter.evaluate() 결과.

    Attributes:
        symbol: 거래 페어.
        grade: 신호 등급 (A_STRONG / B_MODERATE / C_DANGER).
        score: 0~100 점수. QualityGate.check() 의 base score 로 사용.
        action: 진입 방향 ("LONG" / "SHORT"). C_DANGER 면 방향 무의미("").
        oi_change_pct: 후보의 OI 변화율 (%).
        price_change_pct: 후보의 가격 변화율 (%).
        reasons: 등급 산출 근거 텍스트 목록.
    """

    symbol: str
    grade: str
    score: int
    action: str
    oi_change_pct: float
    price_change_pct: float
    reasons: list[str] = field(default_factory=list)


class OIFilter:
    """OI 변화 + 레짐 컨텍스트 신호 등급 판정기.

    사용:
        oi_filter = OIFilter()
        result = oi_filter.evaluate(candidate, regime_state)
        if result.grade == "C_DANGER":
            skip(result.reasons)
    """

    def __init__(
        self,
        oi_extreme_threshold_pct: float = 50.0,
        min_volume_24h_usd: float = 10_000_000.0,
        confidence_strong: float = 0.7,
    ) -> None:
        """필터 초기화.

        Args:
            oi_extreme_threshold_pct: OI 변화율이 이 값을 초과하면 조작·저유동성
                의심으로 C_DANGER 처리 (%). 기본 50%.
            min_volume_24h_usd: 24h 거래대금이 이 값 미만이면 저유동성 C_DANGER
                처리 (USDT). 기본 $10M.
            confidence_strong: 추세 정렬 시 A_STRONG 으로 승급하는 레짐 신뢰도
                하한. 기본 0.7.
        """
        self.oi_extreme_threshold_pct = oi_extreme_threshold_pct
        self.min_volume_24h_usd = min_volume_24h_usd
        self.confidence_strong = confidence_strong

    def evaluate(self, candidate, regime_state) -> OIResult:
        """후보의 방향·등급을 판정한다.

        Args:
            candidate: OIScanner 가 반환한 Candidate (symbol / price /
                oi_change_pct / price_change_pct / volume_24h).
            regime_state: RegimeDetector.detect() 가 반환한 RegimeState
                (regime / confidence).

        Returns:
            OIResult. grade == "C_DANGER" 이면 호출자(_handle_signal)가 즉시
            스킵해야 한다.
        """
        symbol = candidate.symbol
        oi_change = candidate.oi_change_pct
        price_change = candidate.price_change_pct
        regime = regime_state.regime
        confidence = regime_state.confidence
        reasons: list[str] = []

        # ── C_DANGER: HIGH_VOL 레짐 (방어 — main 이 이미 차단하나 이중 가드) ──
        if regime == Regime.HIGH_VOL:
            reasons.append("HIGH_VOL 레짐 — 신규 진입 차단")
            return self._danger(symbol, oi_change, price_change, reasons)

        # ── C_DANGER: OI 변화 극단 (조작·저유동성 의심) ──
        if oi_change > self.oi_extreme_threshold_pct:
            reasons.append(
                f"OI 변화 극단 {oi_change:.1f}% (>{self.oi_extreme_threshold_pct:.0f}%) "
                f"— 조작/저유동성 의심"
            )
            return self._danger(symbol, oi_change, price_change, reasons)

        # ── C_DANGER: 24h 거래대금 부족 (저유동성) ──
        volume_24h = getattr(candidate, "volume_24h", 0.0)
        if volume_24h < self.min_volume_24h_usd:
            reasons.append(
                f"24h 거래대금 부족 ${volume_24h/1e6:.1f}M "
                f"(<${self.min_volume_24h_usd/1e6:.0f}M)"
            )
            return self._danger(symbol, oi_change, price_change, reasons)

        # ── C_DANGER: 가격 방향 불명 ──
        if price_change == 0:
            reasons.append("가격 변화율 0% — 방향 불명")
            return self._danger(symbol, oi_change, price_change, reasons)

        # ── 방향 판정: OI 상승 후보이므로 가격 방향 = 신규 포지션 방향 ──
        action = "LONG" if price_change > 0 else "SHORT"

        # ── C_DANGER: 역추세 진입 ──
        if action == "LONG" and regime == Regime.TREND_DOWN:
            reasons.append("역추세 LONG (regime=TREND_DOWN)")
            return self._danger(symbol, oi_change, price_change, reasons)
        if action == "SHORT" and regime == Regime.TREND_UP:
            reasons.append("역추세 SHORT (regime=TREND_UP)")
            return self._danger(symbol, oi_change, price_change, reasons)

        # ── 등급 산정: 추세 정렬 + 신뢰도 ──
        aligned = (
            (action == "LONG" and regime == Regime.TREND_UP)
            or (action == "SHORT" and regime == Regime.TREND_DOWN)
        )

        if aligned and confidence >= self.confidence_strong:
            grade, score = GRADE_A_STRONG, _SCORE_A
            reasons.append(
                f"추세 정렬 {action}/{regime} + 신뢰도 {confidence:.0%} → A_STRONG"
            )
        elif aligned:
            grade, score = GRADE_B_MODERATE, _SCORE_B_ALIGNED
            reasons.append(
                f"추세 정렬 {action}/{regime} (신뢰도 {confidence:.0%} 낮음) → B_MODERATE"
            )
        else:
            # RANGING / UNCERTAIN — 추세 미정렬이나 역추세도 아님
            grade, score = GRADE_B_MODERATE, _SCORE_B_NEUTRAL
            reasons.append(f"중립 레짐 {regime} ({action}) → B_MODERATE")

        reasons.append(
            f"OI +{oi_change:.1f}%, 가격 {price_change:+.1f}%"
        )
        logger.info("[OIFilter] %s %s/%s score=%d", symbol, grade, action, score)
        return OIResult(
            symbol=symbol,
            grade=grade,
            score=score,
            action=action,
            oi_change_pct=oi_change,
            price_change_pct=price_change,
            reasons=reasons,
        )

    @staticmethod
    def _danger(symbol, oi_change, price_change, reasons) -> OIResult:
        """C_DANGER 등급 OIResult 생성 (진입 차단)."""
        logger.info("[OIFilter] %s C_DANGER: %s", symbol, reasons)
        return OIResult(
            symbol=symbol,
            grade=GRADE_C_DANGER,
            score=_SCORE_C,
            action="",
            oi_change_pct=oi_change,
            price_change_pct=price_change,
            reasons=reasons,
        )
