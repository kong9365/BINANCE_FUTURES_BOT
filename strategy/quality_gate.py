"""
strategy/quality_gate.py
=====================================================================
QualityGate — 레짐별 임계 품질 점수 게이트

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 3: quality = quality_gate.check(candidate, oi_result, required_score))
  - docs/SPEC_v3.1.md D-2 (QualityGate.check() → required_score (regime-based))
  - docs/SPEC_v3.1.md §모듈표 ("QualityGate | 있음 | 유지")

책임:
  - OIResult 의 base score 에 후보 품질 보정을 가산해 최종 점수 산출
  - 레짐별 required_score (REGIME_TRADING_PARAMS[regime].required_quality) 통과 판정
  - 진입 파라미터(action / entry_price / tp / sl / setup_tag) 확정

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지"로만 표기하므로 §8-8 / D-2 의 호출 계약
    기준으로 신규 작성한다.
  - check() 는 외부 호출 없는 순수 로직이므로 동기 메서드다.
  - tp/sl 은 ATR 미입력 환경을 고려해 고정 비율(R:R 2:1)로 산출한다.
    ATR 기반 정밀화는 ExitPlanController 가 트레일링에서 담당한다.
  - setup_tag 는 action 기반("oi_surge_long"/"oi_surge_short")으로 통일한다.
    grade 별로 쪼개면 ExpectancyAnalyzer 표본이 분산되므로 전략 단위로 유지.
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from strategy.oi_filter import GRADE_C_DANGER

logger = logging.getLogger(__name__)


@dataclass
class QualityResult:
    """QualityGate.check() 결과.

    Attributes:
        passed: 진입 허용 여부 (score >= required_score 이고 C_DANGER 아님).
        score: 품질 보정이 가산된 최종 점수.
        required_score: 레짐별 통과 임계 (호출자가 전달).
        setup_tag: 셋업 식별 태그 ("oi_surge_long" / "oi_surge_short").
        action: 진입 방향 ("LONG" / "SHORT"). 차단 시 "".
        entry_price: 진입 가격 (후보 현재가).
        tp: 익절 목표가.
        sl: 손절가.
        reasons: 점수 산출 근거 텍스트 목록.
    """

    passed: bool
    score: int
    required_score: int
    setup_tag: str
    action: str
    entry_price: float
    tp: float
    sl: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """직렬화용 dict (decision 구성·ShadowRecorder·로깅용)."""
        return asdict(self)


class QualityGate:
    """레짐별 임계 품질 점수 게이트.

    사용:
        gate = QualityGate()
        quality = gate.check(candidate, oi_result, required_score=55)
        if not quality.passed:
            skip()
    """

    def __init__(
        self,
        tp_pct: float = 0.02,
        sl_pct: float = 0.01,
        volume_bonus_threshold_usd: float = 100_000_000.0,
        volume_bonus_high_usd: float = 500_000_000.0,
        oi_bonus_threshold_pct: float = 15.0,
        price_overheated_pct: float = 15.0,
    ) -> None:
        """게이트 초기화.

        Args:
            tp_pct: 익절 목표 비율 (entry 대비). 기본 2%.
            sl_pct: 손절 비율 (entry 대비). 기본 1% → R:R 2:1.
            volume_bonus_threshold_usd: 24h 거래대금 +5점 기준 (USDT).
            volume_bonus_high_usd: 24h 거래대금 +10점 기준 (USDT).
            oi_bonus_threshold_pct: OI 변화율 +5점 기준 (%).
            price_overheated_pct: 가격 변화율 절댓값이 이 값을 초과하면
                과열 추격으로 -10점 패널티 (%).
        """
        if tp_pct <= 0 or sl_pct <= 0:
            raise ValueError(f"tp_pct/sl_pct must be > 0, got {tp_pct}/{sl_pct}")
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.volume_bonus_threshold_usd = volume_bonus_threshold_usd
        self.volume_bonus_high_usd = volume_bonus_high_usd
        self.oi_bonus_threshold_pct = oi_bonus_threshold_pct
        self.price_overheated_pct = price_overheated_pct

    def check(self, candidate, oi_result, required_score: int) -> QualityResult:
        """후보의 최종 품질 점수를 산출하고 진입 허용 여부를 판정한다.

        Args:
            candidate: OIScanner Candidate (price / oi_change_pct /
                price_change_pct / volume_24h).
            oi_result: OIFilter OIResult (grade / score / action).
            required_score: 레짐별 통과 임계 (REGIME_TRADING_PARAMS 의
                required_quality).

        Returns:
            QualityResult. passed=False 면 호출자(_handle_signal)가 스킵한다.
        """
        symbol = candidate.symbol
        action = oi_result.action
        reasons: list[str] = []

        # ── 방어 가드: C_DANGER 는 점수와 무관하게 차단 ──
        if oi_result.grade == GRADE_C_DANGER:
            reasons.append("OIFilter C_DANGER — 점수 무관 차단")
            return self._blocked(required_score, candidate.price, reasons)

        # ── base score + 품질 보정 ──
        score = oi_result.score
        reasons.append(f"base score {score} (OIFilter {oi_result.grade})")

        volume_24h = getattr(candidate, "volume_24h", 0.0)
        if volume_24h >= self.volume_bonus_high_usd:
            score += 10
            reasons.append(f"거래대금 ${volume_24h/1e6:.0f}M → +10")
        elif volume_24h >= self.volume_bonus_threshold_usd:
            score += 5
            reasons.append(f"거래대금 ${volume_24h/1e6:.0f}M → +5")

        if candidate.oi_change_pct >= self.oi_bonus_threshold_pct:
            score += 5
            reasons.append(f"OI +{candidate.oi_change_pct:.1f}% → +5")

        abs_price_change = abs(candidate.price_change_pct)
        if abs_price_change > self.price_overheated_pct:
            score -= 10
            reasons.append(
                f"가격 변화 {candidate.price_change_pct:+.1f}% 과열 → -10"
            )
        elif abs_price_change >= 5.0:
            score += 3
            reasons.append(f"가격 변화 {candidate.price_change_pct:+.1f}% → +3")

        # ── 통과 판정 ──
        passed = score >= required_score
        reasons.append(
            f"최종 {score} {'≥' if passed else '<'} 임계 {required_score} "
            f"→ {'통과' if passed else '차단'}"
        )

        entry_price = candidate.price
        tp, sl = self._compute_tp_sl(action, entry_price)
        setup_tag = f"oi_surge_{action.lower()}"

        if passed:
            logger.info(
                "[QualityGate] %s 통과 score=%d/%d %s setup=%s",
                symbol, score, required_score, action, setup_tag,
            )
        else:
            logger.info(
                "[QualityGate] %s 차단 score=%d/%d", symbol, score, required_score
            )

        return QualityResult(
            passed=passed,
            score=score,
            required_score=required_score,
            setup_tag=setup_tag,
            action=action,
            entry_price=entry_price,
            tp=tp,
            sl=sl,
            reasons=reasons,
        )

    def _compute_tp_sl(self, action: str, entry_price: float) -> tuple[float, float]:
        """진입 방향·가격으로 익절/손절가를 산출한다 (고정 비율 R:R 2:1).

        Args:
            action: "LONG" / "SHORT".
            entry_price: 진입 가격.

        Returns:
            (tp, sl). action 이 LONG/SHORT 가 아니면 (entry, entry).
        """
        if action == "LONG":
            return (
                round(entry_price * (1 + self.tp_pct), 8),
                round(entry_price * (1 - self.sl_pct), 8),
            )
        if action == "SHORT":
            return (
                round(entry_price * (1 - self.tp_pct), 8),
                round(entry_price * (1 + self.sl_pct), 8),
            )
        logger.warning("[QualityGate] 알 수 없는 action=%r — tp/sl=entry", action)
        return entry_price, entry_price

    @staticmethod
    def _blocked(
        required_score: int, entry_price: float, reasons: list[str]
    ) -> QualityResult:
        """차단 QualityResult 생성 (C_DANGER 등)."""
        return QualityResult(
            passed=False,
            score=0,
            required_score=required_score,
            setup_tag="",
            action="",
            entry_price=entry_price,
            tp=entry_price,
            sl=entry_price,
            reasons=reasons,
        )
