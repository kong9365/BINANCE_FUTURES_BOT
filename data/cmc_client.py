"""
data/cmc_client.py
=====================================================================
CMCClient — CoinMarketCap 시가총액 순위 조회 (PairWhitelist Tier 3 검증용)

근거:
  - docs/SPEC_v3.1.md §8-7 (PairWhitelist — 시총 톱 N 검증)
  - strategy/pair_whitelist.py _validate_pair (self.cmc.get_market_cap_rank 호출 계약)

책임:
  - CoinMarketCap listings/latest 1회 호출로 {심볼: 시총순위} 맵 구축 + TTL 캐싱
  - get_market_cap_rank(symbol_base) 로 개별 코인 순위 조회

설계 메모:
  - PairWhitelist.refresh() 가 동기 메서드이므로 동기 클라이언트로 작성한다
    (urllib stdlib 사용 — requests/aiohttp 의존 추가 없음).
  - listings/latest 1회 호출로 상위 N개 순위 맵을 만들어, refresh() 한 번에서
    여러 페어를 조회해도 API 호출은 1회로 끝난다 (무료 플랜 credit 절약).
  - API 키는 HTTP 헤더로만 전송하고 로그·예외 메시지에 절대 노출하지 않는다
    (CLAUDE.md TIER 1 #4).
  - API 실패 시 캐시가 있으면 stale 캐시 유지, 없으면 빈 맵 → 조회는 None 반환.
    PairWhitelist._validate_pair 가 None 을 정상 처리(시총 검증 스킵)한다.
=====================================================================
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_CMC_LISTINGS_URL = (
    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
)
_CMC_GLOBAL_URL = (
    "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
)
_CMC_FNG_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"


class CMCClient:
    """CoinMarketCap 시가총액 순위 클라이언트.

    사용:
        cmc = CMCClient(api_key)
        rank = cmc.get_market_cap_rank("SOL")   # 예: 5
        if rank and rank > 50:
            ...  # 시총 50위 밖 → Tier 3 부적격

    상태:
        listings/latest 응답을 {심볼: cmc_rank} 맵으로 캐싱한다. cache_ttl
        이내 재조회는 캐시를 재사용한다.
    """

    def __init__(
        self,
        api_key: str,
        cache_ttl_seconds: float = 3600.0,
        listings_limit: int = 200,
        timeout_seconds: float = 15.0,
    ) -> None:
        """클라이언트 초기화.

        Args:
            api_key: CoinMarketCap Pro API 키. 헤더로만 전송된다.
            cache_ttl_seconds: 순위 맵 캐시 유효 시간 (초). 기본 1시간.
            listings_limit: listings/latest 로 가져올 상위 코인 수. 기본 200.
            timeout_seconds: HTTP 요청 타임아웃 (초).

        Raises:
            ValueError: api_key 가 비어 있을 때.
        """
        if not api_key:
            raise ValueError("CMCClient: api_key 가 비어 있습니다")
        self._api_key = api_key
        self.cache_ttl = cache_ttl_seconds
        self.listings_limit = max(1, int(listings_limit))
        self.timeout = timeout_seconds

        self._rank_map: dict[str, int] = {}
        self._map_fetched_at: float = 0.0

    def get_market_cap_rank(self, symbol_base: str) -> int | None:
        """코인 심볼(베이스)의 시가총액 순위를 반환한다.

        Args:
            symbol_base: 코인 베이스 심볼 (예: "SOL", "BTC" — "USDT" 제외).

        Returns:
            CMC 시총 순위 (1부터). 순위 맵에 없거나(상위 N 밖) 조회 실패 시 None.
        """
        if not symbol_base:
            return None
        self._maybe_refresh()
        return self._rank_map.get(symbol_base.upper())

    def get_global_metrics(self) -> dict | None:
        """글로벌 시장 지표(P5a) — BTC/ETH 도미넌스, 총 시총/거래량, F&G.

        바이낸스가 제공하지 않는 시장 전역 집계만 반환한다(가격/OHLCV 용도 아님).
        Fear&Greed 는 별도 엔드포인트(플랜에 따라 미제공) — best-effort, 실패 시 None.

        Returns:
            {btc_dominance, eth_dominance, total_market_cap_usd,
             total_volume_24h_usd, fear_greed} 또는 조회 실패 시 None.
        """
        try:
            payload = self._get_json(_CMC_GLOBAL_URL)
        except Exception as e:  # noqa: BLE001
            logger.error("[CMC] 글로벌 지표 조회 실패: %s", e)
            return None
        data = payload.get("data") or {}
        usd = (data.get("quote") or {}).get("USD") or {}
        out = {
            "btc_dominance": data.get("btc_dominance"),
            "eth_dominance": data.get("eth_dominance"),
            "total_market_cap_usd": usd.get("total_market_cap"),
            "total_volume_24h_usd": usd.get("total_volume_24h"),
            "fear_greed": None,
        }
        try:  # F&G best-effort (플랜 미지원 시 무시)
            fng = self._get_json(_CMC_FNG_URL)
            val = (fng.get("data") or {}).get("value")
            out["fear_greed"] = float(val) if val is not None else None
        except Exception:  # noqa: BLE001
            pass
        return out

    def _get_json(self, url: str) -> dict:
        """CMC GET → JSON dict. API 키는 헤더로만 전송(로그 노출 금지)."""
        req = urllib.request.Request(
            url,
            headers={
                "X-CMC_PRO_API_KEY": self._api_key,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — 고정 HTTPS
            payload = json.load(resp)
        if not isinstance(payload, dict):
            raise ValueError("CMC 응답이 dict 가 아님")
        return payload

    def _maybe_refresh(self) -> None:
        """캐시가 비었거나 TTL 이 지났으면 순위 맵을 다시 조회한다.

        조회 실패 시 기존 캐시(있으면)를 그대로 유지한다.
        """
        now = time.time()
        if self._rank_map and (now - self._map_fetched_at) < self.cache_ttl:
            return
        try:
            listings = self._fetch_listings()
        except Exception as e:  # noqa: BLE001 — 실패 시 stale 캐시 유지
            logger.error("[CMC] 순위 조회 실패: %s — 기존 캐시 유지", e)
            return

        rank_map: dict[str, int] = {}
        for item in listings:
            symbol = item.get("symbol")
            rank = item.get("cmc_rank")
            if symbol and isinstance(rank, int):
                # 동일 심볼 다수 존재 시 더 높은(작은) 순위 우선
                prev = rank_map.get(symbol.upper())
                if prev is None or rank < prev:
                    rank_map[symbol.upper()] = rank

        if rank_map:
            self._rank_map = rank_map
            self._map_fetched_at = now
            logger.info("[CMC] 시총 순위 맵 갱신: %d개 코인", len(rank_map))
        else:
            logger.warning("[CMC] 응답에서 순위를 파싱하지 못함 — 기존 캐시 유지")

    def _fetch_listings(self) -> list[dict]:
        """CoinMarketCap listings/latest 를 호출해 코인 목록을 반환한다.

        Returns:
            응답의 data 리스트 (각 항목에 symbol / cmc_rank 포함).

        Raises:
            urllib.error.URLError / HTTPError / ValueError 등 — 호출자가
            _maybe_refresh 에서 잡아 stale 캐시로 폴백한다.
        """
        url = (
            f"{_CMC_LISTINGS_URL}"
            f"?start=1&limit={self.listings_limit}&sort=market_cap"
        )
        req = urllib.request.Request(
            url,
            headers={
                "X-CMC_PRO_API_KEY": self._api_key,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — 고정 HTTPS 엔드포인트
            payload = json.load(resp)
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise ValueError("CMC 응답 'data' 가 리스트가 아님")
        return data
