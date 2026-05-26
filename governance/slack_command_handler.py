"""
governance/slack_command_handler.py
=====================================================================
SlackCommandHandler — Slack `/bot halt`, `킬스위치` 명령 → KillSwitch 활성화.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.4.2 (Slack `/bot halt` 명령)
  - mcp/slack_mcp.SlackMCPClient.parse_command
  - governance/kill_switch.py
=====================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from governance.kill_switch import KillSwitch
from mcp.slack_mcp import SlackMCPClient

logger = logging.getLogger(__name__)


def handle_slack_command(
    message_text: str,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Slack 메시지 텍스트로 명령 처리.

    Args:
        message_text: Slack 메시지 본문 (예: "/bot halt").
        user_id: Slack user_id (감사용).

    Returns:
        응답 텍스트 (실행 결과 안내) 또는 None (명령 없음).
    """
    command = SlackMCPClient.parse_command(message_text)
    if command is None:
        return None

    if command == "halt":
        reason = f"Manual halt via Slack (user_id={user_id or 'unknown'})"
        KillSwitch.activate(reason=reason, source="slack")
        logger.critical("[SlackCmd] KillSwitch 활성화 (user_id=%s)", user_id)
        return "🛑 KillSwitch 활성화 — 자동 거래 중단."

    if command == "status":
        active = KillSwitch.is_active()
        if active:
            status = KillSwitch.get_status()
            return (
                f"⚠️ KillSwitch ACTIVE\n"
                f"  Reason: {status.get('reason', 'unknown')}\n"
                f"  Source: {status.get('source', 'unknown')}"
            )
        return "✅ KillSwitch 비활성 (정상 거래 가능)"

    return None
