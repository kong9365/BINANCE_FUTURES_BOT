"""
data/microstructure_universe.py
=====================================================================
Dynamic Tiered Universe Resolver (Microstructure Phase 2 데이터 누적).

* COLLECTION universe 전용 *. *실거래 universe 와 분리* — 보호종목(BTCUSDT/ETHUSDT
등)도 수집은 가능. 실거래 후보 산출에는 *절대 사용 금지* (PairWhitelist.protected_symbols
정책 그대로 유지).

설계 (운영자 critique 반영):
  - Tier 0 (core): 항상 포함 (BTC/ETH/SOL/XRP/DOGE/BNB) — 시장 기준선·BTC risk-off·상관
  - Tier 1 (full microstructure): 24h quoteVolume 상위 N (depth + aggTrade + forceOrder)
  - Tier 2 (light microstructure): 다음 M (aggTrade + forceOrder만 — depth 없음)
  - Tier 3 (monitor only): 기존 OHLCV/OI/Funding 경로 (이 모듈은 분류만, 수집은 안 함)

필터:
  - exchangeInfo: contractType=PERPETUAL AND quoteAsset=USDT AND status=TRADING
  - commodity-like exact symbol set (XAUUSDT/XAGUSDT/CLUSDT/BZUSDT) 기본 제외
    (include_commodity_like=True 면 별도 group 으로 포함)

설계 원칙:
  - 순수 함수(client / exchange_info / ticker_data 주입 가능 — 테스트 mock).
  - Resolver version 명시(향후 알고리즘 변경 시 lineage 추적).
  - Snapshot 메타 저장 — collection_universe_snapshots 테이블 (Phase 3 IC 분석용 lineage).

알파/IC/R0/진입 로직 *없음*. 수집 universe 결정 + lineage 저장만.
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ── Resolver 버전 (lineage 용 — 알고리즘 변경 시 bump) ──
RESOLVER_VERSION = "v1"

# ── Stream type 상수 ─────────────────────────────────────
STREAM_DEPTH = "depth"
STREAM_AGG_TRADE = "aggTrade"
STREAM_FORCE_ORDER = "forceOrder"

TIER1_FULL: Tuple[str, ...] = (STREAM_DEPTH, STREAM_AGG_TRADE, STREAM_FORCE_ORDER)
TIER2_LIGHT: Tuple[str, ...] = (STREAM_AGG_TRADE, STREAM_FORCE_ORDER)

# ── Tier 라벨 ────────────────────────────────────────────
TIER_CORE = "tier0"     # 항상 포함
TIER_FULL = "tier1"     # depth + agg + force
TIER_LIGHT = "tier2"    # agg + force
TIER_MONITOR = "tier3"  # OHLCV/OI/Funding 만 (이 모듈은 미수집)

# ── 카테고리 ─────────────────────────────────────────────
CATEGORY_CRYPTO = "crypto"
CATEGORY_COMMODITY_LIKE = "commodity_like"


@dataclass(frozen=True)
class UniverseSymbol:
    """Tiered universe entry — collection lineage 의 단위."""

    symbol: str
    tier: str
    quote_volume: Optional[float]
    category: str
    stream_types: Tuple[str, ...]


@dataclass
class MicrostructureUniverseConfig:
    """Tiered universe 사전확정 설정.

    실제 sanity 검증은 처음 tier1=15, tier2=30 부터 (CLI override). 기본은 plan 명시.
    """

    tier1_full_count: int = 20
    tier2_light_count: int = 50
    tier3_monitor_count: int = 100      # 이 모듈은 분류만, 수집은 기존 collect_oi
    include_commodity_like: bool = False
    refresh_interval_hours: int = 24

    # Tier 0 핵심 (시장 기준선 — 거래대금 순위 무관 항상 포함)
    core_symbols: Tuple[str, ...] = (
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    )

    # commodity-like exact set (운영자 critique #3 반영 — prefix 단독 사용 금지)
    commodity_like_exact_symbols: Tuple[str, ...] = (
        "XAUUSDT", "XAGUSDT", "CLUSDT", "BZUSDT",
    )

    # 선택: 7일 이상 상장된 심볼만 포함 (exchangeInfo.onboardDate 기반)
    min_listing_days: int = 0   # 0 = 미사용(데이터 부족 시 옵션)

    # 데이터 source 라벨 (lineage)
    source: str = "binance_futures_ticker"


# 운영자 기본 인스턴스(import 편의)
MICROSTRUCTURE_UNIVERSE_CONFIG = MicrostructureUniverseConfig()


# ── 헬퍼: 필터 ───────────────────────────────────────────
def _is_eligible_perpetual(symbol_info: Dict[str, Any]) -> bool:
    """exchangeInfo entry 가 PERPETUAL + USDT + TRADING 인지."""
    return (
        symbol_info.get("contractType") == "PERPETUAL"
        and symbol_info.get("quoteAsset") == "USDT"
        and symbol_info.get("status") == "TRADING"
    )


def _categorize(symbol: str, cfg: MicrostructureUniverseConfig) -> str:
    """심볼 카테고리 (crypto / commodity_like)."""
    if symbol in set(cfg.commodity_like_exact_symbols):
        return CATEGORY_COMMODITY_LIKE
    return CATEGORY_CRYPTO


def _listed_days(symbol_info: Dict[str, Any]) -> Optional[float]:
    """exchangeInfo entry → 상장 일수 (None 이면 미상)."""
    onboard_ms = symbol_info.get("onboardDate")
    if onboard_ms is None or onboard_ms == 0:
        return None
    try:
        onboard_dt = datetime.fromtimestamp(int(onboard_ms) / 1000.0, tz=timezone.utc)
        return (datetime.now(timezone.utc) - onboard_dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


# ── 핵심 resolver ───────────────────────────────────────
def resolve_tiered_universe(
    exchange_info: Dict[str, Any],
    ticker_data: List[Dict[str, Any]],
    cfg: Optional[MicrostructureUniverseConfig] = None,
) -> List[UniverseSymbol]:
    """Tiered universe 산출 (순수 함수, 테스트 가능).

    Args:
        exchange_info: Binance `futures_exchange_info()` 응답 (또는 mock).
                       반드시 `symbols` 키에 list of dict.
        ticker_data: Binance `futures_ticker()` 응답 (또는 mock).
                     각 dict 는 `symbol`, `quoteVolume` 키 필수.
        cfg: 설정.

    Returns:
        Tier 0 → Tier 1 → Tier 2 순서의 UniverseSymbol 리스트.
        중복 (core ∩ Tier 1 등) 은 *Tier 0 → 1 → 2 우선순위* 로 자동 제거.

    노트:
        - commodity-like 는 include_commodity_like=False 면 *Tier 1/2 후보에서 제외*
          (Tier 0 core 는 commodity 가 아니므로 영향 없음).
        - 가용 universe < tier1+tier2 면 가능한 만큼만 채움(에러 안 던짐).
        - Tier 3 (monitor-only) 는 이 함수가 *반환 안 함* — 분류는 운영자가
          collect_oi 의 OI_COLLECT_SYMBOLS 로 별도 관리.
    """
    cfg = cfg or MICROSTRUCTURE_UNIVERSE_CONFIG

    # Stage 1: exchangeInfo 로 유효 PERPETUAL/USDT/TRADING 심볼 화이트리스트
    valid_symbols: Dict[str, Dict[str, Any]] = {}
    for s in exchange_info.get("symbols", []):
        if _is_eligible_perpetual(s):
            valid_symbols[s["symbol"]] = s

    # Stage 2: 24h ticker → quoteVolume map (valid_symbols 만)
    quote_volumes: Dict[str, float] = {}
    for t in ticker_data:
        sym = t.get("symbol")
        if sym in valid_symbols:
            try:
                quote_volumes[sym] = float(t.get("quoteVolume", 0) or 0)
            except (ValueError, TypeError):
                continue

    # Stage 3: Tier 0 (core) — 항상 포함, exchangeInfo 통과 시
    out: List[UniverseSymbol] = []
    seen: set[str] = set()
    for sym in cfg.core_symbols:
        if sym not in valid_symbols:
            logger.warning(
                "[Universe] core_symbol %s 가 PERPETUAL/USDT/TRADING 통과 못 함 — 스킵", sym
            )
            continue
        cat = _categorize(sym, cfg)
        out.append(UniverseSymbol(
            symbol=sym, tier=TIER_CORE,
            quote_volume=quote_volumes.get(sym),
            category=cat, stream_types=TIER1_FULL,
        ))
        seen.add(sym)

    # Stage 4: 후보 정렬 (crypto-only 우선, commodity 옵션 시 포함)
    # 후보 = (valid_symbols ∩ ticker_data 존재) - core
    # ticker 에 없는 심볼은 거래 활동 미상 → 후보 제외 (랭킹 의미 없음)
    candidates: List[Tuple[str, float, str]] = []
    for sym in valid_symbols:
        if sym in seen:
            continue
        if sym not in quote_volumes:
            continue   # ticker 미존재 → 활성 거래 없음 추정, 후보 제외
        qv = quote_volumes[sym]
        cat = _categorize(sym, cfg)
        if cat == CATEGORY_COMMODITY_LIKE and not cfg.include_commodity_like:
            continue
        # 선택: 상장 일수 필터 (운영자 optional)
        if cfg.min_listing_days > 0:
            ld = _listed_days(valid_symbols[sym])
            if ld is not None and ld < cfg.min_listing_days:
                continue
        candidates.append((sym, qv, cat))

    candidates.sort(key=lambda x: x[1], reverse=True)

    # Stage 5: Tier 1 (full)
    added_t1 = 0
    for sym, qv, cat in candidates:
        if added_t1 >= cfg.tier1_full_count:
            break
        if sym in seen:
            continue
        out.append(UniverseSymbol(
            symbol=sym, tier=TIER_FULL, quote_volume=qv,
            category=cat, stream_types=TIER1_FULL,
        ))
        seen.add(sym)
        added_t1 += 1

    # Stage 6: Tier 2 (light)
    added_t2 = 0
    for sym, qv, cat in candidates:
        if added_t2 >= cfg.tier2_light_count:
            break
        if sym in seen:
            continue
        out.append(UniverseSymbol(
            symbol=sym, tier=TIER_LIGHT, quote_volume=qv,
            category=cat, stream_types=TIER2_LIGHT,
        ))
        seen.add(sym)
        added_t2 += 1

    return out


# ── Helpers (collector 통합용) ──────────────────────────
def symbols_by_stream(universe: Sequence[UniverseSymbol]) -> Dict[str, List[str]]:
    """{stream_type: [symbol, ...]} 매핑 (collector 구독 빌드용).

    예: {"depth": [BTC, ETH, ...20개], "aggTrade": [...70개], "forceOrder": [...]}
    """
    out: Dict[str, List[str]] = {STREAM_DEPTH: [], STREAM_AGG_TRADE: [],
                                  STREAM_FORCE_ORDER: []}
    for u in universe:
        for st in u.stream_types:
            out.setdefault(st, []).append(u.symbol)
    # 중복 제거 (Tier 0 + Tier 1 동일 심볼 가능성)
    for k in list(out.keys()):
        out[k] = sorted(set(out[k]))
    return out


def build_symbol_streams_dict(
    universe: Sequence[UniverseSymbol],
) -> Dict[str, Tuple[str, ...]]:
    """{symbol: stream_types_tuple} — collector 의 symbol_streams 인자용.

    중복 심볼 (core ∩ tier1) 은 *더 풍부한* stream_types 로 합산.
    """
    out: Dict[str, Tuple[str, ...]] = {}
    for u in universe:
        if u.symbol in out:
            merged = tuple(sorted(set(out[u.symbol]) | set(u.stream_types)))
            out[u.symbol] = merged
        else:
            out[u.symbol] = tuple(u.stream_types)
    return out


def build_multiplex_streams(
    symbol_streams: Dict[str, Tuple[str, ...]],
) -> List[str]:
    """Binance multiplex stream URL 조각 리스트.

    예: ["btcusdt@depth20@100ms", "btcusdt@aggTrade", "ethusdt@aggTrade",
         "!forceOrder@arr"]

    multiplex 1 연결 → DNS/TLS handshake 1회 (운영자 critique #2 반영).
    """
    streams: List[str] = []
    has_force_order = False
    for sym, types in symbol_streams.items():
        sym_lower = sym.lower()
        if STREAM_DEPTH in types:
            # USDT-M Partial Book Depth — Binance combined stream 은 rate suffix 없는
            # `<symbol>@depth20` 형태만 수용 (probe 검증). raw websockets 사용.
            streams.append(f"{sym_lower}@depth20")
        if STREAM_AGG_TRADE in types:
            # Binance USDT-M `@aggTrade` 가 raw connection 에서 0건 반환(Binance 측
            # throttle/region 추정 — Phase 2 probe 검증). `@trade` (raw trade) 가 더
            # 안정적이며 더 granular(every trade). 동일 의미(CVD 산출 가능)이므로
            # 의미 label `aggTrade` 유지하고 URL 만 `@trade` 사용.
            streams.append(f"{sym_lower}@trade")
        if STREAM_FORCE_ORDER in types:
            has_force_order = True
    if has_force_order:
        streams.append("!forceOrder@arr")
    return streams


# ── Lineage 저장 ────────────────────────────────────────
def save_universe_snapshot(
    universe: Sequence[UniverseSymbol],
    persist,
    cfg: Optional[MicrostructureUniverseConfig] = None,
    snapshot_ts: Optional[datetime] = None,
) -> bool:
    """Supabase `collection_universe_snapshots` 적재 (Phase 3 IC 분석 lineage).

    Args:
        universe: resolver 결과.
        persist: SupabasePersistence (data/persistence.py).
        cfg: source 라벨 가져옴.
        snapshot_ts: 강제 시각 (테스트). None 이면 now().

    Returns:
        성공 여부 (실패 시 outbox 폴백, False 반환).
    """
    cfg = cfg or MICROSTRUCTURE_UNIVERSE_CONFIG
    ts = (snapshot_ts or datetime.now(timezone.utc)).isoformat()
    rows = []
    for u in universe:
        rows.append({
            "ts": ts,
            "tier": u.tier,
            "symbol": u.symbol,
            "quote_volume": float(u.quote_volume) if u.quote_volume is not None else None,
            "category": u.category,
            "stream_types": list(u.stream_types),
            "source": cfg.source,
            "resolver_version": RESOLVER_VERSION,
        })
    if not rows:
        return True
    try:
        return persist.upsert_many(
            "collection_universe_snapshots", rows,
            "ts,symbol",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Universe] snapshot 적재 실패: %s", e)
        return False


# ── Binance API wrapper (실 호출 — CLI 에서만 사용) ────
def fetch_exchange_info_and_ticker(client) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Binance AsyncClient 가 아닌 sync Client (python-binance) 로 단일 호출.

    Returns:
        (exchange_info, ticker_data).
    """
    ei = client.futures_exchange_info()
    tk = client.futures_ticker()
    return ei, tk
