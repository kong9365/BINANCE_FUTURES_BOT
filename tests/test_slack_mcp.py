"""tests/test_slack_mcp.py — Slack MCP client + command parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db.init_db import init_db
from mcp.outbox import Outbox
from mcp.slack_mcp import SlackMCPClient, SlackMessage


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "slack.db"
        init_db(db_path)
        yield str(db_path)


@pytest.fixture
def outbox(tmp_db):
    return Outbox(db_path=tmp_db)


@pytest.mark.asyncio
async def test_send_alert_success(outbox):
    """send_fn 성공 → True 반환, outbox 큐 X."""
    send_fn = AsyncMock(return_value=True)
    client = SlackMCPClient(outbox=outbox, send_fn=send_fn)
    ok = await client.send_alert(SlackMessage(
        channel="#test", text="hello", severity="info",
    ))
    assert ok is True
    assert outbox.count_pending() == 0


@pytest.mark.asyncio
async def test_send_alert_fallback_to_outbox(outbox):
    """send_fn 실패 → outbox 큐."""
    send_fn = AsyncMock(side_effect=Exception("network down"))
    client = SlackMCPClient(outbox=outbox, send_fn=send_fn)
    ok = await client.send_alert(SlackMessage(
        channel="#test", text="hello", severity="info",
    ))
    assert ok is False
    assert outbox.count_pending() == 1


@pytest.mark.asyncio
async def test_send_alert_no_send_fn_outbox(outbox):
    """send_fn=None → 직접 outbox 큐."""
    client = SlackMCPClient(outbox=outbox, send_fn=None)
    ok = await client.send_alert(SlackMessage(channel="#test", text="x"))
    assert ok is False
    assert outbox.count_pending() == 1


@pytest.mark.asyncio
async def test_send_trade_entry_format(outbox):
    """진입 알림 메시지 포맷 (청사진 §3.4.2)."""
    send_fn = AsyncMock(return_value=True)
    client = SlackMCPClient(outbox=outbox, send_fn=send_fn)
    ok = await client.send_trade_entry(
        symbol="SOLUSDT", side="LONG", quantity=5.0,
        entry_price=142.5, setup_id="1d_tsmom_donchian_long_v1",
        confidence=0.78, reasoning="Donchian 20일 돌파",
    )
    assert ok is True
    call_args = send_fn.call_args[0][0]
    assert "진입 발생" in call_args["text"]
    assert "SOLUSDT" in call_args["text"]
    assert "1d_tsmom_donchian_long_v1" in call_args["text"]


@pytest.mark.asyncio
async def test_send_kill_switch_alert(outbox):
    """KillSwitch 알림 포맷."""
    send_fn = AsyncMock(return_value=True)
    client = SlackMCPClient(outbox=outbox, send_fn=send_fn)
    ok = await client.send_kill_switch_alert(
        reason="daily -1.5%", source="daily_loss_hard",
    )
    assert ok is True
    msg = send_fn.call_args[0][0]
    assert "KillSwitch" in msg["text"]
    assert msg["severity"] == "critical"


def test_parse_command_halt():
    assert SlackMCPClient.parse_command("/bot halt") == "halt"
    assert SlackMCPClient.parse_command("킬스위치") == "halt"
    assert SlackMCPClient.parse_command("halt") == "halt"


def test_parse_command_status():
    assert SlackMCPClient.parse_command("/bot status") == "status"
    assert SlackMCPClient.parse_command("status") == "status"


def test_parse_command_unknown():
    assert SlackMCPClient.parse_command("hello") is None
    assert SlackMCPClient.parse_command("") is None
    assert SlackMCPClient.parse_command(None) is None  # type: ignore


def test_no_outbox_rejected():
    with pytest.raises(ValueError, match="outbox"):
        SlackMCPClient(outbox=None)
