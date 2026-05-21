"""
data/collector.py
=====================================================================
BinanceDataCollector — Binance USDT-M Futures 시장 데이터 수집 (REST + WS 훅)

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 1: 데이터 수집), §8-8-4 (WebSocket 콜백)
  - docs/SPEC_v3.1.md D-2 (데이터 흐름: WebSocket → Layer 1)

책임:
  - kline(4h/1h) / funding rate / ticker price / open interest REST 조회
  - python-binance 동기 클라이언트를 asyncio.to_thread 로 래핑 (메인 루프 비차단)
  - WebSocket 콜백 등록 훅 제공 (실 연결은 운영 환경의 백그라운드 태스크가 담당,
    드라이런·테스트는 REST 폴링만 사용)

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지"로만 표기하고 본문 코드를 제공하지 않으므로,
    §8-8 / §8-8-4 / D-2 에서 추출한 호출 계약 기준으로 신규 작성한다.
  - get_candles() 반환 포맷은 RegimeDetector.detect() 가 기대하는
    (open, high, low, close, volume, timestamp) 6-튜플 (strategy/regime_detector.py 참조).
  - API 키·시크릿은 코드·로그·예외 어디에도 노출하지 않는다 (CLAUDE.md TIER 1 #4).
  - 외부 호출은 모두 try/except + fallback (빈 리스트 / 직전값 / None).
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

logger = logging.getLogger(__name__)

# kline limit 허용 범위 (Binance Futures API 상한 1500)
_MIN_KLINE_LIMIT = 1
_MAX_KLINE_LIMIT = 1500


class BinanceDataCollector:
    """Binance USDT-M Futures 시장 데이터 수집기.

    사용:
        # 1) 외부에서 생성한 클라이언트 주입 (테스트·메인 통합 권장)
        collector = BinanceDataCollector(binance_client=client)

        # 2) .env 의 BINANCE_API_KEY / BINANCE_API_SECRET 로 자동 생성
        collector = BinanceDataCollector(use_testnet=True)

        candles_4h = await collector.get_candles("BTCUSDT", "4h", 100)
        funding = await collector.get_funding_rate("BTCUSDT")

    Attributes:
        client: python-binance Client 인스턴스. main_7590.py 가
            `self.binance = collector.client` 형태로 공유한다.
    """

    def __init__(
        self,
        binance_client=None,
        use_testnet: bool = False,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        """수집기 초기화.

        Args:
            binance_client: 외부에서 생성한 python-binance Client. 주어지면
                그대로 사용하고 키 기반 생성을 건너뛴다 (테스트·통합 권장 경로).
            use_testnet: True 면 Binance Futures Testnet 에 연결한다.
                None 이 아닌 환경변수 USE_TESTNET 가 있으면 그쪽이 우선.
            api_key: API 키. None 이면 환경변수에서 읽는다 (use_testnet 에 따라
                테스트넷/실거래 키를 자동 선택 — _build_client 참조).
            api_secret: API 시크릿. None 이면 환경변수에서 읽는다.

        Raises:
            RuntimeError: binance_client 미주입 + 키 없음 + 클라이언트 생성 실패 시.
        """
        env_testnet = os.environ.get("USE_TESTNET")
        if env_testnet is not None:
            use_testnet = env_testnet.strip().lower() in ("1", "true", "yes")
        self.use_testnet = use_testnet

        # WebSocket 콜백 훅 (§8-8-4) — 실 연결 전까지 None
        self._ws_kline_callback: Callable | None = None
        self._ws_user_callback: Callable | None = None
        self._ws_started = False

        # 상장폐지/정산 종목 스킵 집합 — 첫 -4108(또는 price 누락) 감지 시 등록하여
        # 이후 스캔에서 OI/시세 조회를 건너뛴다(매 스캔 ERROR 로그 노이즈 + 불필요한
        # API 호출 방지).
        self._delisted_symbols: set[str] = set()

        if binance_client is not None:
            self.client = binance_client
            logger.info("[Collector] 외부 주입 클라이언트 사용 (testnet=%s)", use_testnet)
            return

        self.client = self._build_client(api_key, api_secret, use_testnet)

    def _build_client(
        self, api_key: str | None, api_secret: str | None, use_testnet: bool
    ):
        """python-binance Client 를 키 기반으로 생성한다.

        환경변수 키 선택 (use_testnet 기준):
          - use_testnet=True  → BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
            (감사 H4: 미설정 시 live 키로 폴백하지 않고 RuntimeError — 자격증명 교차 차단)
          - use_testnet=False → BINANCE_API_KEY / BINANCE_API_SECRET
        실거래/테스트넷 키는 서로 다른 발급처(binance.com vs
        testnet.binancefuture.com)의 별개 자격증명이므로 분리해 둔다.
        명시적으로 전달된 api_key/api_secret 은 환경변수보다 우선한다.

        키 값 자체는 로그·예외 메시지에 절대 포함하지 않는다 (TIER 1 #4).

        Returns:
            binance.client.Client 인스턴스.

        Raises:
            RuntimeError: 키 누락 또는 클라이언트 생성 실패 시 (원인 메시지는
                키 값을 포함하지 않도록 유형만 노출).
        """
        if use_testnet:
            # 감사 H4: testnet 키가 없을 때 live 키로 폴백하지 않는다.
            # live/test 자격증명 교차(실수로 실거래 키가 사용되는 사고)를 차단한다.
            key = api_key or os.environ.get("BINANCE_TESTNET_API_KEY")
            secret = api_secret or os.environ.get("BINANCE_TESTNET_API_SECRET")
            key_names = "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET"
        else:
            key = api_key or os.environ.get("BINANCE_API_KEY")
            secret = api_secret or os.environ.get("BINANCE_API_SECRET")
            key_names = "BINANCE_API_KEY / BINANCE_API_SECRET"

        if not key or not secret:
            raise RuntimeError(
                f"{key_names} 누락 — .env 설정 필요 "
                f"(또는 binance_client 직접 주입). use_testnet={use_testnet}"
            )
        try:
            # 지연 import: 테스트는 binance_client 주입 경로만 쓰므로
            # python-binance 미설치 환경에서도 모듈 import 가 깨지지 않게 한다.
            from binance.client import Client

            client = Client(key, secret, testnet=use_testnet)
            logger.info("[Collector] 키 기반 클라이언트 생성 완료 (testnet=%s)", use_testnet)
            return client
        except Exception as e:
            logger.error("[Collector] 클라이언트 생성 실패: %s", type(e).__name__)
            raise RuntimeError(
                f"Binance 클라이언트 생성 실패: {type(e).__name__}"
            ) from None

    # ── REST 조회 ───────────────────────────────────────────────

    async def get_candles(
        self, symbol: str, interval: str = "4h", limit: int = 100
    ) -> list[tuple]:
        """USDT-M Futures kline 캔들을 조회한다.

        Args:
            symbol: 거래 페어 (예: "BTCUSDT").
            interval: 캔들 간격 (예: "1h", "4h").
            limit: 캔들 개수. [1, 1500] 범위로 클램프된다.

        Returns:
            (open, high, low, close, volume, timestamp) 6-튜플 리스트
            (시간 오름차순, 최신이 마지막). 조회 실패 시 빈 리스트.
            RegimeDetector.detect() 가 기대하는 포맷이다.
        """
        if not symbol:
            logger.warning("[Collector] get_candles: symbol 비어 있음 — 빈 리스트 반환")
            return []
        limit = max(_MIN_KLINE_LIMIT, min(int(limit), _MAX_KLINE_LIMIT))

        try:
            raw = await asyncio.to_thread(
                self.client.futures_klines,
                symbol=symbol,
                interval=interval,
                limit=limit,
            )
        except Exception as e:
            logger.error("[Collector] %s %s kline 조회 실패: %s", symbol, interval, e)
            return []

        return self._parse_klines(raw, symbol, interval)

    @staticmethod
    def _parse_klines(raw, symbol: str, interval: str) -> list[tuple]:
        """Binance futures_klines 원본 응답을 6-튜플 리스트로 변환한다.

        Binance kline 항목 인덱스: [0]openTime [1]open [2]high [3]low
        [4]close [5]volume [6]closeTime ...

        파싱 불가한 개별 항목은 건너뛰고 경고만 남긴다 (전체 실패로 번지지 않게).
        """
        candles: list[tuple] = []
        for item in raw or []:
            try:
                candles.append((
                    float(item[1]),   # open
                    float(item[2]),   # high
                    float(item[3]),   # low
                    float(item[4]),   # close
                    float(item[5]),   # volume
                    int(item[0]),     # timestamp (openTime, ms)
                ))
            except (IndexError, ValueError, TypeError) as e:
                logger.warning(
                    "[Collector] %s %s kline 항목 파싱 실패: %s", symbol, interval, e
                )
        return candles

    async def get_funding_rate(self, symbol: str) -> float:
        """현재(최근) 펀딩비를 조회한다.

        Args:
            symbol: 거래 페어.

        Returns:
            최근 펀딩비 (예: 0.0001 = 0.01%/8h). 조회 실패 시 0.0 (중립 fallback).
        """
        if not symbol:
            logger.warning("[Collector] get_funding_rate: symbol 비어 있음 — 0.0 반환")
            return 0.0
        try:
            data = await asyncio.to_thread(
                self.client.futures_mark_price, symbol=symbol
            )
            return float(data["lastFundingRate"])
        except Exception as e:
            logger.error("[Collector] %s 펀딩비 조회 실패: %s — 0.0 fallback", symbol, e)
            return 0.0

    async def get_ticker_price(self, symbol: str) -> float | None:
        """현재 시세(마지막 체결가)를 조회한다.

        Args:
            symbol: 거래 페어.

        Returns:
            현재가 (float). 조회 실패 시 None.
        """
        if not symbol:
            logger.warning("[Collector] get_ticker_price: symbol 비어 있음 — None 반환")
            return None
        if symbol in self._delisted_symbols:
            return None   # 상장폐지/정산 종목 — 조용히 스킵
        try:
            data = await asyncio.to_thread(
                self.client.futures_symbol_ticker, symbol=symbol
            )
            return float(data["price"])
        except Exception as e:
            self._note_fetch_error(symbol, "시세", e)
            return None

    async def get_open_interest(self, symbol: str) -> float | None:
        """현재 미결제약정(Open Interest) 총량을 조회한다.

        Args:
            symbol: 거래 페어.

        Returns:
            OI 총량 (계약 수, float). 조회 실패 시 None.
        """
        if not symbol:
            logger.warning("[Collector] get_open_interest: symbol 비어 있음 — None 반환")
            return None
        if symbol in self._delisted_symbols:
            return None   # 상장폐지/정산 종목 — 조용히 스킵
        try:
            data = await asyncio.to_thread(
                self.client.futures_open_interest, symbol=symbol
            )
            return float(data["openInterest"])
        except Exception as e:
            self._note_fetch_error(symbol, "OI", e)
            return None

    async def get_open_interest_history(
        self, symbol: str, period: str = "15m", limit: int = 2
    ) -> list[float]:
        """OI 이력(sumOpenInterest)을 오래된→최신 순으로 반환한다.

        Binance futures_open_interest_hist(period: 5m/15m/30m/1h/2h/4h/6h/12h/1d)
        를 사용해 룩백 윈도우 기준 OI 변화율 산출에 쓴다(직전 스캔 30초가 아니라
        의미 있는 시간 창으로 비교하기 위함).

        Args:
            symbol: 거래 페어.
            period: OI 집계 주기(예: "15m").
            limit: 가져올 데이터 포인트 수.

        Returns:
            sumOpenInterest float 리스트(오래된→최신). 실패/없음 시 빈 리스트.
        """
        if not symbol:
            logger.warning("[Collector] get_open_interest_history: symbol 비어 있음")
            return []
        if symbol in self._delisted_symbols:
            return []   # 상장폐지/정산 종목 — 조용히 스킵
        try:
            raw = await asyncio.to_thread(
                self.client.futures_open_interest_hist,
                symbol=symbol, period=period, limit=limit,
            )
            return [float(d["sumOpenInterest"]) for d in (raw or [])]
        except Exception as e:
            self._note_fetch_error(symbol, "OI 이력", e)
            return []

    @staticmethod
    def _is_delisted_error(e: Exception) -> bool:
        """예외가 상장폐지/정산/거래중지 신호인지 판별한다.

        Binance -4108(Symbol is on delivering/delivered/settling/closed/pre-trading)
        또는 시세 응답에 'price' 키가 없는 경우(삭제된 심볼)를 포괄한다.
        """
        s = str(e).lower()
        return (
            "-4108" in s
            or "delivering" in s
            or "delivered" in s
            or "settling" in s
            or "closed or pre-trading" in s
            or s == "'price'"           # KeyError('price') — 삭제 심볼 ticker 응답
            or s == "'openinterest'"    # KeyError('openInterest')
        )

    def _note_fetch_error(self, symbol: str, kind: str, e: Exception) -> None:
        """조회 실패를 로깅한다. 상장폐지/정산이면 1회 WARNING 후 스킵 등록(노이즈 차단),
        그 외(일시적 네트워크 등)는 ERROR 로 남긴다.
        """
        if self._is_delisted_error(e):
            if symbol not in self._delisted_symbols:
                self._delisted_symbols.add(symbol)
                logger.warning(
                    "[Collector] %s 상장폐지/정산 종목으로 판단 — 이후 스캔에서 제외: %s",
                    symbol, e,
                )
            return  # 이미 등록됐으면 조용히
        logger.error("[Collector] %s %s 조회 실패: %s", symbol, kind, e)

    async def get_24h_quote_volume(self, symbol: str) -> float | None:
        """24시간 거래대금(quote volume, USDT)을 조회한다.

        Args:
            symbol: 거래 페어.

        Returns:
            24h quote volume (USDT, float). 조회 실패 시 None.
        """
        if not symbol:
            logger.warning("[Collector] get_24h_quote_volume: symbol 비어 있음 — None")
            return None
        try:
            data = await asyncio.to_thread(
                self.client.futures_ticker, symbol=symbol
            )
            return float(data["quoteVolume"])
        except Exception as e:
            logger.error("[Collector] %s 24h 거래대금 조회 실패: %s", symbol, e)
            return None

    # ── WebSocket 콜백 훅 (§8-8-4) ──────────────────────────────
    #
    # 명세서 §8-8-4 는 main_7590.py 측의 _on_ws_kline / _on_ws_user_data
    # 콜백만 정의한다. collector 는 콜백을 보관하는 등록 훅과 start/stop
    # 스텁만 제공하고, 실제 WS 스레드 연결은 운영 환경의 백그라운드 태스크가
    # 담당한다. 드라이런·테스트는 REST 폴링만 사용하므로 WS 없이도 동작한다.

    def register_kline_callback(self, callback: Callable) -> None:
        """WS kline 메시지 수신 콜백을 등록한다 (§8-8-4 _on_ws_kline)."""
        self._ws_kline_callback = callback
        logger.info("[Collector] WS kline 콜백 등록")

    def register_user_callback(self, callback: Callable) -> None:
        """WS userData 메시지 수신 콜백을 등록한다 (§8-8-4 _on_ws_user_data)."""
        self._ws_user_callback = callback
        logger.info("[Collector] WS userData 콜백 등록")

    def start_ws(self) -> None:
        """WebSocket 스트림 시작 (운영 환경 전용 스텁).

        실제 ThreadedWebsocketManager 연결은 운영 배포 시 이 메서드를
        구현·확장한다. 드라이런·테스트에서는 REST 폴링만 사용하므로
        호출되더라도 안전하게 no-op + 경고 로깅만 수행한다.
        """
        if self._ws_started:
            logger.debug("[Collector] start_ws: 이미 시작됨")
            return
        self._ws_started = True
        logger.warning(
            "[Collector] start_ws 스텁 — 실 WS 미연결. "
            "메인 루프는 REST 폴링으로 동작합니다."
        )

    def stop_ws(self) -> None:
        """WebSocket 스트림 정지 (스텁). 시작된 적 없으면 no-op."""
        if not self._ws_started:
            return
        self._ws_started = False
        logger.info("[Collector] stop_ws — WS 스텁 정지")
