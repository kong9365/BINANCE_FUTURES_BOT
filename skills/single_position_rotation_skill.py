"""
skills/single_position_rotation_skill.py
=====================================================================
SinglePositionRotationSkill — EV 1위 자동 선정 (청사진 §3.2.3)

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.3 (Single-Position Rotation 워크플로우)
  - 운영자 결정 #v2-3 Strategy Skill #3
  - config/settings.RISK_RULES.max_concurrent_positions = 1 (이미 설정)

역할:
  - 여러 SignalDecision 후보 입력 → EV 1위 1개 선정
  - 청산 후 즉시 재스캔 (다음 1d 봉 대기 X)
  - 직전 1위와 같은 종목은 *cooldown* 고려 (rotation)

본 Skill 은 *집계 Skill* — 다른 Skill 출력을 *비교*해서 1개 선정.
=====================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from audit.signal_decision import SignalDecision
from skills.base import StrategySkill

logger = logging.getLogger(__name__)


class SinglePositionRotationSkill(StrategySkill):
    """후보 풀에서 EV 1위 자동 선정.

    사용:
        rotation = SinglePositionRotationSkill()
        candidates = [signal_solusdt, signal_avaxusdt, ...]
        top = rotation.select_top(candidates, market_state={
            "previous_symbol": "AVAXUSDT",
            "current_position_count": 0,
        })
        if top is not None:
            # 진입 후보
            ...
    """

    SETUP_ID = "single_position_rotation_v1"
    CATEGORY = "rotation"
    ACADEMIC_REF = "Single-Position Rotation (청사진 §3.2.3)"
    PARAMS = {
        "max_concurrent": 1,                       # 청사진 §3.2.3 강제
        "previous_symbol_cooldown_hours": 12,     # rotation: 직전 1위 종목 12h 회피
        "min_ev_threshold": 0.0,                  # EV 양수만 진입
        "min_confidence_threshold": 0.55,         # 최소 confidence
    }

    def evaluate(
        self,
        symbol: str,
        candles: list,
        market_state: Optional[dict] = None,
    ) -> SignalDecision:
        """단일 symbol 평가 — *본 Skill 은 단일 평가 안 함*.

        StrategySkill 의 abstract evaluate() 시그니처 호환을 위해 구현하지만
        실제 사용은 select_top() 메서드.
        """
        # 본 Skill 은 *집계* — 단일 symbol 평가는 무의미
        raw_data_hash = self._compute_raw_data_hash(candles)
        return self._build_flat_decision(
            symbol=symbol,
            reasoning="SinglePositionRotationSkill is an aggregator — use select_top()",
            raw_data_hash=raw_data_hash,
            features={"aggregator": True},
        )

    def select_top(
        self,
        candidates: list[SignalDecision],
        market_state: Optional[dict] = None,
    ) -> Optional[SignalDecision]:
        """EV 1위 후보 선정 (청사진 §3.2.3).

        Args:
            candidates: SignalDecision 리스트 (DailyTSMOMDonchianSkill 등 출력).
            market_state: dict — "previous_symbol" (직전 보유), "previous_exit_ts"
                (datetime), "current_position_count" (현재 보유 수).

        Returns:
            EV 1위 SignalDecision 또는 None (모두 차단 시).
        """
        market_state = market_state or {}

        # 1. 현재 보유 수 한도 체크 (Single-Position)
        current_count = market_state.get("current_position_count", 0)
        if current_count >= self.PARAMS["max_concurrent"]:
            logger.info(
                "[Rotation] current=%d >= max=%d — 신규 진입 차단",
                current_count, self.PARAMS["max_concurrent"],
            )
            return None

        # 2. action != FLAT 인 후보만 (실제 진입 가능)
        actionable = [c for c in candidates if c.action in ("LONG", "SHORT")]
        if not actionable:
            return None

        # 3. confidence + ev 최소 임계 필터
        filtered = [
            c for c in actionable
            if c.confidence >= self.PARAMS["min_confidence_threshold"]
            and c.ev_estimated >= self.PARAMS["min_ev_threshold"]
        ]
        if not filtered:
            logger.info(
                "[Rotation] %d candidates filtered out (min_conf=%.2f, min_ev=%.2f)",
                len(actionable),
                self.PARAMS["min_confidence_threshold"],
                self.PARAMS["min_ev_threshold"],
            )
            return None

        # 4. 직전 1위 cooldown (rotation 의미)
        previous_symbol = market_state.get("previous_symbol")
        previous_exit_ts = market_state.get("previous_exit_ts")
        if previous_symbol and previous_exit_ts:
            cooldown_h = self.PARAMS["previous_symbol_cooldown_hours"]
            elapsed = datetime.now(timezone.utc) - previous_exit_ts
            if elapsed < timedelta(hours=cooldown_h):
                filtered = [c for c in filtered if c.symbol != previous_symbol]
                if not filtered:
                    logger.info(
                        "[Rotation] previous symbol %s cooldown — 다른 후보 없음",
                        previous_symbol,
                    )
                    return None

        # 5. EV 1위 선정
        ranked = sorted(filtered, key=lambda c: c.ev_estimated, reverse=True)
        top = ranked[0]
        # rank_in_universe 업데이트 (1=top)
        # 새 SignalDecision 객체 생성 (immutable 원칙)
        return SignalDecision(
            signal_id=top.signal_id,
            setup_id=top.setup_id,
            params_hash=top.params_hash,
            claude_session_id=top.claude_session_id,
            signal_source=top.signal_source,
            reasoning=top.reasoning + f" [Rotation rank=1/{len(filtered)}]",
            ts_signal_generated=top.ts_signal_generated,
            ts_db_recorded=top.ts_db_recorded,
            raw_data_hash=top.raw_data_hash,
            features_snapshot_json=top.features_snapshot_json,
            symbol=top.symbol,
            action=top.action,
            confidence=top.confidence,
            cost_estimate_json=top.cost_estimate_json,
            ev_estimated=top.ev_estimated,
            rank_in_universe=1,
            expires_at=top.expires_at,
            status=top.status,
            immutable=top.immutable,
        )
