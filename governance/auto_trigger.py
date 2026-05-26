"""
governance/auto_trigger.py
=====================================================================
AutoTrigger — KillSwitch 자동 활성화 6조건 (청사진 §3.4.3).

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.4.3 (자동 활성화 조건)
  - 운영자 결정 #v2-2: hard 임계 -1.5% 유지 (청사진 -5% 가 아닌)
  - governance/risk_hierarchy.py (2-tier)

6조건:
  1. 일일 손실 -1.5% (운영자 결정 #v2-2 hard 임계)
  2. OpsAgent CRITICAL (또는 SystemHealthMonitor critical)
  3. MCP 연결 끊김 5분 (M5 이후 활성)
  4. 데이터 stream stale > 3분
  5. API 키 권한 변경 감지 (M5 이후 활성)
  6. 외장 SSD disconnect (BOT_DATA_DIR mount 체크)

설계 메모:
  - 순수 함수 + 상태 객체 비슷 (외부 I/O 최소).
  - check() 호출자가 KillSwitch.activate() 직접 호출 (의존성 분리).
=====================================================================
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from governance.risk_hierarchy import (
    RISK_HIERARCHY_CONFIG, RiskHierarchyConfig, check_daily_loss,
)

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    """자동 활성화 평가 결과."""

    activate: bool                # True 면 KillSwitch.activate() 권장
    reason: Optional[str] = None
    source: Optional[str] = None  # daily_loss_hard/ops_critical/mcp_disconnect/...


def check_daily_loss_hard(
    current_wallet: float,
    daily_start: float,
    cfg: RiskHierarchyConfig = RISK_HIERARCHY_CONFIG,
) -> TriggerResult:
    """#1: 일일 손실 -1.5% (운영자 결정 #v2-2 hard)."""
    status = check_daily_loss(current_wallet, daily_start, cfg)
    if status == "HARD":
        pct = (current_wallet - daily_start) / daily_start * 100 if daily_start > 0 else 0
        return TriggerResult(
            activate=True,
            reason=f"Daily loss {pct:.2f}% reached hard limit -{cfg.daily_hard_pct * 100:.1f}%",
            source="daily_loss_hard",
        )
    return TriggerResult(activate=False)


def check_ops_critical(ops_review_verdict: Optional[str]) -> TriggerResult:
    """#2: OpsAgent CRITICAL."""
    if ops_review_verdict == "CRITICAL":
        return TriggerResult(
            activate=True,
            reason="OpsAgent verdict=CRITICAL — system health degraded",
            source="ops_critical",
        )
    return TriggerResult(activate=False)


def check_data_stale(staleness_minutes: float, max_minutes: float = 3.0) -> TriggerResult:
    """#4: 데이터 stream stale > 3분."""
    if staleness_minutes > max_minutes:
        return TriggerResult(
            activate=True,
            reason=f"Data stream stale {staleness_minutes:.1f}분 > {max_minutes}분",
            source="data_stale",
        )
    return TriggerResult(activate=False)


def check_ssd_disconnect(bot_data_dir: str = "data") -> TriggerResult:
    """#6: 외장 SSD disconnect (BOT_DATA_DIR 존재 X)."""
    if not Path(bot_data_dir).exists():
        return TriggerResult(
            activate=True,
            reason=f"BOT_DATA_DIR ({bot_data_dir}) disconnected",
            source="ssd_disconnect",
        )
    return TriggerResult(activate=False)


def check_mcp_disconnect(last_seen_seconds_ago: float, max_seconds: float = 300) -> TriggerResult:
    """#3: MCP 연결 끊김 5분 (M5 이후 활성)."""
    if last_seen_seconds_ago > max_seconds:
        return TriggerResult(
            activate=True,
            reason=f"MCP last seen {last_seen_seconds_ago:.0f}s ago > {max_seconds}s",
            source="mcp_disconnect",
        )
    return TriggerResult(activate=False)


def check_api_key_changed(current_permissions: Optional[dict], expected: dict) -> TriggerResult:
    """#5: API 키 권한 변경 감지 (M5 이후 활성)."""
    if current_permissions is None:
        return TriggerResult(activate=False)
    # 예상과 다른 권한 발견 시 활성화
    for k, v in expected.items():
        if current_permissions.get(k) != v:
            return TriggerResult(
                activate=True,
                reason=f"API key permission changed: {k} {expected[k]} → {current_permissions.get(k)}",
                source="api_key_changed",
            )
    return TriggerResult(activate=False)
