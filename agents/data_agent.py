"""
agents/data_agent.py
=====================================================================
DataAgent — 데이터 품질 + 룩어헤드 + 상장일 검증.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.2 Agent 4
  - strategy/pair_whitelist.py (보호자산 + 상장일 + 시총 검증)
  - CLAUDE.md TIER 3 #6 (백테스트 룩어헤드 코드 금지)
  - PairWhitelist 본문 0줄 수정.

입력: SignalDecision + 데이터 메타 (candles 길이, missing_ratio, etc.)
출력: verdict in {VALID, INVALID, STALE}
=====================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from agents.base import Agent
from audit.agent_review import AgentReview
from audit.signal_decision import SignalDecision

logger = logging.getLogger(__name__)


class DataAgent(Agent):
    """데이터 품질 + 룩어헤드 + 상장일 검증."""

    AGENT_TYPE = "data_backtest"

    def __init__(
        self,
        max_staleness_minutes: float = 5.0,
        max_missing_ratio: float = 0.02,
        protected_symbols: Optional[list[str]] = None,
    ) -> None:
        self.max_staleness_minutes = max_staleness_minutes
        self.max_missing_ratio = max_missing_ratio
        # protected_symbols 가 주입되지 않으면 settings.py 에서 로드
        if protected_symbols is None:
            from config.settings import PAIR_WHITELIST_CONFIG
            self.protected_symbols = PAIR_WHITELIST_CONFIG.protected_symbols
        else:
            self.protected_symbols = protected_symbols

    async def review(self, payload: SignalDecision) -> AgentReview:
        """SignalDecision + features 의 데이터 메타로 검증."""
        if not isinstance(payload, SignalDecision):
            raise TypeError(
                f"payload must be SignalDecision, got {type(payload).__name__}"
            )

        try:
            features = json.loads(payload.features_snapshot_json)
        except (json.JSONDecodeError, TypeError):
            features = {}

        concerns: list[str] = []
        recommendations: list[str] = []

        # 1. 보호자산 차단 (CLAUDE.md TIER 1 #6)
        if payload.symbol in self.protected_symbols:
            full_json = self._serialize_full_json({
                "agent": self.AGENT_TYPE,
                "verdict": "INVALID",
                "concerns": [f"{payload.symbol} is protected (CLAUDE.md TIER 1 #6)"],
                "recommendations": ["PairWhitelist.protected_symbols 차단 — 봇 거래 금지"],
            })
            return AgentReview(
                signal_id=payload.signal_id,
                agent=self.AGENT_TYPE,
                verdict="INVALID",
                full_json=full_json,
                concerns=[f"{payload.symbol} is protected"],
                recommendations=["PairWhitelist protected_symbols 강제 차단"],
                confidence="HIGH",
            )

        # 2. 데이터 staleness
        staleness_minutes = features.get("staleness_minutes", 0)
        if staleness_minutes > self.max_staleness_minutes:
            verdict = "STALE"
            concerns.append(
                f"staleness {staleness_minutes:.1f}분 > {self.max_staleness_minutes}분"
            )
            confidence = "HIGH"
        else:
            # 3. Missing ratio
            missing_ratio = features.get("missing_ratio_pct", 0) / 100
            if missing_ratio > self.max_missing_ratio:
                verdict = "STALE"
                concerns.append(
                    f"missing_ratio {missing_ratio * 100:.2f}% > "
                    f"{self.max_missing_ratio * 100:.2f}%"
                )
                confidence = "MEDIUM"
            else:
                # 4. 룩어헤드 검증 (features 에 'lookahead_flag' 가 있으면)
                if features.get("lookahead_detected"):
                    verdict = "INVALID"
                    concerns.append("Lookahead detected — pandas.shift(-1) 의심")
                    recommendations.append("backtest 코드 즉시 중단 + 룩어헤드 제거")
                    confidence = "HIGH"
                else:
                    verdict = "VALID"
                    confidence = "HIGH"

        # 5. 상장일 검증 (PairWhitelist 위임 — 본 Agent 는 reasoning 만)
        listing_age_days = features.get("listing_age_days", 999)
        if listing_age_days < 30:
            concerns.append(
                f"listing_age {listing_age_days}일 < 30일 — survivorship bias 의심"
            )
            recommendations.append("PairWhitelist min_listing_age_days 적용 확인")
            if verdict == "VALID":
                verdict = "STALE"
                confidence = "MEDIUM"

        full_json = self._serialize_full_json({
            "agent": self.AGENT_TYPE,
            "verdict": verdict,
            "data_quality": {
                "staleness_minutes": staleness_minutes,
                "missing_ratio_pct": features.get("missing_ratio_pct", 0),
                "n_candles": features.get("n_candles", 0),
                "listing_age_days": listing_age_days,
            },
            "lookahead_check": {
                "feature_uses_future_data": features.get("lookahead_detected", False),
            },
            "survivorship_bias_check": {
                "point_in_time": features.get("point_in_time", True),
                "onboard_filter_applied": features.get("onboard_filter_applied", True),
            },
            "raw_data_hash": payload.raw_data_hash,
            "concerns": concerns,
            "recommendations": recommendations,
        })

        return AgentReview(
            signal_id=payload.signal_id,
            agent=self.AGENT_TYPE,
            verdict=verdict,
            full_json=full_json,
            concerns=concerns,
            recommendations=recommendations,
            confidence=confidence,
        )
