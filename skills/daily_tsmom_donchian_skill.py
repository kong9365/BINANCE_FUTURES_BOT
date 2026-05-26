"""
skills/daily_tsmom_donchian_skill.py
=====================================================================
DailyTSMOMDonchianSkill — 청사진 §3.2.1 의 "1d_tsmom_donchian_long_v1"

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.2.1 (Strategy Skill 사례 — TSMOM/Donchian)
  - SYSTEM_DESIGN_BLUEPRINT.md §9.2 (Han·Kang·Ryu 2023 SSRN 4675565)
  - docs/HANDOFF.md (1d 돌파 walk-forward PF 1.39 OOS 1.63, ZEC 단일 97% 발견)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M2 (운영자 결정 #v2-3 Strategy Skill #1)

설계 메모:
  - 기존 strategy/breakout.py `evaluate_breakout()` 재사용 (1d candles 입력).
  - PARAMS 는 청사진 §3.2.1 의 표준 + 추세추종 합의 (donchian 20, EMA 200, ADX 25).
  - TIER 1 #9 조건부 완화: 본 setup 은 *재시도 X 재검증*. M2 백테스트 7기준 통과시만
    R0_QUALIFIED. 같은 데이터 + 같은 임계 단순 재시도는 여전히 금지.
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
from strategy.breakout import BreakoutConfig, evaluate_breakout

logger = logging.getLogger(__name__)


class DailyTSMOMDonchianSkill(StrategySkill):
    """1d 추세추종 (Donchian 돌파) — 청사진 §3.2.1 v1.

    핵심 룰 (strategy/breakout.evaluate_breakout 재사용):
        LONG  = 1d 마감봉 종가 > 직전 20d Donchian 상단 AND > 200EMA AND ADX ≥ 25
        SHORT = 대칭 (하향)

    저승률·고R 프로파일 (HANDOFF: 1d walk-forward 승률 54~57%, expR +0.23 OOS +0.42).
    """

    SETUP_ID = "1d_tsmom_donchian_long_v1"
    CATEGORY = "trend"
    ACADEMIC_REF = "Han·Kang·Ryu 2023 SSRN 4675565 (TSMOM Sharpe 1.51)"
    PARAMS = {
        # Donchian
        "donchian_entry": 20,
        "donchian_exit": 10,
        # ADX
        "adx_period": 14,
        "adx_trend_min": 25.0,
        # EMA
        "ema_period": 200,
        # ATR
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 4.0,
        # Timeframe
        "timeframe": "1d",
        # confidence 산정 (튜닝 없이 단순 룰)
        "base_confidence": 0.60,
        "confidence_per_adx_unit": 0.01,   # ADX 25 → 0.60, ADX 35 → 0.70
        "max_confidence": 0.85,
    }

    def evaluate(
        self,
        symbol: str,
        candles: list,
        market_state: Optional[dict] = None,
    ) -> SignalDecision:
        """1d 돌파 평가 → SignalDecision 반환.

        Args:
            symbol: 거래 페어.
            candles: 1d 마감봉 리스트 (BinanceDataCollector.get_candles 출력 형식).
                각 봉은 (open, high, low, close, volume, timestamp) tuple.
            market_state: 시장 컨텍스트 (regime, btc_risk_off 등).

        Returns:
            SignalDecision — action=LONG/SHORT/FLAT.
        """
        raw_data_hash = self._compute_raw_data_hash(candles)
        market_state = market_state or {}

        # 입력 검증
        if not candles:
            return self._build_flat_decision(
                symbol=symbol,
                reasoning="No candles provided",
                raw_data_hash=raw_data_hash,
            )

        # BreakoutConfig — PARAMS 에서 매핑
        cfg = BreakoutConfig(
            donchian_entry=self.PARAMS["donchian_entry"],
            donchian_exit=self.PARAMS["donchian_exit"],
            adx_period=self.PARAMS["adx_period"],
            adx_trend_min=self.PARAMS["adx_trend_min"],
            ema_period=self.PARAMS["ema_period"],
            atr_period=self.PARAMS["atr_period"],
            atr_stop_mult=self.PARAMS["atr_stop_mult"],
            atr_target_mult=self.PARAMS["atr_target_mult"],
        )

        signal = evaluate_breakout(candles, cfg)
        if signal is None:
            # 진입 조건 미충족
            return self._build_flat_decision(
                symbol=symbol,
                reasoning=(
                    f"No breakout signal — ADX/EMA/Donchian 조건 미충족 "
                    f"(min_bars={cfg.min_bars()}, len={len(candles)})"
                ),
                raw_data_hash=raw_data_hash,
            )

        # confidence 계산 (PARAMS 기반)
        adx_excess = max(0.0, signal.adx - self.PARAMS["adx_trend_min"])
        confidence = min(
            self.PARAMS["max_confidence"],
            self.PARAMS["base_confidence"]
            + adx_excess * self.PARAMS["confidence_per_adx_unit"],
        )

        # 자연어 reasoning 템플릿 (TIER 1 #10: GPT 직접 호출 X)
        reasoning = (
            f"1d {signal.action} signal — Donchian {self.PARAMS['donchian_entry']}일 "
            f"{'상단' if signal.action == 'LONG' else '하단'} 돌파 "
            f"(level={signal.level:.4f}), ADX {signal.adx:.1f} "
            f"≥ {self.PARAMS['adx_trend_min']}, "
            f"{'EMA200 위' if signal.action == 'LONG' else 'EMA200 아래'} "
            f"({signal.ema:.4f}), ATR {signal.atr:.4f}. "
            f"Setup ref: {self.ACADEMIC_REF}."
        )

        features = {
            "action": signal.action,
            "adx": signal.adx,
            "ema": signal.ema,
            "atr": signal.atr,
            "donchian_level": signal.level,
            "timeframe": self.PARAMS["timeframe"],
            "n_candles": len(candles),
        }

        now = datetime.now(timezone.utc)
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
            action=signal.action,
            confidence=confidence,
            expires_at=now + timedelta(hours=24),
        )
