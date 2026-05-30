"""
sizing/dynamic_sizer.py
=====================================================================
DynamicPositionSizer — Kelly 기반 동적 포지션 사이징

Kelly 공식:
  K* = (W × G - L) / (G × L) = W/L - (1-W)/G
  여기서 W=승률, L=손실 R(양수), G=수익 R(양수)

위험:
  - 승률·R 추정 오차 시 K가 음수가 되거나 과대 사이즈 산출
  - 표본 부족 시 K 신뢰도 매우 낮음

v3.1 안전장치:
  - 자본 <= $1,000: Quarter-Kelly (K/4) 사용
  - 자본 $1,000~$5,000: Half-Kelly (K/2)
  - 자본 > $5,000: Half-Kelly + 점진적 완화
  - 레짐별 상한 cap
  - 신뢰도(confidence) 반영

명세서: docs/SPEC_v3.1.md §8-3
v3.1.2 정정 (3건, 사용자 승인 — 세션 4):
  - §8-3-2 kelly_fraction_for_capital 경계값 `< 1000` → `<= 1000`
  - §8-3-2 Kelly 음수 분기 _zero_result 인자 순서 버그 수정
  - §8-3-3 #2 본문 "~6%" 추정치는 cap 미반영 → 실제 10% (코드 변경 없음)
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    """포지션 사이징 결과.

    Attributes:
        size_pct: 자본 대비 포지션 크기 (%). 0~12.
        size_usdt: 명목 가치 (USDT).
        kelly_raw: 원본 Kelly 값 (fraction 적용 전). 음수 가능.
        kelly_fraction_used: 적용된 Kelly fraction (0.25 또는 0.50).
        regime_cap_pct: 레짐별 상한 (%).
        capital_cap_pct: 자본 규모별 절대 상한 (%).
        confidence_factor: 신뢰도 반영 계수 (>= confidence_min_factor).
        final_cap_pct: 실제 적용된 최종 상한 (%).
        reason: 산출 근거 텍스트.
    """

    size_pct: float                  # 자본 대비 %
    size_usdt: float                 # 명목 가치
    kelly_raw: float                 # 원본 Kelly 값
    kelly_fraction_used: float       # 1/4 or 1/2
    regime_cap_pct: float
    capital_cap_pct: float
    confidence_factor: float
    final_cap_pct: float
    reason: str


class DynamicPositionSizer:
    """
    포지션 사이즈 계산.

    사용:
        sizer = DynamicPositionSizer()
        result = sizer.calculate(
            capital=1000,
            win_rate=0.55,
            avg_win_R=2.0, avg_loss_R=1.0,
            regime="TREND_UP",
            confidence=0.8,
        )
        position_usdt = result.size_usdt
    """

    # 레짐별 자본 % 상한
    REGIME_CAPS = {
        "TREND_UP":   0.10,
        "TREND_DOWN": 0.10,
        "RANGING":    0.06,
        "UNCERTAIN":  0.05,
        "HIGH_VOL":   0.00,
    }

    # 자본 규모별 Kelly fraction
    @staticmethod
    def kelly_fraction_for_capital(capital: float) -> float:
        """자본 규모에 따른 Kelly fraction 결정.

        Args:
            capital: 가용 자본 (USDT).

        Returns:
            float: 0.25 (Quarter-Kelly, 자본 <= $1,000) 또는
                   0.50 (Half-Kelly, 그 이상). Full-Kelly는 절대 사용 안 함.
        """
        # v3.1.2 정정: §8-3-2 경계값 `< 1000` → `<= 1000`.
        # 사유: §8-3-1 책임 명세 + §8-3-3 #1 시나리오 모두 $1,000을
        #       Quarter-Kelly로 처리. 정확히 $1,000 자본 사용자에게
        #       Half-Kelly는 다소 공격적이므로 보수적으로 정정.
        if capital <= 1000:
            return 0.25     # Quarter-Kelly (소액 매우 보수적)
        elif capital < 5000:
            return 0.50     # Half-Kelly
        elif capital < 20000:
            return 0.50     # Half-Kelly 유지
        else:
            return 0.50     # Half-Kelly 상한 (절대 Full-Kelly 사용하지 않음)

    # 자본 규모별 절대 상한 (%)
    @staticmethod
    def capital_cap_for_capital(capital: float) -> float:
        """자본 규모에 따른 절대 상한 (자본 대비 비율) 결정.

        Args:
            capital: 가용 자본 (USDT).

        Returns:
            float: 자본 대비 최대 포지션 비율 (0.05~0.12).
        """
        if capital < 1000:
            return 0.05     # 최대 5% (= $50 미만)
        elif capital < 3000:
            return 0.08
        elif capital < 10000:
            return 0.10
        else:
            return 0.12

    def __init__(
        self,
        min_size_pct: float = 0.02,        # 최소 2%
        absolute_max_pct: float = 0.12,    # 절대 상한 12%
        confidence_min_factor: float = 0.5,  # 신뢰도 낮을 때 최소 0.5배까지 축소
    ):
        """DynamicPositionSizer 초기화.

        Args:
            min_size_pct: 최소 포지션 비율 (기본 0.02 = 2%).
            absolute_max_pct: 절대 상한 비율 (기본 0.12 = 12%).
            confidence_min_factor: 신뢰도 반영 시 하한 계수 (기본 0.5).
        """
        self.min_size_pct = min_size_pct
        self.absolute_max_pct = absolute_max_pct
        self.confidence_min_factor = confidence_min_factor

    def calculate(
        self,
        capital: float,
        win_rate: float,                 # 0~1
        avg_win_R: float,                # 평균 수익 R (양수)
        avg_loss_R: float,               # 평균 손실 R (양수)
        regime: str,
        confidence: float = 0.7,         # 레짐 신뢰도 0~1
        sample_count: int = 0,           # 승률 표본 수 (0이면 매우 보수적)
        entry_price: float | None = None,      # A1 [2.2]: risk-cap 입력 (선택)
        stop_loss: float | None = None,        # A1 [2.2]: 손절가 (선택)
        risk_per_trade_pct: float | None = None,  # A1 [2.2]: 거래당 리스크 비율 (선택)
    ) -> SizingResult:
        """포지션 사이즈 계산.

        Args:
            capital: 가용 자본 (USDT). 0 이하면 사이즈 0 반환.
            win_rate: 승률 (0~1). 범위 밖이면 0.5로 보정.
            avg_win_R: 평균 수익 R (양수). 비정상이면 2.0으로 보정.
            avg_loss_R: 평균 손실 R (양수). 비정상이면 1.0으로 보정.
            regime: 시장 레짐 문자열. "HIGH_VOL"이면 진입 차단.
            confidence: 레짐 신뢰도 (0~1). confidence_min_factor 미만이면 상향.
            sample_count: 승률 표본 수. 20/50 미만이면 단계적 축소.
            entry_price: 진입가. stop_loss/risk_per_trade_pct 와 함께 주어지면
                A1 [2.2] risk-based notional cap 을 추가 적용한다 (사이즈 축소만).
            stop_loss: 손절가. entry_price 와의 거리로 거래당 리스크를 산정한다.
            risk_per_trade_pct: 거래당 허용 리스크 비율 (예: 0.005 = 0.5%).
                세 인자 중 하나라도 None 이면 risk-cap 은 비활성(기존 동작 불변).

        Returns:
            SizingResult: 사이징 결과 (size_pct, size_usdt, 근거 등).
        """
        reasons = []

        # ── 1. 입력 검증 ──
        if capital <= 0:
            logger.warning("사이징 차단: capital <= 0 (capital=%s)", capital)
            return self._zero_result(0, 0, 0, 0, 0, "capital <= 0")
        if regime == "HIGH_VOL":
            logger.info("사이징 차단: HIGH_VOL 레짐 진입 차단 (regime=%s)", regime)
            return self._zero_result(0, 0, 0, 0, 0, "HIGH_VOL 진입 차단")
        if win_rate <= 0 or win_rate >= 1:
            logger.warning("win_rate 비정상 (%.4f) → 0.5 사용", win_rate)
            win_rate = 0.5
            reasons.append("win_rate 비정상 → 0.5 사용")
        if avg_win_R <= 0 or avg_loss_R <= 0:
            logger.warning(
                "R 비정상 (win_R=%.4f, loss_R=%.4f) → 2.0/1.0 보정",
                avg_win_R, avg_loss_R,
            )
            avg_win_R = avg_win_R if avg_win_R > 0 else 2.0
            avg_loss_R = avg_loss_R if avg_loss_R > 0 else 1.0
            reasons.append("R 비정상 → 2.0/1.0 사용")

        # ── 2. Kelly 계산 ──
        # K = W/L - (1-W)/G  (Edward Thorp 표기)
        kelly_raw = win_rate / avg_loss_R - (1 - win_rate) / avg_win_R

        if kelly_raw <= 0:
            logger.info(
                "사이징 차단: Kelly 음수/0 (W=%.2f, R=%.2f/%.2f, K=%.4f)",
                win_rate, avg_win_R, avg_loss_R, kelly_raw,
            )
            # v3.1.2 정정: §8-3-2 Kelly 음수 분기 인자 순서 버그 수정.
            # 기존: _zero_result(0, kelly_raw, ...) → kelly_raw 필드에 0,
            #       kelly_fraction_used 필드에 원본 Kelly가 들어가 의미가 뒤바뀜.
            # 수정: _zero_result(kelly_raw, 0, ...) → kelly_raw 필드에 원본 Kelly.
            return self._zero_result(kelly_raw, 0, 0, 0, 0,
                f"Kelly 음수 (W={win_rate:.2f}, R={avg_win_R:.2f}/{avg_loss_R:.2f}) — 진입 불가")

        # ── 3. Kelly fraction 적용 ──
        k_frac = self.kelly_fraction_for_capital(capital)
        fractional_kelly = kelly_raw * k_frac

        # ── 4. 표본 부족 시 추가 축소 ──
        sample_penalty = 1.0
        if sample_count < 20:
            sample_penalty = 0.5
            logger.info("표본 부족 (n=%d<20): 사이즈 50%% 축소 적용", sample_count)
            reasons.append(f"표본 부족 (n={sample_count}<20), 사이즈 50% 축소")
        elif sample_count < 50:
            sample_penalty = 0.75
            logger.info("표본 부족 (n=%d<50): 사이즈 75%% 축소 적용", sample_count)
            reasons.append(f"표본 부족 (n={sample_count}<50), 사이즈 75% 축소")

        adjusted = fractional_kelly * sample_penalty

        # ── 5. 신뢰도 반영 ──
        conf_factor = max(self.confidence_min_factor, confidence)
        if conf_factor > confidence:
            logger.info(
                "신뢰도 하한 적용: confidence=%.2f → %.2f", confidence, conf_factor,
            )
        adjusted *= conf_factor

        # ── 6. 캡 적용 ──
        regime_cap = self.REGIME_CAPS.get(regime, 0.05)
        capital_cap = self.capital_cap_for_capital(capital)
        final_cap = min(regime_cap, capital_cap, self.absolute_max_pct)

        size_pct = max(self.min_size_pct, min(adjusted, final_cap))
        size_usdt = capital * size_pct

        if abs(size_pct - final_cap) < 1e-6:
            reasons.append(f"cap 적용: {final_cap*100:.1f}%")
        if abs(size_pct - self.min_size_pct) < 1e-6:
            reasons.append(f"최소값 적용: {self.min_size_pct*100:.1f}%")

        # ── A1 [2.2]: risk-per-trade 기반 notional cap (손절거리 반영) ──
        # notional ≤ risk_pct × capital × entry / |entry−sl| 를 기존 cap 과 min 결합.
        # hard ceiling 이므로 min_size 하한보다 낮아질 수 있다(리스크 우선). 사이즈는
        # 줄어들기만 한다. 세 인자 미제공 시 비활성(기존 동작 100% 불변).
        if (
            entry_price is not None and stop_loss is not None
            and risk_per_trade_pct is not None
            and risk_per_trade_pct > 0 and entry_price > 0
        ):
            stop_dist = abs(entry_price - stop_loss)
            # zero-division 가드: sl==entry(또는 극소 거리)면 risk-cap 생략(폭발 방지).
            if stop_dist <= entry_price * 1e-9:
                logger.warning(
                    "[Sizer] 손절거리 0/극소 (entry=%s, sl=%s) → risk-cap 생략",
                    entry_price, stop_loss,
                )
                reasons.append("손절거리 0 → risk-cap 생략")
            else:
                risk_cap_usdt = risk_per_trade_pct * capital * entry_price / stop_dist
                if risk_cap_usdt < size_usdt:
                    size_usdt = risk_cap_usdt
                    size_pct = size_usdt / capital   # min_size 하한 아래로도 가능
                    logger.info(
                        "[Sizer] risk-cap 적용: risk %.2f%%/trade → notional $%.2f",
                        risk_per_trade_pct * 100, size_usdt,
                    )
                    reasons.append(f"risk cap {risk_per_trade_pct*100:.2f}%/trade 적용")

        return SizingResult(
            size_pct=round(size_pct * 100, 3),
            size_usdt=round(size_usdt, 2),
            kelly_raw=round(kelly_raw, 4),
            kelly_fraction_used=k_frac,
            regime_cap_pct=round(regime_cap * 100, 2),
            capital_cap_pct=round(capital_cap * 100, 2),
            confidence_factor=round(conf_factor, 3),
            final_cap_pct=round(final_cap * 100, 2),
            reason="; ".join(reasons) if reasons else "정상 산출",
        )

    def _zero_result(self, k_raw, k_used, r_cap, c_cap, conf, reason) -> SizingResult:
        """사이즈 0 결과 생성 (진입 차단 시 사용).

        Args:
            k_raw: kelly_raw 필드 값.
            k_used: kelly_fraction_used 필드 값.
            r_cap: regime_cap_pct 필드 값.
            c_cap: capital_cap_pct 필드 값.
            conf: confidence_factor 필드 값.
            reason: 차단 사유 텍스트.

        Returns:
            SizingResult: size_pct=0, size_usdt=0인 결과.
        """
        return SizingResult(
            size_pct=0.0, size_usdt=0.0,
            kelly_raw=k_raw, kelly_fraction_used=k_used,
            regime_cap_pct=r_cap, capital_cap_pct=c_cap,
            confidence_factor=conf, final_cap_pct=0,
            reason=reason,
        )
