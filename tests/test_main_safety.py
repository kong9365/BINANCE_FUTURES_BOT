"""
tests/test_main_safety.py
=====================================================================
A5 [2.10] — 안전 기본값: 무플래그·무env 실행이 실거래를 시도하지 않도록.

진입점(main_7590._resolve_effective_dry_run) 게이트:
  - mainnet(USE_TESTNET≠true) + not dry_run + LIVE_TRADING_ENABLED≠true 이면
    dry_run 을 강제(True). testnet·명시적 opt-in 은 영향 없음(안전쪽으로만).
  - 기존 .env 기반 testnet 동작은 유지(실거래 비활성화는 하지 않음).

음성(무플래그→실주문X) + 양성(opt-in→허용) 둘 다 검증한다.
"""

from __future__ import annotations

import main_7590


def _eff(flag: bool, env: dict, monkeypatch) -> bool:
    for k in ("USE_TESTNET", "LIVE_TRADING_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return main_7590._resolve_effective_dry_run(flag)


def test_a5_dry_run_flag_always_dry(monkeypatch):
    """--dry-run 플래그가 있으면 무조건 dry_run(True)."""
    assert _eff(True, {}, monkeypatch) is True
    assert _eff(True, {"LIVE_TRADING_ENABLED": "true"}, monkeypatch) is True


def test_a5_testnet_keeps_existing_behavior(monkeypatch):
    """USE_TESTNET=true → not dry_run 유지(testnet 주문은 안전, 기존 동작 불변)."""
    assert _eff(False, {"USE_TESTNET": "true"}, monkeypatch) is False


def test_a5_mainnet_no_optin_forces_dry(monkeypatch):
    """음성: 무플래그·무env(mainnet) → 실주문 방지 위해 dry_run 강제(True)."""
    assert _eff(False, {}, monkeypatch) is True


def test_a5_mainnet_live_optin_allows(monkeypatch):
    """양성: mainnet + not dry_run + LIVE_TRADING_ENABLED=true → live 허용(False).

    가드가 정당한 실거래를 막지 않는지 확인.
    """
    assert _eff(False, {"LIVE_TRADING_ENABLED": "true"}, monkeypatch) is False
