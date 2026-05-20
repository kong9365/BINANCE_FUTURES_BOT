"""
strategy/cost_guard.py
=====================================================================
CostGuard — 비용 차감 기대값 게이트

E(trade) = W × reward - (1-W) × risk - total_cost > 0
이 부등식이 성립하지 않으면 어떤 신호도 진입 불가.

v3.0과 동일한 핵심 로직, v3.1에서 추가:
  - pair_tier 인자로 페어별 슬리피지 차등
  - default_win_rate 데이터 부족 시 보수적 적용

명세서: docs/SPEC_v3.1.md §8-2
의존성: 없음 (외부 호출 없음)
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CostGuardResult:
    """CostGuard.check() 결과 컨테이너.

    Attributes:
        passed: 진입 가능 여부 (기대값 > min_expected_value 일 때 True).
        expected_value: 비용 차감 후 기대값 ($).
        estimated_cost: 추정 왕복 비용 ($).
        assumed_win_rate: 계산에 사용한 승률.
        reward: TP 도달 시 순이익 ($, 비용 차감 전).
        risk: SL 도달 시 순손실 ($, 비용 차감 전).
        pair_tier: 페어 Tier (1/2/3).
        slippage_assumed: 적용한 편도 슬리피지.
        reason: 통과/차단 사유 문자열.
    """

    passed: bool
    expected_value: float           # 기대값 ($)
    estimated_cost: float           # 왕복 비용 ($)
    assumed_win_rate: float
    reward: float                   # TP 도달 시 순이익 ($)
    risk: float                     # SL 도달 시 순손실 ($)
    pair_tier: int
    slippage_assumed: float
    reason: str


class CostGuard:
    """진입 전 비용 차감 기대값 검사.

    사용:
        guard = CostGuard()
        guard.update_win_rates({"trend_pullback_long": 0.55})
        result = guard.check(
            setup_tag="trend_pullback_long",
            entry_price=50000, tp_price=51000, sl_price=49600,
            position_usdt=100, action="LONG", pair_tier=1,
        )
        if not result.passed:
            skip_trade(result.reason)
    """

    # Tier별 기본 슬리피지 (편도)
    DEFAULT_SLIPPAGE_BY_TIER: Dict[int, float] = {
        1: 0.00050,   # Tier 1 (BTC/ETH): 0.05%
        2: 0.00100,   # Tier 2 (상위 알트): 0.10%
        3: 0.00150,   # Tier 3 (톱 20): 0.15%
    }

    def __init__(
        self,
        # 수수료
        taker_fee_rate: float = 0.00045,        # 0.045% per side (BNB 10% 할인)
        maker_fee_rate: float = 0.00018,        # 0.018% per side (BNB 10% 할인)
        slippage_by_tier: Optional[Dict[int, float]] = None,
        # 승률 보수성
        default_win_rate: float = 0.45,         # 데이터 부족 시 매우 보수적
        # 기대값 임계
        min_expected_value: float = 0.0,        # 0이면 양수만 통과 (엄격)
        # 사용할 수수료 면 (Post-Only 우선이면 진입은 maker, 청산은 taker)
        entry_is_maker: bool = True,
        exit_is_taker: bool = True,
    ) -> None:
        """CostGuard 초기화.

        Args:
            taker_fee_rate: Taker 수수료율 (편도). 기본 0.045% (BNB 할인 적용).
            maker_fee_rate: Maker 수수료율 (편도). 기본 0.018% (BNB 할인 적용).
            slippage_by_tier: Tier별 편도 슬리피지 맵. None이면 기본값 사용.
            default_win_rate: 실측 데이터 부족 시 사용할 보수적 승률.
            min_expected_value: 통과 기준 기대값 임계 ($). 0이면 양수만 통과.
            entry_is_maker: 진입 주문이 maker 면인지 여부.
            exit_is_taker: 청산 주문이 taker 면인지 여부.
        """
        self.taker_fee_rate = taker_fee_rate
        self.maker_fee_rate = maker_fee_rate
        self.slippage_by_tier = slippage_by_tier or dict(self.DEFAULT_SLIPPAGE_BY_TIER)
        self.default_win_rate = default_win_rate
        self.min_expected_value = min_expected_value
        self.entry_is_maker = entry_is_maker
        self.exit_is_taker = exit_is_taker

        self._win_rates: Dict[str, float] = {}
        self._win_rate_sample_counts: Dict[str, int] = {}

    def update_win_rates(
        self,
        win_rates: Dict[str, float],
        sample_counts: Optional[Dict[str, int]] = None,
    ) -> None:
        """ExpectancyAnalyzer 결과로 setup_tag별 승률 업데이트.

        Args:
            win_rates: {setup_tag: 승률(0.0~1.0)} 맵.
            sample_counts: {setup_tag: 표본 수} 맵. None이면 표본 수 미갱신
                (이 경우 해당 tag는 데이터 부족으로 간주되어 default 사용).

        Returns:
            None. 내부 상태(_win_rates, _win_rate_sample_counts)를 갱신.
        """
        # ── 입력 검증 ──
        for tag, wr in win_rates.items():
            if not 0.0 <= wr <= 1.0:
                logger.warning(
                    "[CostGuard] update_win_rates: 비정상 승률 무시 "
                    "(setup_tag=%s, win_rate=%s, 허용 범위 0.0~1.0)",
                    tag, wr,
                )
                continue
            self._win_rates[tag] = wr

        if sample_counts:
            for tag, n in sample_counts.items():
                if n < 0:
                    logger.warning(
                        "[CostGuard] update_win_rates: 음수 표본 수 무시 "
                        "(setup_tag=%s, sample_count=%s)",
                        tag, n,
                    )
                    continue
                self._win_rate_sample_counts[tag] = n

    def check(
        self,
        setup_tag: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        position_usdt: float,        # 명목 가치 (레버리지 적용 전)
        action: str,                 # "LONG" | "SHORT"
        pair_tier: int = 2,          # 1, 2, 3
        min_samples: int = 10,       # 이 미만이면 default_win_rate 사용
    ) -> CostGuardResult:
        """비용 차감 후 기대값을 계산하고 진입 가능 여부를 반환.

        Args:
            setup_tag: 셋업 식별 태그. 실시간 승률 조회 키.
            entry_price: 진입 가격.
            tp_price: 익절(TP) 가격.
            sl_price: 손절(SL) 가격.
            position_usdt: 포지션 명목 가치 ($, 레버리지 적용 전).
            action: 방향. "LONG" 또는 "SHORT".
            pair_tier: 페어 Tier (1/2/3). 슬리피지 차등에 사용.
            min_samples: 실측 승률을 신뢰할 최소 표본 수. 미만이면 default 사용.

        Returns:
            CostGuardResult. passed=True이면 기대값이 임계를 초과해 진입 가능,
            False이면 입력 오류 또는 음의 기대값으로 진입 불가.
        """
        # ── 입력 검증 ──
        if entry_price <= 0 or position_usdt <= 0:
            logger.warning(
                "[CostGuard] 잘못된 입력값 (setup_tag=%s, entry_price=%s, "
                "position_usdt=%s)",
                setup_tag, entry_price, position_usdt,
            )
            return CostGuardResult(
                passed=False, expected_value=0, estimated_cost=0,
                assumed_win_rate=0, reward=0, risk=0,
                pair_tier=pair_tier, slippage_assumed=0,
                reason="잘못된 입력값",
            )
        if action not in ("LONG", "SHORT"):
            logger.warning(
                "[CostGuard] 잘못된 action (setup_tag=%s, action=%r, "
                "허용값 'LONG'|'SHORT')",
                setup_tag, action,
            )
            return CostGuardResult(
                passed=False, expected_value=0, estimated_cost=0,
                assumed_win_rate=0, reward=0, risk=0,
                pair_tier=pair_tier, slippage_assumed=0,
                reason=f"잘못된 action: {action}",
            )

        if pair_tier not in self.slippage_by_tier:
            logger.warning(
                "[CostGuard] 미등록 pair_tier=%s — 최대 슬리피지(0.15%%)로 대체 "
                "(setup_tag=%s)",
                pair_tier, setup_tag,
            )

        # ── 1. 손익률 계산 ──
        if action == "LONG":
            reward_pct = (tp_price - entry_price) / entry_price
            risk_pct = (entry_price - sl_price) / entry_price
        else:  # SHORT
            reward_pct = (entry_price - tp_price) / entry_price
            risk_pct = (sl_price - entry_price) / entry_price

        if reward_pct <= 0 or risk_pct <= 0:
            logger.warning(
                "[CostGuard] 비합리적 TP/SL (setup_tag=%s, action=%s, "
                "entry=%s, tp=%s, sl=%s, reward%%=%.4f, risk%%=%.4f)",
                setup_tag, action, entry_price, tp_price, sl_price,
                reward_pct, risk_pct,
            )
            return CostGuardResult(
                passed=False, expected_value=0, estimated_cost=0,
                assumed_win_rate=0, reward=0, risk=0,
                pair_tier=pair_tier, slippage_assumed=0,
                reason=f"비합리적 TP/SL: reward%={reward_pct:.4f}, risk%={risk_pct:.4f}",
            )

        reward_usd = reward_pct * position_usdt
        risk_usd = risk_pct * position_usdt

        # ── 2. 왕복 비용 ──
        entry_fee_rate = self.maker_fee_rate if self.entry_is_maker else self.taker_fee_rate
        exit_fee_rate = self.taker_fee_rate if self.exit_is_taker else self.maker_fee_rate
        slip_per_side = self.slippage_by_tier.get(pair_tier, 0.00150)

        cost_entry = (entry_fee_rate + slip_per_side) * position_usdt
        cost_exit = (exit_fee_rate + slip_per_side) * position_usdt
        total_cost = cost_entry + cost_exit

        # ── 3. 승률 결정 ──
        n_samples = self._win_rate_sample_counts.get(setup_tag, 0)
        if n_samples < min_samples:
            win_rate = self.default_win_rate
            win_rate_source = f"default ({n_samples}<{min_samples} samples)"
        else:
            win_rate = self._win_rates.get(setup_tag, self.default_win_rate)
            win_rate_source = f"empirical (n={n_samples})"

        # ── 4. 기대값 계산 ──
        # 비용을 양쪽 결과에 절반씩 차감하는 보수적 가정
        net_reward = reward_usd - total_cost / 2
        net_risk = risk_usd + total_cost / 2
        expected_value = win_rate * net_reward - (1 - win_rate) * net_risk

        passed = expected_value > self.min_expected_value

        reason_prefix = "통과" if passed else "차단"
        reason = (
            f"[{reason_prefix}] EV={expected_value:.3f}$ "
            f"(W={win_rate:.2%} [{win_rate_source}], "
            f"reward=${reward_usd:.3f}, risk=${risk_usd:.3f}, "
            f"cost=${total_cost:.3f}, tier={pair_tier})"
        )

        if passed:
            logger.info("[CostGuard] %s %s", setup_tag, reason)
        else:
            logger.info("[CostGuard] %s 차단: %s", setup_tag, reason)

        return CostGuardResult(
            passed=passed,
            expected_value=round(expected_value, 4),
            estimated_cost=round(total_cost, 4),
            assumed_win_rate=round(win_rate, 4),
            reward=round(reward_usd, 4),
            risk=round(risk_usd, 4),
            pair_tier=pair_tier,
            slippage_assumed=round(slip_per_side, 6),
            reason=reason,
        )

    def get_min_winrate_for_passing(
        self,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        position_usdt: float,
        action: str,
        pair_tier: int = 2,
    ) -> float:
        """이 거래가 통과하려면 필요한 최소 승률을 계산 (디버깅용).

        손익분기 조건 W × reward - (1-W) × risk = 0 을 풀어
        W = risk / (reward + risk) 를 반환한다.

        Args:
            entry_price: 진입 가격.
            tp_price: 익절(TP) 가격.
            sl_price: 손절(SL) 가격.
            position_usdt: 포지션 명목 가치 ($).
            action: 방향. "LONG" 또는 "SHORT".
            pair_tier: 페어 Tier (1/2/3). 슬리피지 차등에 사용.

        Returns:
            손익분기에 필요한 최소 승률 (0.0~1.0).
            입력이 비합리적이면(reward/risk ≤ 0) 1.0 반환.
        """
        if action not in ("LONG", "SHORT"):
            logger.warning(
                "[CostGuard] get_min_winrate_for_passing: 잘못된 action=%r — "
                "1.0 반환", action,
            )
            return 1.0
        if entry_price <= 0 or position_usdt <= 0:
            logger.warning(
                "[CostGuard] get_min_winrate_for_passing: 잘못된 입력값 "
                "(entry_price=%s, position_usdt=%s) — 1.0 반환",
                entry_price, position_usdt,
            )
            return 1.0

        if action == "LONG":
            r_pct = (tp_price - entry_price) / entry_price
            x_pct = (entry_price - sl_price) / entry_price
        else:
            r_pct = (entry_price - tp_price) / entry_price
            x_pct = (sl_price - entry_price) / entry_price

        if r_pct <= 0 or x_pct <= 0:
            logger.warning(
                "[CostGuard] get_min_winrate_for_passing: 비합리적 TP/SL "
                "(reward%%=%.4f, risk%%=%.4f) — 1.0 반환",
                r_pct, x_pct,
            )
            return 1.0

        slip = self.slippage_by_tier.get(pair_tier, 0.00150)
        fee_total = (self.maker_fee_rate + self.taker_fee_rate) * position_usdt
        cost = fee_total + 2 * slip * position_usdt

        reward = r_pct * position_usdt - cost / 2
        risk = x_pct * position_usdt + cost / 2

        # W × reward - (1-W) × risk = 0 → W = risk / (reward + risk)
        return risk / (reward + risk) if (reward + risk) > 0 else 1.0
