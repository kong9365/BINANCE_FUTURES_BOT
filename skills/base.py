"""
skills/base.py
=====================================================================
StrategySkill — 청사진 §3.2.1 의 Strategy Skill 추상 base

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.1 (Strategy Skill — 결정 규칙 표준화)
  - SYSTEM_DESIGN_BLUEPRINT.md §6.1 (SignalDecision 출력 형식)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2 (3개 skill wrap)

설계 원칙:
  - PARAMS_HASH 는 *__init_subclass__* 에서 자동 계산 → 자식 클래스가 PARAMS
    정의만 하면 자동 보장. 변경 시 새 PARAMS_HASH → 다중검정 보정 가능.
  - evaluate() 는 abstract — 자식 구현 필수.
  - 입력 검증 + 자연어 reasoning 템플릿화 (TIER 1 #10: GPT 직접 호출 X).
=====================================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from audit.signal_decision import SignalDecision

logger = logging.getLogger(__name__)


class StrategySkill:
    """청사진 §3.2.1 Strategy Skill 추상 base.

    자식 클래스 정의 예시:
        class DailyTSMOMDonchianSkill(StrategySkill):
            SETUP_ID = "1d_tsmom_donchian_long_v1"
            PARAMS = {"donchian_entry": 20, "ema_period": 200, ...}
            CATEGORY = "trend"
            ACADEMIC_REF = "Han·Kang·Ryu 2023 SSRN 4675565"

            def evaluate(self, symbol, candles, market_state) -> SignalDecision:
                ...

    PARAMS_HASH 는 __init_subclass__ 에서 자동 계산 (sha256 of sorted PARAMS).
    """

    # 자식이 반드시 override
    SETUP_ID: str = ""
    PARAMS: dict[str, Any] = {}
    CATEGORY: str = ""              # "trend" / "mean_rev" / "context" / "rotation"
    ACADEMIC_REF: str = ""          # 학계 근거

    # 자동 계산
    PARAMS_HASH: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.PARAMS:
            normalized = json.dumps(cls.PARAMS, sort_keys=True, separators=(",", ":"))
            cls.PARAMS_HASH = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def evaluate(
        self,
        symbol: str,
        candles: list,
        market_state: Optional[dict] = None,
    ) -> SignalDecision:
        """평가 + ALCOA+ 추적. 자식 클래스가 override 필수.

        Args:
            symbol: 거래 페어 (예: "SOLUSDT").
            candles: 마감봉 리스트 (예: list of (o,h,l,c,v,ts) tuples).
            market_state: 시장 컨텍스트 dict (regime, btc_risk_off, macro 등).

        Returns:
            SignalDecision (action=LONG/SHORT/FLAT/EXIT, reasoning 포함).
        """
        raise NotImplementedError(
            f"{type(self).__name__}.evaluate() must be implemented"
        )

    def _build_flat_decision(
        self,
        symbol: str,
        reasoning: str,
        raw_data_hash: str,
        features: Optional[dict] = None,
    ) -> SignalDecision:
        """진입 조건 미충족 시 FLAT SignalDecision 생성 헬퍼."""
        now = datetime.now(timezone.utc)
        return SignalDecision(
            setup_id=self.SETUP_ID,
            params_hash=self.PARAMS_HASH,
            signal_source="strategy_skill",
            reasoning=reasoning,
            ts_signal_generated=now,
            raw_data_hash=raw_data_hash,
            features_snapshot_json=json.dumps(features or {}, sort_keys=True),
            symbol=symbol,
            action="FLAT",
            confidence=0.0,
            expires_at=now + timedelta(hours=24),
        )

    def _compute_raw_data_hash(self, candles: list) -> str:
        """입력 candles 의 sha256 (ALCOA+ Original)."""
        # tuple → tuple of tuple 직렬화. timestamp 까지 포함되어야 함.
        try:
            normalized = json.dumps(
                [list(c) for c in candles], separators=(",", ":")
            )
        except (TypeError, ValueError):
            # candles 가 list of tuples 인 경우 fallback
            normalized = str(candles)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
