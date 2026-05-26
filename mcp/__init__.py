"""MCP Connectors (v3.2.0 M5, 청사진 §3.1.2 + §3.4.2).

운영자 결정 #v1-4 (2026-05-26):
  - Slack MCP: ON (Kill Switch 트리거 + 알림)
  - Notion MCP: 보류 (Phase 1.5 prompt 이후)
  - Binance MCP: shadow opt-in (python-binance와 결과 비교만)
  - WebSocket: 기존 data/ws_collector.py 유지

설계 원칙:
  - shadow: 기존 python-binance + telegram-bot 그대로 작동
  - Outbox 패턴: Slack down 시 SQLite 백로그 + 지수 백오프
  - 모든 MCP 호출은 *옵션* — env flag 로 OFF 가능
"""

from mcp.outbox import Outbox

__all__ = ["Outbox"]
