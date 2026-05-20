"""
tests/test_collector.py
=====================================================================
BinanceDataCollector 단위 테스트.

근거: docs/SPEC_v3.1.md §8-8 (Layer 1 데이터 수집), §8-8-4 (WS 콜백 훅)

구성:
  - binance_client 는 MagicMock 주입 (실제 네트워크 호출 없음 — CLAUDE.md 규칙)
  - asyncio.to_thread 래핑이므로 mock 메서드는 동기 callable 로 충분
  - pytest asyncio_mode=auto → async def test_* 직접 사용

검증 시나리오:
  1. 클라이언트 주입 → self.client 동일성
  2. 키 없이 생성 → RuntimeError
  3. get_candles → 6-튜플 (open,high,low,close,volume,timestamp) 파싱
  4. get_candles 예외 → 빈 리스트 fallback
  5. get_candles 빈 symbol → 빈 리스트
  6. get_candles limit 클램프 ([1,1500])
  7. _parse_klines 비정상 항목 스킵
  8. get_funding_rate 파싱 / 예외 → 0.0 fallback
  9. get_ticker_price 파싱 / 예외 → None
 10. get_open_interest 파싱 / 예외 → None
 11. get_24h_quote_volume 파싱 / 예외 → None
 12. WS 콜백 등록 + start_ws/stop_ws 스텁
=====================================================================
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from data.collector import BinanceDataCollector


# ── fixtures / helpers ──────────────────────────────────────────────

def _make_kline(open_time, o, h, low, c, v):
    """Binance futures_klines 항목 형태 (12-요소 리스트, 앞 7개만 사용)."""
    return [open_time, str(o), str(h), str(low), str(c), str(v), open_time + 1,
            "0", 0, "0", "0", "0"]


@pytest.fixture
def mock_client() -> MagicMock:
    """python-binance Client mock — 각 테스트가 return_value/side_effect 를 설정."""
    return MagicMock()


@pytest.fixture
def collector(mock_client) -> BinanceDataCollector:
    """주입 클라이언트 기반 collector."""
    return BinanceDataCollector(binance_client=mock_client)


# ── 1. 초기화 ───────────────────────────────────────────────────────

def test_injected_client_is_used(mock_client):
    """주입한 클라이언트가 self.client 로 그대로 노출된다."""
    c = BinanceDataCollector(binance_client=mock_client)
    assert c.client is mock_client


def _clear_key_env(monkeypatch):
    """4종 키 + USE_TESTNET 환경변수를 모두 제거 (테스트 격리)."""
    for name in (
        "BINANCE_API_KEY", "BINANCE_API_SECRET",
        "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET",
        "USE_TESTNET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_no_keys_raises_runtime_error(monkeypatch):
    """클라이언트 미주입 + 키 없음 (실거래 모드) → RuntimeError."""
    _clear_key_env(monkeypatch)
    with pytest.raises(RuntimeError, match="BINANCE_API_KEY"):
        BinanceDataCollector()


def test_use_testnet_env_overrides(monkeypatch, mock_client):
    """환경변수 USE_TESTNET 가 use_testnet 인자보다 우선한다."""
    monkeypatch.setenv("USE_TESTNET", "true")
    c = BinanceDataCollector(binance_client=mock_client, use_testnet=False)
    assert c.use_testnet is True


def _patch_fake_client(monkeypatch):
    """binance.client.Client 를 네트워크 없는 가짜로 교체하고 호출 인자를 캡처한다.

    CLAUDE.md 규칙(테스트는 실제 네트워크 호출 금지)에 맞춰, 키 선택 로직만
    결정론적으로 검증한다.
    """
    captured = {}

    class _FakeClient:
        def __init__(self, key, secret, testnet=False):
            captured["key"] = key
            captured["secret"] = secret
            captured["testnet"] = testnet

    import binance.client as binance_client_mod
    monkeypatch.setattr(binance_client_mod, "Client", _FakeClient)
    return captured


def test_live_keys_selected_when_not_testnet(monkeypatch):
    """use_testnet=False → BINANCE_API_KEY/SECRET 로 클라이언트 생성."""
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("BINANCE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "live-secret")
    captured = _patch_fake_client(monkeypatch)
    c = BinanceDataCollector(use_testnet=False)
    assert c.client is not None
    assert c.use_testnet is False
    assert captured["key"] == "live-key"
    assert captured["testnet"] is False


def test_testnet_keys_selected_when_testnet(monkeypatch):
    """use_testnet=True → BINANCE_TESTNET_API_KEY/SECRET 로 생성 (실거래 키 없어도)."""
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "testnet-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "testnet-secret")
    captured = _patch_fake_client(monkeypatch)
    c = BinanceDataCollector(use_testnet=True)
    assert c.client is not None
    assert c.use_testnet is True
    assert captured["key"] == "testnet-key"     # 감사 H4: live 키로 폴백하지 않음
    assert captured["testnet"] is True


def test_testnet_does_not_fall_back_to_live_keys(monkeypatch):
    """감사 H4: testnet 키 미설정 + 실거래 키 존재 → 폴백하지 않고 RuntimeError.

    live/test 자격증명 교차를 차단한다 (testnet 키 이름을 명시).
    """
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("BINANCE_API_KEY", "live-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "live-secret")
    with pytest.raises(RuntimeError, match="BINANCE_TESTNET_API_KEY"):
        BinanceDataCollector(use_testnet=True)


def test_testnet_missing_all_keys_raises(monkeypatch):
    """테스트넷 모드 + 키 전무 → RuntimeError (TESTNET 키 이름 명시)."""
    _clear_key_env(monkeypatch)
    with pytest.raises(RuntimeError, match="BINANCE_TESTNET_API_KEY"):
        BinanceDataCollector(use_testnet=True)


# ── 3~7. get_candles ────────────────────────────────────────────────

async def test_get_candles_parses_six_tuple(collector, mock_client):
    """kline 원본 → (open,high,low,close,volume,timestamp) 6-튜플."""
    mock_client.futures_klines.return_value = [
        _make_kline(1000, 10, 12, 9, 11, 100),
        _make_kline(2000, 11, 15, 10, 14, 200),
    ]
    candles = await collector.get_candles("BTCUSDT", "4h", 100)
    assert candles == [
        (10.0, 12.0, 9.0, 11.0, 100.0, 1000),
        (11.0, 15.0, 10.0, 14.0, 200.0, 2000),
    ]


async def test_get_candles_exception_returns_empty(collector, mock_client):
    """API 예외 → 빈 리스트 fallback (메인 루프 보호)."""
    mock_client.futures_klines.side_effect = RuntimeError("network down")
    assert await collector.get_candles("BTCUSDT", "4h", 100) == []


async def test_get_candles_empty_symbol_returns_empty(collector, mock_client):
    """빈 symbol → API 호출 없이 빈 리스트."""
    assert await collector.get_candles("", "4h", 100) == []
    mock_client.futures_klines.assert_not_called()


async def test_get_candles_limit_clamped(collector, mock_client):
    """limit 은 [1,1500] 으로 클램프되어 API 에 전달된다."""
    mock_client.futures_klines.return_value = []
    await collector.get_candles("BTCUSDT", "1h", 99999)
    assert mock_client.futures_klines.call_args.kwargs["limit"] == 1500
    await collector.get_candles("BTCUSDT", "1h", 0)
    assert mock_client.futures_klines.call_args.kwargs["limit"] == 1


async def test_get_candles_skips_malformed_item(collector, mock_client):
    """파싱 불가한 개별 항목은 스킵하고 나머지는 정상 반환한다."""
    mock_client.futures_klines.return_value = [
        _make_kline(1000, 10, 12, 9, 11, 100),
        ["bad", "data"],                       # 인덱스 부족 → 스킵
        [2000, "x", "y", "z", "w", "v", 2001],  # float 변환 실패 → 스킵
    ]
    candles = await collector.get_candles("BTCUSDT", "4h", 10)
    assert candles == [(10.0, 12.0, 9.0, 11.0, 100.0, 1000)]


# ── 8. get_funding_rate ─────────────────────────────────────────────

async def test_get_funding_rate_parses(collector, mock_client):
    """futures_mark_price 의 lastFundingRate 를 float 으로 반환한다."""
    mock_client.futures_mark_price.return_value = {"lastFundingRate": "0.00012345"}
    assert await collector.get_funding_rate("BTCUSDT") == pytest.approx(0.00012345)


async def test_get_funding_rate_exception_returns_zero(collector, mock_client):
    """API 예외 → 0.0 중립 fallback."""
    mock_client.futures_mark_price.side_effect = RuntimeError("fail")
    assert await collector.get_funding_rate("BTCUSDT") == 0.0


async def test_get_funding_rate_empty_symbol(collector, mock_client):
    """빈 symbol → 0.0, API 미호출."""
    assert await collector.get_funding_rate("") == 0.0
    mock_client.futures_mark_price.assert_not_called()


# ── 9. get_ticker_price ─────────────────────────────────────────────

async def test_get_ticker_price_parses(collector, mock_client):
    """futures_symbol_ticker 의 price 를 float 으로 반환한다."""
    mock_client.futures_symbol_ticker.return_value = {"price": "63250.5"}
    assert await collector.get_ticker_price("BTCUSDT") == pytest.approx(63250.5)


async def test_get_ticker_price_exception_returns_none(collector, mock_client):
    """API 예외 → None fallback."""
    mock_client.futures_symbol_ticker.side_effect = RuntimeError("fail")
    assert await collector.get_ticker_price("BTCUSDT") is None


# ── 10. get_open_interest ───────────────────────────────────────────

async def test_get_open_interest_parses(collector, mock_client):
    """futures_open_interest 의 openInterest 를 float 으로 반환한다."""
    mock_client.futures_open_interest.return_value = {"openInterest": "123456.78"}
    assert await collector.get_open_interest("BTCUSDT") == pytest.approx(123456.78)


async def test_get_open_interest_exception_returns_none(collector, mock_client):
    """API 예외 → None fallback."""
    mock_client.futures_open_interest.side_effect = RuntimeError("fail")
    assert await collector.get_open_interest("BTCUSDT") is None


# ── 11. get_24h_quote_volume ────────────────────────────────────────

async def test_get_24h_quote_volume_parses(collector, mock_client):
    """futures_ticker 의 quoteVolume 을 float 으로 반환한다."""
    mock_client.futures_ticker.return_value = {"quoteVolume": "9876543.21"}
    assert await collector.get_24h_quote_volume("BTCUSDT") == pytest.approx(9876543.21)


async def test_get_24h_quote_volume_exception_returns_none(collector, mock_client):
    """API 예외 → None fallback."""
    mock_client.futures_ticker.side_effect = RuntimeError("fail")
    assert await collector.get_24h_quote_volume("BTCUSDT") is None


# ── 12. WebSocket 콜백 훅 ───────────────────────────────────────────

def test_ws_callback_registration(collector):
    """kline/user 콜백 등록 훅이 내부 상태에 보관된다."""
    kline_cb = MagicMock()
    user_cb = MagicMock()
    collector.register_kline_callback(kline_cb)
    collector.register_user_callback(user_cb)
    assert collector._ws_kline_callback is kline_cb
    assert collector._ws_user_callback is user_cb


def test_start_stop_ws_stub_idempotent(collector):
    """start_ws/stop_ws 스텁은 멱등하며 _ws_started 플래그를 토글한다."""
    assert collector._ws_started is False
    collector.start_ws()
    assert collector._ws_started is True
    collector.start_ws()  # 재호출 안전
    assert collector._ws_started is True
    collector.stop_ws()
    assert collector._ws_started is False
    collector.stop_ws()  # 재호출 안전
    assert collector._ws_started is False
