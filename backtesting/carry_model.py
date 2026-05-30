"""
backtesting/carry_model.py — 델타중립 캐리 *오프라인* PnL 분해 (순수 함수).

★ 연구 전용 / 순수 계산. **네트워크·계좌 API·spot 주문·파일 I/O 일절 없음.**
  spot long + perp short(코인 qty 매칭) 구조의 일별 손익을 분해한다:
      spot leg / perp short leg / basis PnL / funding income / 거래·rebalance 비용.

  델타중립 캐리는 *라이브 카운터파트가 없다*(라이브 실행은 TIER-1 spot 격리와
  충돌 — Phase D 의 C 판정). 따라서 backtest_engine(방향성 봇) 과의 §4 정합
  대상이 아닌 **독립 오프라인 연구 모듈**이다. 이 모듈은 자산을 만지지 않는다.

부호 규약 (Binance):
  funding_rate > 0  → LONG 이 SHORT 에게 지급 → 우리 구조(perp SHORT)는 *수취*(+).
  funding_rate < 0  → 반대 → 지급(−).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CarryCosts:
    """보수적 비용 가정 (0 으로 두지 않는다). BNB 수수료 할인 미적용."""
    spot_fee: float = 0.001      # spot taker 0.10%
    fut_fee: float = 0.0005      # futures taker 0.05%
    spot_slip: float = 0.0005    # spot spread/slippage proxy 5 bps
    fut_slip: float = 0.0003     # perp spread/slippage proxy 3 bps


@dataclass(frozen=True)
class DayPnL:
    spot_leg: float       # qty*(spot_t1-spot_t)              — 가격 추종(대형, ±)
    perp_leg: float       # -qty*(perp_t1-perp_t)  [short]    — 가격 추종(대형, ∓)
    basis_pnl: float      # spot_leg + perp_leg               — 델타중립 잔차(소형)
    funding: float        # perp_notional * daily_funding_sum — 캐리 원천
    rebal_cost: float     # 일 단위 동일-notional 복원 양다리 비용(≥0)
    net: float            # basis_pnl + funding - rebal_cost  (개시/청산비 별도)


def funding_income(perp_notional: float, daily_funding_sum: float) -> float:
    """perp SHORT 의 일 funding 수취/지급.
    daily_funding_sum = 그 날 8h funding rate 3개의 합. + 면 수취, − 면 지급."""
    return perp_notional * daily_funding_sum


def basis_pnl(qty: float, spot_t: float, spot_t1: float,
              perp_t: float, perp_t1: float):
    """델타중립(코인 qty 동일) spot long + perp short 가격 PnL.
    반환: (spot_leg, perp_leg, basis=spot_leg+perp_leg).
    Δspot==Δperp 면 basis=0(완전 델타중립). perp 가 spot 대비 더 내리면(베이시스 축소)
    short leg 이득 → basis>0."""
    spot_leg = qty * (spot_t1 - spot_t)
    perp_leg = -qty * (perp_t1 - perp_t)
    return spot_leg, perp_leg, spot_leg + perp_leg


def rebalance_cost(notional: float, spot_ret: float, perp_ret: float,
                   costs: CarryCosts):
    """일 단위 *동일 notional* 복원 turnover 비용(양다리, 보수적 상한).
    가격이 r 만큼 움직이면 leg 가치가 notional*(1+r) → notional 로 되돌리는
    turnover = notional*|r|. 비용 = turnover*(fee+slip). 밴드 rebalance 였다면 더 쌌을 것.
    반환: (spot_cost, perp_cost) 둘 다 ≥ 0."""
    spot_c = notional * abs(spot_ret) * (costs.spot_fee + costs.spot_slip)
    perp_c = notional * abs(perp_ret) * (costs.fut_fee + costs.fut_slip)
    return spot_c, perp_c


def entry_exit_cost(notional: float, costs: CarryCosts) -> float:
    """1회 개시(또는 청산) 양다리 full-notional 비용 (fee+slip), ≥ 0."""
    spot_c = notional * (costs.spot_fee + costs.spot_slip)
    perp_c = notional * (costs.fut_fee + costs.fut_slip)
    return spot_c + perp_c


def carry_day(notional: float, spot_t: float, spot_t1: float,
              perp_t: float, perp_t1: float, daily_funding_sum: float,
              costs: CarryCosts) -> DayPnL:
    """보유 중 하루치 델타중립 캐리 손익 분해 (개시/청산비 제외).
    notional = spot leg 목표 USD. qty = notional/spot_t (코인 매칭 → perp short 동일 qty)."""
    if spot_t <= 0 or perp_t <= 0:
        raise ValueError("price must be positive")
    qty = notional / spot_t
    spot_leg, perp_leg, basis = basis_pnl(qty, spot_t, spot_t1, perp_t, perp_t1)
    perp_notional = qty * perp_t
    fund = funding_income(perp_notional, daily_funding_sum)
    spot_ret = spot_t1 / spot_t - 1.0
    perp_ret = perp_t1 / perp_t - 1.0
    sc, pc = rebalance_cost(notional, spot_ret, perp_ret, costs)
    rebal = sc + pc
    net = basis + fund - rebal
    return DayPnL(spot_leg=spot_leg, perp_leg=perp_leg, basis_pnl=basis,
                  funding=fund, rebal_cost=rebal, net=net)
