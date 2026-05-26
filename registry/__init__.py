"""Setup Registry (v3.2.0 M2, 청사진 §6.5).

본 패키지는 청사진의 Setup Registry 를 구현:
  - PARAMS_HASH 기반 setup 등록 (재현성)
  - 메트릭 update (백테스트 7기준)
  - status 관리 (PAPER_ONLY / R0_QUALIFIED / DISABLED / COOLING)
  - 평가 이력 추적 (다중검정 보정용)
"""

from registry.setup_registry import SetupRegistry, SetupStatus

__all__ = ["SetupRegistry", "SetupStatus"]
