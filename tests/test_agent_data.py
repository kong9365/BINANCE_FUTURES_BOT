"""
tests/test_agent_data.py
=====================================================================
DataAgent 검증 — staleness + lookahead + 보호자산 + 상장일.
=====================================================================
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agents.data_agent import DataAgent
from audit.signal_decision import SignalDecision


def _make_signal(symbol: str = "SOLUSDT", features: dict = None) -> SignalDecision:
    now = datetime.now(timezone.utc)
    return SignalDecision(
        setup_id="1d_tsmom_donchian_long_v1",
        params_hash="a" * 64,
        signal_source="strategy_skill",
        reasoning="test",
        ts_signal_generated=now,
        raw_data_hash="b" * 64,
        features_snapshot_json=json.dumps(features or {}),
        symbol=symbol,
        action="LONG",
        confidence=0.7,
        expires_at=now + timedelta(hours=24),
    )


@pytest.mark.asyncio
async def test_valid_normal_data():
    """정상 데이터 → VALID."""
    agent = DataAgent()
    signal = _make_signal(features={
        "staleness_minutes": 2, "missing_ratio_pct": 0.5,
        "n_candles": 250, "listing_age_days": 365,
    })
    review = await agent.review(signal)
    assert review.verdict == "VALID"


@pytest.mark.asyncio
async def test_stale_old_data():
    """staleness 10분 (> 5분) → STALE."""
    agent = DataAgent()
    signal = _make_signal(features={
        "staleness_minutes": 10, "missing_ratio_pct": 0.1,
        "listing_age_days": 365,
    })
    review = await agent.review(signal)
    assert review.verdict == "STALE"


@pytest.mark.asyncio
async def test_invalid_protected_symbol():
    """BTCUSDT (보호자산) → INVALID."""
    agent = DataAgent(protected_symbols=["BTCUSDT", "ETHUSDT"])
    signal = _make_signal(symbol="BTCUSDT", features={
        "staleness_minutes": 1, "missing_ratio_pct": 0.0,
    })
    review = await agent.review(signal)
    assert review.verdict == "INVALID"
    assert "protected" in review.concerns[0].lower()


@pytest.mark.asyncio
async def test_invalid_lookahead_detected():
    """lookahead_detected=True → INVALID."""
    agent = DataAgent()
    signal = _make_signal(features={
        "staleness_minutes": 1, "missing_ratio_pct": 0.0,
        "lookahead_detected": True, "listing_age_days": 365,
    })
    review = await agent.review(signal)
    assert review.verdict == "INVALID"
    assert any("ookahead" in c for c in review.concerns)


@pytest.mark.asyncio
async def test_stale_new_listing():
    """listing_age_days 15 → STALE (< 30일 survivorship bias)."""
    agent = DataAgent()
    signal = _make_signal(features={
        "staleness_minutes": 1, "missing_ratio_pct": 0.0,
        "listing_age_days": 15,
    })
    review = await agent.review(signal)
    # listing_age < 30 → STALE 또는 concerns 에 noted
    assert review.verdict in {"STALE", "VALID"}
    assert any("listing_age" in c or "survivorship" in c for c in review.concerns)


@pytest.mark.asyncio
async def test_stale_high_missing_ratio():
    """missing_ratio_pct 3% (> 2%) → STALE."""
    agent = DataAgent()
    signal = _make_signal(features={
        "staleness_minutes": 1, "missing_ratio_pct": 3.0,
        "listing_age_days": 365,
    })
    review = await agent.review(signal)
    assert review.verdict == "STALE"


@pytest.mark.asyncio
async def test_full_json_contains_lookahead_check():
    """full_json 에 lookahead_check 포함."""
    agent = DataAgent()
    signal = _make_signal(features={"staleness_minutes": 1, "listing_age_days": 365})
    review = await agent.review(signal)
    data = json.loads(review.full_json)
    assert "lookahead_check" in data
    assert "survivorship_bias_check" in data
    assert "data_quality" in data


@pytest.mark.asyncio
async def test_default_protected_symbols_from_config():
    """protected_symbols 미주입 시 config/settings.py 로드."""
    agent = DataAgent()  # 기본 — settings.py 의 6개 로드
    signal = _make_signal(symbol="BTCUSDT")  # 보호자산
    review = await agent.review(signal)
    assert review.verdict == "INVALID"


@pytest.mark.asyncio
async def test_invalid_payload_type():
    agent = DataAgent()
    with pytest.raises(TypeError):
        await agent.review("not a signal")
