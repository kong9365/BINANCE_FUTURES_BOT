"""
tests/test_notify.py
=====================================================================
monitoring/notify.py + 데몬 가동 배선 — Telegram 발신 폴백·fail-soft,
run() 비활성 no-op, CLI 보호종목 제외. *발신만·실주문 0*.
"""

from __future__ import annotations

import monitoring.notify as nt
from config.settings import MonitoringConfig
from monitoring.notify import build_telegram_sender
from monitoring.runner import run


def test_telegram_sender_noop_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    send = build_telegram_sender()
    send("line1\nline2")                       # 미설정 → no-op(로그만), 예외 없음


def test_telegram_sender_fail_soft(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    def _boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(nt.urllib.request, "urlopen", _boom)
    build_telegram_sender()("hi")              # 발신 실패 → fail-soft(예외 전파 X)


def test_run_disabled_is_noop():
    # enabled=False → 즉시 반환(Client 생성·네트워크 없음)
    run(["ADAUSDT"], "x.db", lambda t: None, MonitoringConfig(enabled=False), max_cycles=0)


def test_observer_cli_excludes_protected(monkeypatch):
    monkeypatch.setenv("MONITORING_SYMBOLS", "BTCUSDT,ADAUSDT,ETHUSDT,SOLUSDT")
    from scripts.run_observer import _symbols
    syms = _symbols()
    assert "BTCUSDT" not in syms and "ETHUSDT" not in syms    # ★ 보호종목 제외
    assert "ADAUSDT" in syms and "SOLUSDT" in syms
