"""
mcp/slack_mcp.py
=====================================================================
SlackMCPClient — Slack 알림 + Kill Switch 명령 처리.

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.4.2 (Slack MCP 명세)
  - 운영자 결정 #v1-4 (Slack ON)
  - mcp/outbox.py (fail-soft)

설계:
  - send_alert(): 직접 호출 + 실패 시 outbox 큐로 적재
  - read_command(): Slack `/bot halt`, `킬스위치` 메시지 감지
  - 청사진 §3.4.2 메시지 포맷 (진입 알림, 이상 신호, 일일 P&L)

본 모듈은 *최소 인터페이스*만 — 실제 Slack 라이브러리 호출은
주입된 send_fn 으로 (테스트 용이성).
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from mcp.outbox import Outbox

logger = logging.getLogger(__name__)


@dataclass
class SlackMessage:
    """Slack 알림 메시지."""

    channel: str
    text: str
    severity: str = "info"     # info / warn / critical


class SlackMCPClient:
    """Slack MCP 알림기 + 명령 핸들러.

    사용:
        async def real_slack_send(payload: dict) -> bool:
            # 실제 Slack API 호출
            ...
        client = SlackMCPClient(outbox=outbox, send_fn=real_slack_send)
        await client.send_alert(SlackMessage(channel="#alerts", text="..."))
    """

    def __init__(
        self,
        outbox: Outbox,
        send_fn: Optional[Callable[[dict], Awaitable[bool]]] = None,
        default_channel_alerts: str = "#bot-alerts",
        default_channel_trades: str = "#bot-trades",
    ) -> None:
        if outbox is None:
            raise ValueError("outbox must not be None")
        self.outbox = outbox
        self.send_fn = send_fn
        self.default_channel_alerts = default_channel_alerts
        self.default_channel_trades = default_channel_trades

    async def send_alert(self, message: SlackMessage) -> bool:
        """알림 전송 — 실패 시 outbox 큐 적재 (fail-soft).

        Returns:
            True = 즉시 전송 성공, False = outbox 큐 적재.
        """
        payload = {
            "channel": message.channel,
            "text": message.text,
            "severity": message.severity,
        }
        if self.send_fn is None:
            # send_fn 미주입 — 항상 outbox 큐
            self.outbox.enqueue(target="slack", payload=payload)
            logger.info("[Slack] send_fn 미주입 — outbox 큐 적재")
            return False

        try:
            ok = await self.send_fn(payload)
            if ok:
                return True
        except Exception as e:  # noqa: BLE001 — fail-soft
            logger.warning("[Slack] send 실패: %s — outbox 큐", e)

        self.outbox.enqueue(target="slack", payload=payload)
        return False

    async def send_trade_entry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        setup_id: str,
        confidence: float,
        reasoning: str,
    ) -> bool:
        """청사진 §3.4.2 진입 알림 메시지 포맷."""
        text = (
            f"✅ 진입 발생\n"
            f"  Symbol: {symbol}\n"
            f"  Side: {side}\n"
            f"  Quantity: {quantity}\n"
            f"  Avg Entry: ${entry_price}\n"
            f"  Setup: {setup_id}\n"
            f"  Confidence: {confidence:.2f}\n"
            f"  Reasoning: {reasoning}"
        )
        return await self.send_alert(SlackMessage(
            channel=self.default_channel_trades, text=text, severity="info",
        ))

    async def send_kill_switch_alert(self, reason: str, source: str) -> bool:
        """청사진 §3.4.2 이상 신호 알림."""
        text = (
            f"🚨 KillSwitch 활성화\n"
            f"  Reason: {reason}\n"
            f"  Source: {source}\n"
            f"  자동 거래 중단"
        )
        return await self.send_alert(SlackMessage(
            channel=self.default_channel_alerts, text=text, severity="critical",
        ))

    @staticmethod
    def parse_command(text: str) -> Optional[str]:
        """Slack 메시지에서 Kill Switch 명령 감지.

        Returns:
            'halt' / 'status' / None
        """
        if not text:
            return None
        normalized = text.strip().lower()
        if normalized in ("/bot halt", "킬스위치", "/halt", "halt"):
            return "halt"
        if normalized in ("/bot status", "/status", "status"):
            return "status"
        return None
