"""Phase D-2 — 델타중립 캐리 PnL 분해 단위테스트 (순수·오프라인).

검정: funding income 부호/크기, basis PnL(델타중립 상쇄·베이시스 축소 이득),
비용 >0, 일별 분해 합 일관성, 가격 가드, **계좌/주문/네트워크 API 무참조(TIER-1)**.
"""

from pathlib import Path

import pytest

from backtesting.carry_model import (
    CarryCosts,
    basis_pnl,
    carry_day,
    entry_exit_cost,
    funding_income,
    rebalance_cost,
)


def test_funding_income_short_receives_when_positive():
    # funding>0 → LONG 이 SHORT 에 지급 → 우리(perp SHORT) 수취(+)
    assert funding_income(10_000, 0.0003) == pytest.approx(3.0)
    # funding<0 → 지급(−)
    assert funding_income(10_000, -0.0002) == pytest.approx(-2.0)
    assert funding_income(10_000, 0.0) == 0.0


def test_basis_pnl_delta_neutral_cancels():
    # spot/perp 동일 이동(코인 qty 동일) → basis 0 (완전 델타중립)
    spot_leg, perp_leg, basis = basis_pnl(1.0, 100, 110, 100, 110)
    assert spot_leg == pytest.approx(10.0)
    assert perp_leg == pytest.approx(-10.0)
    assert basis == pytest.approx(0.0)


def test_basis_pnl_short_gains_when_premium_narrows():
    # spot 100→100, perp 101→100 (프리미엄 1→0 축소) → perp short 이득
    spot_leg, perp_leg, basis = basis_pnl(1.0, 100, 100, 101, 100)
    assert spot_leg == pytest.approx(0.0)
    assert perp_leg == pytest.approx(1.0)
    assert basis == pytest.approx(1.0)


def test_basis_pnl_short_loses_when_premium_widens():
    # perp 100→102, spot 100→100 (프리미엄 확대) → short 손실
    _, perp_leg, basis = basis_pnl(1.0, 100, 100, 100, 102)
    assert perp_leg == pytest.approx(-2.0)
    assert basis == pytest.approx(-2.0)


def test_costs_strictly_positive_and_zero_when_flat():
    c = CarryCosts()
    sc, pc = rebalance_cost(10_000, 0.03, 0.03, c)
    assert sc > 0 and pc > 0
    assert entry_exit_cost(10_000, c) > 0
    # 무변동 → rebalance turnover 0
    sc0, pc0 = rebalance_cost(10_000, 0.0, 0.0, c)
    assert sc0 == 0.0 and pc0 == 0.0


def test_carry_day_decomposition_consistency():
    c = CarryCosts()
    d = carry_day(10_000, 100, 102, 100, 101, 0.0003, c)
    assert d.basis_pnl == pytest.approx(d.spot_leg + d.perp_leg)   # basis = 두 leg 합
    assert d.net == pytest.approx(d.basis_pnl + d.funding - d.rebal_cost)
    assert d.funding > 0          # funding>0 수취
    assert d.rebal_cost > 0       # 가격 변동 → rebalance 비용


def test_carry_day_rejects_nonpositive_price():
    with pytest.raises(ValueError):
        carry_day(10_000, 0.0, 100, 100, 100, 0.0, CarryCosts())


def test_no_account_or_order_access_in_module():
    """carry_model 은 순수 오프라인 — 계좌/주문/spot/네트워크 API 참조 0 (TIER-1 보장)."""
    src = Path(__file__).resolve().parent.parent / "backtesting" / "carry_model.py"
    text = src.read_text(encoding="utf-8")
    forbidden = [
        "futures_account", "get_account", "create_order", "futures_create_order",
        "get_klines", "futures_klines", "import requests", "Client(",
        "api_key", "withdraw", "get_asset_balance",
    ]
    hits = [w for w in forbidden if w in text]
    assert hits == [], f"carry_model 에 금지 API 참조 발견: {hits}"
