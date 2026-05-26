"""
tests/test_sector_map.py
=====================================================================
strategy/sector_map.py — 섹터 매핑 정합·헬퍼 단위 테스트.
=====================================================================
"""

from __future__ import annotations

from strategy.sector_map import (
    SECTOR_AI,
    SECTOR_BTC,
    SECTOR_DEFI,
    SECTOR_ETH,
    SECTOR_L1,
    SECTOR_L2,
    SECTOR_MEME,
    SECTOR_PAYMENT,
    get_sector,
    is_any_held_same_sector,
    same_sector,
)


def test_get_sector_known_symbols():
    assert get_sector("BTCUSDT") == SECTOR_BTC
    assert get_sector("ETHUSDT") == SECTOR_ETH
    assert get_sector("SOLUSDT") == SECTOR_L1
    assert get_sector("ARBUSDT") == SECTOR_L2
    assert get_sector("DOGEUSDT") == SECTOR_MEME
    assert get_sector("1000PEPEUSDT") == SECTOR_MEME
    assert get_sector("FETUSDT") == SECTOR_AI
    assert get_sector("AAVEUSDT") == SECTOR_DEFI
    assert get_sector("XRPUSDT") == SECTOR_PAYMENT


def test_get_sector_case_insensitive():
    assert get_sector("solusdt") == SECTOR_L1
    assert get_sector("DoGeUsDt") == SECTOR_MEME


def test_get_sector_unknown_returns_none():
    assert get_sector("ZZZUSDT") is None
    assert get_sector("") is None


def test_same_sector_basic():
    assert same_sector("DOGEUSDT", "1000PEPEUSDT")          # MEME 동일
    assert same_sector("SOLUSDT", "AVAXUSDT")               # L1 동일
    assert not same_sector("DOGEUSDT", "ETHUSDT")           # MEME != ETH
    assert not same_sector("SOLUSDT", "ARBUSDT")            # L1 != L2


def test_same_sector_unknown_is_false():
    # 한쪽 미등록이면 차단하지 않는다(보수적).
    assert not same_sector("ZZZUSDT", "ETHUSDT")
    assert not same_sector("SOLUSDT", "")
    assert not same_sector("", "")


def test_is_any_held_same_sector_meme_block():
    held = ["DOGEUSDT", "BTCUSDT"]
    assert is_any_held_same_sector("1000PEPEUSDT", held)    # MEME 동조 → 차단
    assert is_any_held_same_sector("SHIBUSDT", held)
    assert not is_any_held_same_sector("ARBUSDT", held)     # L2 — 다른 섹터
    assert not is_any_held_same_sector("ETHUSDT", held)     # ETH 단독


def test_is_any_held_same_sector_excludes_self():
    # 후보가 보유 종목 안에 있어도 *그 자신*은 제외(자기 자신 비교는 의미 없음).
    held = ["DOGEUSDT"]
    assert not is_any_held_same_sector("DOGEUSDT", [])
    # 자기 자신만 들어있고 같은 섹터 다른 종목이 없으면 False
    assert not is_any_held_same_sector("DOGEUSDT", ["DOGEUSDT"])


def test_is_any_held_same_sector_empty():
    assert not is_any_held_same_sector("DOGEUSDT", [])
    assert not is_any_held_same_sector("", ["DOGEUSDT"])


def test_is_any_held_same_sector_unknown_candidate():
    # 후보 미등록이면 동조 판정 불가 → 차단 안 함.
    assert not is_any_held_same_sector("ZZZUSDT", ["DOGEUSDT", "1000PEPEUSDT"])


def test_l1_vs_l2_distinct():
    # L1·L2 동조 위험을 별도로 보고 싶을 때를 위해 분리 유지.
    assert get_sector("SOLUSDT") != get_sector("ARBUSDT")


def test_protected_symbol_inj_is_l1():
    # 보호종목 INJUSDT 도 섹터 매핑 존재.
    assert get_sector("INJUSDT") == SECTOR_L1
