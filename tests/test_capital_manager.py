"""
tests/test_capital_manager.py
=====================================================================
CapitalManager 단위 테스트 — 부록 E-2-4의 8개 시나리오.

binance_client는 unittest.mock.MagicMock으로 mock.
futures_account()는 부록 명세의 반환 형식 사용:
    {"totalWalletBalance": "1000", "totalMarginBalance": "1030",
     "availableBalance": "970", "totalPositionInitialMargin": "30",
     "totalUnrealizedProfit": "30"}

pytest asyncio_mode=auto 이므로 async def test_* 직접 사용.
=====================================================================
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from data.capital_manager import CapitalManager, CapitalSnapshot

ACCOUNT_RESPONSE = {
    "totalWalletBalance": "1000",
    "totalMarginBalance": "1030",
    "availableBalance": "970",
    "totalPositionInitialMargin": "30",
    "totalUnrealizedProfit": "30",
}


def _make_client(response: dict | None = None) -> MagicMock:
    """futures_account()가 주어진 dict를 반환하는 mock 클라이언트."""
    client = MagicMock()
    client.futures_account.return_value = response or dict(ACCOUNT_RESPONSE)
    return client


# ── 시나리오 1: 정상 응답 → 모든 필드 파싱 ──
async def test_normal_response_parses_all_fields():
    cm = CapitalManager(_make_client())
    snap = await cm.get_snapshot()

    assert isinstance(snap, CapitalSnapshot)
    assert snap.wallet_balance == 1000.0
    assert snap.margin_balance == 1030.0
    assert snap.available_balance == 970.0
    assert snap.locked_margin == 30.0
    assert snap.unrealized_pnl == 30.0
    # margin_utilization_pct = 30 / 1000 * 100 = 3.0
    assert snap.margin_utilization_pct == 3.0


# ── 시나리오 2: 캐시 동작 (TTL 10초) — 2번 호출 → API 1번만 ──
async def test_cache_within_ttl_calls_api_once():
    client = _make_client()
    cm = CapitalManager(client, cache_ttl_seconds=10.0)

    await cm.get_snapshot()
    await cm.get_snapshot()

    assert client.futures_account.call_count == 1


# ── 시나리오 3: force_refresh=True → 캐시 무시 ──
async def test_force_refresh_ignores_cache():
    client = _make_client()
    cm = CapitalManager(client, cache_ttl_seconds=10.0)

    await cm.get_snapshot()
    await cm.get_snapshot(force_refresh=True)

    assert client.futures_account.call_count == 2


# ── 시나리오 4: 초기 자본 기록 (최초 호출 시) ──
async def test_initial_capital_recorded_on_first_call():
    cm = CapitalManager(_make_client())
    assert cm.get_initial_capital() is None

    snap = await cm.get_snapshot()
    assert cm.get_initial_capital() == snap.wallet_balance == 1000.0

    # 이후 호출에서 잔고가 변해도 초기 자본은 고정
    cm.binance.futures_account.return_value = {**ACCOUNT_RESPONSE, "totalWalletBalance": "1200"}
    await cm.get_snapshot(force_refresh=True)
    assert cm.get_initial_capital() == 1000.0


# ── 시나리오 5: 일일 시작 잔고 갱신 (날짜 바뀌면) ──
async def test_daily_start_balance_updates_when_date_changes():
    client = _make_client()
    cm = CapitalManager(client, cache_ttl_seconds=10.0)

    await cm.get_snapshot()
    assert cm.get_daily_start_capital() == 1000.0
    first_date = cm._daily_start_date
    assert first_date is not None

    # datetime 로컬 import 패치는 취약하므로 상태를 직접 과거로 조작 +
    # 잔고 변경 후 force_refresh → _update_daily_start가 갱신해야 함
    cm._daily_start_date = "2020-01-01"
    client.futures_account.return_value = {**ACCOUNT_RESPONSE, "totalWalletBalance": "1150"}
    await cm.get_snapshot(force_refresh=True)

    assert cm.get_daily_start_capital() == 1150.0
    assert cm._daily_start_date == first_date  # 다시 오늘 날짜로 갱신됨


# ── 시나리오 6: API 실패 + 캐시 있음 → 캐시 반환 (stale 경고) ──
async def test_api_failure_with_cache_returns_stale(caplog):
    client = _make_client()
    cm = CapitalManager(client, cache_ttl_seconds=10.0)

    first = await cm.get_snapshot()  # 캐시 채움

    client.futures_account.side_effect = RuntimeError("network down")
    with caplog.at_level(logging.WARNING, logger="data.capital_manager"):
        stale = await cm.get_snapshot(force_refresh=True)

    assert stale is first  # 동일 캐시 객체 반환
    assert any("stale" in r.message for r in caplog.records)


# ── 시나리오 7: API 실패 + 캐시 없음 → raise ──
async def test_api_failure_without_cache_raises():
    client = _make_client()
    client.futures_account.side_effect = RuntimeError("network down")
    cm = CapitalManager(client)

    with pytest.raises(RuntimeError, match="network down"):
        await cm.get_snapshot()


# ── 시나리오 8: get_spot_balance() → ValueError ──
async def test_get_spot_balance_raises_valueerror():
    cm = CapitalManager(_make_client())
    with pytest.raises(ValueError, match="Spot 잔고 조회 금지"):
        cm.get_spot_balance("BTC")
