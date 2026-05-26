"""
governance/shadow_config.py
=====================================================================
ShadowAgentConfig + env helper — lightweight (agents 의존성 X).

순환 import 방지를 위해 분리:
  config.settings → governance.shadow_config (OK, 가벼움)
  governance.shadow_runner → agents.* + governance.shadow_config (OK)
  → config.settings 가 agents 를 거치지 않음 (순환 해소)
=====================================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ShadowAgentConfig:
    """ShadowAgentRunner 설정 (config/settings.py 통합)."""

    enabled: bool = False                # env ENABLE_SHADOW_AGENTS 우선
    log_to_audit: bool = True            # audit_log 적재
    log_full_review_json: bool = True    # 5-Agent JSON 전체 기록
    timeout_seconds: float = 5.0          # 응답지연 측정 (M6 운영자 7기준 #8)


def resolve_shadow_enabled(cfg_default: bool = False) -> bool:
    """ENABLE_SHADOW_AGENTS env 파싱."""
    raw = os.environ.get("ENABLE_SHADOW_AGENTS")
    if raw is None:
        return cfg_default
    return raw.strip().lower() in ("1", "true", "yes", "on")
