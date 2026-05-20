"""
tests/test_cmc_client.py
=====================================================================
CMCClient 단위 테스트.

근거:
  - docs/SPEC_v3.1.md §8-7 (PairWhitelist Tier 3 시총 검증)
  - strategy/pair_whitelist.py (self.cmc.get_market_cap_rank 호출 계약)

구성:
  - urllib.request.urlopen 을 mock (실제 네트워크 호출 없음 — CLAUDE.md 규칙)

검증 시나리오:
  1. get_market_cap_rank — listings 응답에서 순위 반환
  2. 대소문자 무관 조회
  3. 캐시 — TTL 내 재조회는 API 재호출 안 함
  4. TTL 만료 → 재조회
  5. 순위 맵에 없는 심볼 → None
  6. 빈 심볼 → None
  7. API 실패 + 기존 캐시 있음 → stale 캐시 유지
  8. API 실패 + 캐시 없음 → None
  9. 빈 api_key → ValueError
 10. 중복 심볼 → 더 높은(작은) 순위 우선
=====================================================================
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from data.cmc_client import CMCClient


# ── helpers ─────────────────────────────────────────────────────────

def _listing(symbol: str, rank: int) -> dict:
    return {"symbol": symbol, "cmc_rank": rank}


def _mock_urlopen_response(payload: dict) -> MagicMock:
    """urlopen 컨텍스트매니저 mock — json.load 가 읽을 수 있는 객체."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


_DEFAULT_PAYLOAD = {
    "data": [
        _listing("BTC", 1),
        _listing("ETH", 2),
        _listing("SOL", 5),
        _listing("XRP", 7),
    ]
}


# ── 1~2. 순위 조회 ──────────────────────────────────────────────────

def test_get_rank_returns_value():
    """listings 응답에서 cmc_rank 를 반환한다."""
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(_DEFAULT_PAYLOAD)):
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("SOL") == 5
        assert cmc.get_market_cap_rank("BTC") == 1


def test_get_rank_case_insensitive():
    """심볼 대소문자와 무관하게 조회된다."""
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(_DEFAULT_PAYLOAD)):
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("sol") == 5
        assert cmc.get_market_cap_rank("Eth") == 2


# ── 3~4. 캐싱 ───────────────────────────────────────────────────────

def test_cache_avoids_refetch_within_ttl():
    """TTL 내 재조회는 API 를 다시 호출하지 않는다."""
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(_DEFAULT_PAYLOAD)) as m:
        cmc = CMCClient("test-key", cache_ttl_seconds=3600)
        cmc.get_market_cap_rank("SOL")
        cmc.get_market_cap_rank("ETH")
        cmc.get_market_cap_rank("XRP")
        assert m.call_count == 1


def test_cache_refetches_after_ttl():
    """TTL 만료 시 API 를 다시 호출한다 (ttl=0 → 매번 재조회)."""
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(_DEFAULT_PAYLOAD)) as m:
        cmc = CMCClient("test-key", cache_ttl_seconds=0)
        cmc.get_market_cap_rank("SOL")
        cmc.get_market_cap_rank("SOL")
        assert m.call_count == 2


# ── 5~6. 미존재 / 빈 심볼 ───────────────────────────────────────────

def test_unknown_symbol_returns_none():
    """순위 맵에 없는 심볼(상위 N 밖) → None."""
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(_DEFAULT_PAYLOAD)):
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("DOGE") is None


def test_empty_symbol_returns_none():
    """빈 심볼 → None, API 미호출."""
    with patch("urllib.request.urlopen") as m:
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("") is None
        m.assert_not_called()


# ── 7~8. API 실패 ───────────────────────────────────────────────────

def test_api_failure_keeps_stale_cache():
    """API 실패 시 기존 캐시를 유지한다 (조회는 stale 값 반환)."""
    responses = [
        _mock_urlopen_response(_DEFAULT_PAYLOAD),       # 1차 성공
        urllib.error.URLError("network down"),          # 2차 실패
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        cmc = CMCClient("test-key", cache_ttl_seconds=0)  # 매번 refresh 시도
        assert cmc.get_market_cap_rank("SOL") == 5        # 1차: 캐시 채움
        assert cmc.get_market_cap_rank("SOL") == 5        # 2차: 실패 → stale 유지


def test_api_failure_no_cache_returns_none():
    """API 실패 + 기존 캐시 없음 → None."""
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("network down")):
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("SOL") is None


# ── 9. 입력 검증 ────────────────────────────────────────────────────

def test_empty_api_key_raises():
    """빈 api_key → ValueError."""
    with pytest.raises(ValueError, match="api_key"):
        CMCClient("")


# ── 10. 중복 심볼 ───────────────────────────────────────────────────

def test_duplicate_symbol_keeps_better_rank():
    """동일 심볼이 여러 개면 더 높은(작은) 순위를 유지한다."""
    payload = {"data": [_listing("SOL", 90), _listing("SOL", 5)]}
    with patch("urllib.request.urlopen",
               return_value=_mock_urlopen_response(payload)):
        cmc = CMCClient("test-key")
        assert cmc.get_market_cap_rank("SOL") == 5
