"""
monitoring/notify.py
=====================================================================
관찰 알림 Telegram *발신 전용* 콜백 빌더 — 읽기전용 모니터링용.

★ 발신만(sendMessage). 거래·주문·계좌 0. 봇 토큰/챗ID 는 운영자가 env 로 직접 설정
  (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). 미설정 시 no-op(로그만) 폴백. fail-soft.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Callable, Optional

from config.settings import MONITORING_CONFIG, MonitoringConfig

logger = logging.getLogger(__name__)


def build_telegram_sender(cfg: Optional[MonitoringConfig] = None) -> Callable[[str], None]:
    """env(TELEGRAM_BOT_TOKEN/CHAT_ID) 로 발신 콜백 생성. 미설정 시 로그-only no-op.

    반환 콜백은 fail-soft(발신 실패는 로그만, 예외 전파 안 함 → 데몬 루프 보호).
    """
    cfg = cfg or MONITORING_CONFIG
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.info("[observer] Telegram 미설정 → 알림은 로그로만(no-op).")

        def _null(text: str) -> None:
            logger.info("[observer:alert(disabled)] %s", text.splitlines()[0] if text else "")
        return _null

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _send(text: str) -> None:
        try:
            data = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # noqa: BLE001 — fail-soft(루프 보호)
            logger.warning("[observer] Telegram 발신 실패: %s", e)

    return _send
