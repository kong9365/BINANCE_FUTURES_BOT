"""
skills/candidate_context_skill.py
=====================================================================
CandidateContextSkill — 후보 선정 보조 (운영자 결정 #v2-3 Strategy Skill #2)

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.1.4 (온체인 / 매크로 보조 데이터)
  - SYSTEM_DESIGN_BLUEPRINT.md §10.1 TIER 1 #10 (AI Research 직접 진입 결정 금지)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2 (operator request Skill #2)
  - 기존 analytics/macro_event_analyzer.py + data/oi_scanner.py + strategy/oi_filter.py wrap

역할:
  - *직접 거래 결정 X* — 후보 *필터* 또는 *risk flag* 만 발행
  - Macro 이벤트 임박 (예: CPI, FOMC) → SKIP
  - OI C_DANGER 등급 → SKIP
  - 모두 정상 → CONTINUE (이어서 DailyTSMOMDonchianSkill 등 trend skill 적용)

TIER 1 #10 준수: AI Research 또는 GPT 직접 호출 X. 자연어 reasoning 은 *템플릿*만.
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


class CandidateContextSkill(StrategySkill):
    """후보 선정 보조 — Macro / OI 위험 차단.

    출력 SignalDecision:
        action="FLAT" — 차단 (Macro 임박 또는 OI C_DANGER)
        action="LONG"/"SHORT"/"EXIT" — 본 Skill 은 *직접 거래 결정 안 함* (TIER 1 #10)
        즉 반환은 항상 FLAT 또는 *CONTINUE* (action="FLAT", confidence=0.95+, special marker).

    market_state 입력 필수:
        - "macro_blocked": bool (MacroEventAnalyzer.is_blocked() 결과)
        - "oi_grade": str ("A_STRONG" / "B_MODERATE" / "C_DANGER" 또는 None)
        - "regime": str (RegimeDetector 결과)
        - "btc_risk_off": bool

    사용 패턴:
        ctx_decision = candidate_context_skill.evaluate(symbol, candles, market_state)
        if ctx_decision.action == "FLAT" and "blocked" in ctx_decision.reasoning.lower():
            return ctx_decision  # 후보 차단
        # 정상 컨텍스트 — trend skill 등 적용 가능
    """

    SETUP_ID = "candidate_context_v1"
    CATEGORY = "context"
    ACADEMIC_REF = "Macro events + OI extremes 보조 필터 (직접 신호 X)"
    PARAMS = {
        # OI 위험 등급 — C_DANGER 이면 차단
        "block_oi_grade": ["C_DANGER"],
        # 추가 차단 규칙 (확장 가능)
        "block_high_vol_regime": True,    # HIGH_VOL 레짐 차단
        "block_btc_risk_off": True,        # btc_risk_off 활성 시 차단
    }

    def evaluate(
        self,
        symbol: str,
        candles: list,
        market_state: Optional[dict] = None,
    ) -> SignalDecision:
        """Context 평가 — 차단 사유가 있으면 FLAT, 없으면 FLAT (continue marker).

        본 Skill 은 직접 거래 결정을 *절대* 하지 않음 (CLAUDE.md TIER 1 #10).
        후속 trend/rotation skill 의 입력으로 사용.
        """
        market_state = market_state or {}
        raw_data_hash = self._compute_raw_data_hash(candles)

        # 차단 사유 수집
        block_reasons: list[str] = []

        # 1. Macro 이벤트 임박
        if market_state.get("macro_blocked"):
            block_reasons.append("Macro event blocked (e.g. CPI/FOMC 임박)")

        # 2. OI 위험 등급
        oi_grade = market_state.get("oi_grade")
        if oi_grade in self.PARAMS["block_oi_grade"]:
            block_reasons.append(f"OI grade={oi_grade} (위험 등급)")

        # 3. HIGH_VOL 레짐
        if self.PARAMS["block_high_vol_regime"] and \
                market_state.get("regime") == "HIGH_VOL":
            block_reasons.append("Regime=HIGH_VOL (변동성 과도)")

        # 4. BTC Risk-Off
        if self.PARAMS["block_btc_risk_off"] and market_state.get("btc_risk_off"):
            block_reasons.append("BTC Risk-Off 활성 (1h -1.2% 급락)")

        features = {
            "macro_blocked": market_state.get("macro_blocked", False),
            "oi_grade": oi_grade,
            "regime": market_state.get("regime"),
            "btc_risk_off": market_state.get("btc_risk_off", False),
            "block_reasons_count": len(block_reasons),
        }

        now = datetime.now(timezone.utc)
        if block_reasons:
            reasoning = (
                f"Candidate blocked — {len(block_reasons)} reasons: "
                + "; ".join(block_reasons)
            )
            return SignalDecision(
                signal_id=str(uuid4()),
                setup_id=self.SETUP_ID,
                params_hash=self.PARAMS_HASH,
                signal_source="strategy_skill",
                reasoning=reasoning,
                ts_signal_generated=now,
                raw_data_hash=raw_data_hash,
                features_snapshot_json=json.dumps(features, sort_keys=True),
                symbol=symbol,
                action="FLAT",
                confidence=0.0,
                expires_at=now + timedelta(hours=1),
            )

        # 정상 — continue marker (action=FLAT, confidence=0.9)
        return SignalDecision(
            signal_id=str(uuid4()),
            setup_id=self.SETUP_ID,
            params_hash=self.PARAMS_HASH,
            signal_source="strategy_skill",
            reasoning="Candidate context OK — no blocking conditions (continue)",
            ts_signal_generated=now,
            raw_data_hash=raw_data_hash,
            features_snapshot_json=json.dumps(features, sort_keys=True),
            symbol=symbol,
            action="FLAT",                # *직접 거래 결정 X* (TIER 1 #10)
            confidence=0.9,                # CONTINUE marker (높은 confidence = 정상)
            expires_at=now + timedelta(hours=1),
        )
