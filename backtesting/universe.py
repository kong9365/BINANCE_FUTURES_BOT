"""
backtesting/universe.py
=====================================================================
유동성 유니버스 리졸버 (A2 — 유니버스 정합/확대 + 폭 스윕).

목적:
  "봇이 실제 거래 가능한 유니버스"를 거래소 24h 거래대금으로 객관 산출한다.
  테스트 유니버스를 라이브 유니버스에 맞추고(대표성), 슬리피지 티어를 유동성에
  연동하며, 유니버스 폭(top-N) 스윕으로 "좁은 유니버스의 기회비용"을 정량화한다.

설계:
  - 입력은 Binance USDⓈ-M futures 24h 티커(`futures_ticker`)뿐(공개·read-only).
  - USDT 무기한만, 24h 거래대금(quoteVolume) ≥ 임계, 보호종목 제외, 거래대금 내림차순.
  - 슬리피지 티어를 거래대금 밴드로 부여(유동성↑ = 비용↓):
      ≥$1B → tier1, ≥$300M → tier2, 그 외 → tier3.
  - CMC 시총 순위는 선택적 라벨(가용 시). 핵심 필터는 거래대금(=실제 비용/체결성).
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TIER1_VOL = 1_000_000_000.0   # >$1B
_TIER2_VOL = 300_000_000.0     # >$300M
_TIER3_VOL = 100_000_000.0     # >$100M (기존 봇 PairWhitelist 게이트)
_TIER4_VOL = 30_000_000.0      # >$30M  (광범위 유니버스 진입대, 0.30% 슬립)
# 그 외(≥$10M ~) → tier5(0.60% 슬립). $10M 미만은 호출자가 사전 필터(min_quote_volume_usd).


def volume_tier(quote_volume_usd: float) -> int:
    """24h 거래대금 → 슬리피지 티어(1 최유동 ~ 5 저유동, ExecConfig 와 정렬).

    2026-05-23: 광범위 유니버스($10M 가드) 도입에 따라 4/5 추가.
    """
    if quote_volume_usd >= _TIER1_VOL:
        return 1
    if quote_volume_usd >= _TIER2_VOL:
        return 2
    if quote_volume_usd >= _TIER3_VOL:
        return 3
    if quote_volume_usd >= _TIER4_VOL:
        return 4
    return 5


@dataclass
class UniverseEntry:
    symbol: str
    quote_volume_usd: float
    tier: int
    cmc_rank: Optional[int] = None


def resolve_liquid_universe(
    client,
    min_quote_volume_usd: float = 100_000_000.0,
    exclude: Optional[List[str]] = None,
    top_n: Optional[int] = None,
    cmc=None,
) -> List[UniverseEntry]:
    """거래소 24h 티커로 유동성 유니버스를 산출(거래대금 내림차순).

    Args:
        client: Binance 클라이언트(futures_ticker 보유).
        min_quote_volume_usd: 24h 거래대금 하한(기본 $100M — 봇 PairWhitelist 정합).
        exclude: 제외 심볼(보호종목 등).
        top_n: 상위 N만(폭 제한). None 이면 전체.
        cmc: 선택적 CMCClient(get_market_cap_rank) — rank 라벨링용.

    Returns:
        UniverseEntry 리스트(거래대금 내림차순).
    """
    exclude_set = set(exclude or [])
    try:
        raw = client.futures_ticker()
    except Exception as e:  # noqa: BLE001
        logger.error("[Universe] futures_ticker 실패: %s", e)
        return []

    rows: List[Tuple[str, float]] = []
    for t in raw or []:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT") or sym in exclude_set:
            continue
        try:
            qv = float(t.get("quoteVolume", 0.0))
        except (TypeError, ValueError):
            continue
        if qv < min_quote_volume_usd:
            continue
        rows.append((sym, qv))

    rows.sort(key=lambda x: -x[1])
    if top_n:
        rows = rows[:top_n]

    out: List[UniverseEntry] = []
    for sym, qv in rows:
        rank = None
        if cmc is not None:
            try:
                rank = cmc.get_market_cap_rank(sym.replace("USDT", ""))
            except Exception:  # noqa: BLE001
                rank = None
        out.append(UniverseEntry(symbol=sym, quote_volume_usd=qv,
                                  tier=volume_tier(qv), cmc_rank=rank))
    return out


def tiers_map(entries: List[UniverseEntry]) -> Dict[str, int]:
    """{symbol: tier} 맵(백테스트 비용용)."""
    return {e.symbol: e.tier for e in entries}
