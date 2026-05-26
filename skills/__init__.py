"""Strategy Skills (v3.2.0 M2, 청사진 §3.2.1).

본 패키지는 청사진의 Strategy Skill 추상화 — 기존 strategy/ 모듈을
*얇은 wrapper* 로 감싸 SignalDecision dataclass 출력.

운영자 결정 #v2-3 (2026-05-26): 3개 wrap, OISurge 제외:
  - DailyTSMOMDonchianSkill — 1d 돌파 (strategy/breakout.py 재사용)
  - CandidateContextSkill   — Macro/OI 위험 차단 (후보 필터)
  - SinglePositionRotationSkill — EV 1위 자동 선정 (청사진 §3.2.3)

설계 원칙:
  - Skill 클래스는 *순수 함수* 비슷 (외부 I/O 최소화, 테스트 용이)
  - SETUP_ID, PARAMS, PARAMS_HASH 는 class attribute (재현성 보장)
  - 입력: symbol, candles, market_state → 출력: SignalDecision
  - TIER 1 #10 정합: AI Research / GPT 직접 호출 X (자연어 reasoning 은 *템플릿*)
"""

from skills.base import StrategySkill

__all__ = ["StrategySkill"]
