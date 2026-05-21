"""
backtesting/collect_oi.py
=====================================================================
OI + OHLCV 누적 수집 CLI — OI-급증 전략 검증용 데이터 적재(스케줄 실행).

배경:
  Binance OI 이력은 ~30일만 제공되므로, 본 스크립트를 **주기적으로**(예: 1시간
  마다) 실행해 OI 시계열을 forward 누적한다(backtesting/data_loader.merge_into).
  수개월 누적 후 OI-급증 전략을 walk-forward 로 검증한다(명세 §10-4).

실행:
  python -m backtesting.collect_oi [--interval 1h] [--oi-period 1h] [--limit 500]

스케줄 (운영자, OS 레벨 권장 — Claude 미실행 중에도 무인 누적):
  Windows Task Scheduler:
    schtasks /Create /TN BinanceOICollect /SC HOURLY /TR ^
      "cmd /c cd /d <repo> && python -m backtesting.collect_oi >> logs\\oi_collect.log 2>&1"
  cron (Linux/macOS):
    0 * * * * cd <repo> && /usr/bin/python -m backtesting.collect_oi >> logs/oi_collect.log 2>&1

주의:
  - OI/klines 는 공개 market data(주문 없음, read-only). 실거래 키가 있으면
    read-only 로 사용한다(USE_TESTNET 무관 — 실시장 OI 가 필요).
  - 수집 종목은 OI_COLLECT_SYMBOLS 환경변수(콤마구분) 우선, 없으면 기본 목록.
  - httpx/httpcore INFO 로그 억제(텔레그램 토큰과 무관하나 일관성 유지).
=====================================================================
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backtesting import data_loader as dl

logger = logging.getLogger(__name__)

# 시간별 스케줄 실행 시 Supabase 에 적재할 최근 봉 수(겹침 포함). UNIQUE upsert 라
# 멱등 — 1시간 갭 + 여유를 덮는다. --backfill 은 전체를 1회 시딩.
_DEFAULT_PUSH_RECENT = 48

# 시장-베타 인덱스(P5a) — 거래 아님, OHLCV read-only 인덱스로만 적재(보호종목 거래 금지 불변).
_INDEX_SYMBOLS: List[str] = ["BTCUSDT", "ETHUSDT"]

# 기본 수집 유니버스 — 유동성 높은 비보호 USDT 무기한(보호종목 제외).
_DEFAULT_OI_COLLECT_SYMBOLS: List[str] = [
    "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "BNBUSDT", "AVAXUSDT",
    "LINKUSDT", "TRXUSDT", "DOTUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT",
]


def resolve_symbols() -> List[str]:
    """OI_COLLECT_SYMBOLS 환경변수(콤마구분) 우선, 비어 있으면 기본 목록."""
    raw = os.environ.get("OI_COLLECT_SYMBOLS")
    if raw:
        syms = [s for s in raw.replace(" ", "").split(",") if s]
        if syms:
            return syms
    return list(_DEFAULT_OI_COLLECT_SYMBOLS)


def _build_live_client():
    """실시장 OI/klines 수집용 live read-only 클라이언트(주문 없음)."""
    from binance.client import Client
    return Client(
        os.environ.get("BINANCE_API_KEY"),
        os.environ.get("BINANCE_API_SECRET"),
        testnet=False,
    )


def _build_persistence():
    """Supabase 주저장 + 로컬 폴백 어댑터(env 기반). 키 미설정 시 outbox 전용."""
    from data.persistence import SupabasePersistence
    return SupabasePersistence()


def _build_cmc():
    """CMC 클라이언트(env CMC_API_KEY). 키 없으면 None(글로벌 지표 스킵)."""
    key = os.environ.get("CMC_API_KEY")
    if not key:
        return None
    try:
        from data.cmc_client import CMCClient
        return CMCClient(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("[CollectOI] CMC 클라이언트 생성 실패: %s", e)
        return None


def push_market_global(persist, cmc) -> bool:
    """CMC 글로벌 지표 1행을 market_global 에 적재(스냅샷 시계열). cmc None 이면 스킵."""
    if cmc is None:
        logger.info("[CollectOI] CMC 미설정 — market_global 적재 스킵")
        return False
    metrics = cmc.get_global_metrics()
    if not metrics:
        return False
    return persist.insert("market_global", {**metrics, "source": "cmc"})


def fetch_funding(client, symbol: str, limit: int = 500) -> List[dict]:
    """심볼 펀딩비 이력을 funding_history 행 형식으로 반환. 실패 시 빈 리스트."""
    try:
        raw = client.futures_funding_rate(symbol=symbol, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("[CollectOI] %s 펀딩 이력 조회 실패: %s", symbol, e)
        return []
    rows: List[dict] = []
    for item in raw or []:
        try:
            ts = datetime.fromtimestamp(
                int(item["fundingTime"]) / 1000.0, tz=timezone.utc
            )
            rows.append({
                "symbol": symbol, "ts": ts.isoformat(),
                "funding_rate": float(item["fundingRate"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return rows


def _df_rows(symbol: str, df, interval: str, oi_period: str):
    """DataFrame(OHLCV+open_interest, ts index) → (ohlcv 행, oi 행) 리스트."""
    ohlcv_rows: List[dict] = []
    oi_rows: List[dict] = []
    has_oi = "open_interest" in df.columns
    for ts, r in df.iterrows():
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        ohlcv_rows.append({
            "symbol": symbol, "interval": interval, "ts": ts_iso,
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]),
        })
        if has_oi:
            oi_rows.append({
                "symbol": symbol, "ts": ts_iso,
                "open_interest": float(r["open_interest"]), "period": oi_period,
            })
    return ohlcv_rows, oi_rows


def push_to_supabase(persist, symbol, df, funding_rows, *, interval="1h",
                     oi_period="1h", recent: Optional[int] = _DEFAULT_PUSH_RECENT
                     ) -> Dict[str, int]:
    """심볼 1종의 OHLCV/OI/펀딩을 Supabase 에 멱등(upsert) 적재.

    recent 지정 시 최근 N봉만(겹침 idempotent). None 이면 전체(backfill).
    """
    ohlcv_rows, oi_rows = _df_rows(symbol, df, interval, oi_period)
    if recent is not None and recent > 0:
        ohlcv_rows = ohlcv_rows[-recent:]
        oi_rows = oi_rows[-recent:]
        funding_rows = funding_rows[-recent:]
    persist.upsert_many("ohlcv", ohlcv_rows)
    persist.upsert_many("oi_history", oi_rows)
    persist.upsert_many("funding_history", funding_rows)
    return {"ohlcv": len(ohlcv_rows), "oi": len(oi_rows), "funding": len(funding_rows)}


def collect_once(
    interval: str = "1h",
    oi_period: str = "1h",
    limit: int = 500,
    symbols: Optional[List[str]] = None,
    client=None,
    out_dir=dl.DEFAULT_CACHE_DIR,
    persist=None,
    push_supabase: bool = True,
    push_recent: Optional[int] = _DEFAULT_PUSH_RECENT,
    index_symbols: Optional[List[str]] = None,
    cmc=None,
    push_global: bool = True,
) -> Dict[str, int]:
    """1회 수집 패스 — 유니버스 OI+OHLCV 를 캐시에 누적하고 Supabase 에 적재.

    Args:
        interval/oi_period/limit: data_loader.collect_universe 인자.
        symbols: 수집 종목(None 이면 resolve_symbols()).
        client: 주입 클라이언트(None 이면 live read-only 생성). 테스트는 mock 주입.
        out_dir: 로컬 CSV 캐시 디렉토리(기본 backtests/cache/, gitignore).
        persist: SupabasePersistence 주입(None 이면 env 기반 생성). 테스트는 mock.
        push_supabase: True 면 OHLCV/OI/펀딩을 Supabase 에 멱등 적재.
        push_recent: 적재할 최근 봉 수(None 이면 전체 backfill).

    Returns:
        {symbol: 누적 행수}.
    """
    symbols = symbols or resolve_symbols()
    client = client or _build_live_client()
    data = dl.collect_universe(
        client, symbols, interval=interval, oi_period=oi_period,
        limit=limit, out_dir=out_dir,
    )
    summary = {k: len(v) for k, v in data.items()}
    logger.info("[CollectOI] 수집 완료(%d종목): %s", len(summary), summary)

    if push_supabase:
        persist = persist or _build_persistence()
        pushed: Dict[str, dict] = {}
        for sym, df in data.items():
            funding_rows = fetch_funding(client, sym, limit=limit)
            pushed[sym] = push_to_supabase(
                persist, sym, df, funding_rows,
                interval=interval, oi_period=oi_period, recent=push_recent,
            )

        # 시장-베타 인덱스(BTC/ETH) OHLCV 적재 — 거래 아님, read-only 인덱스(P5a)
        idx_syms = _INDEX_SYMBOLS if index_symbols is None else index_symbols
        if idx_syms:
            idx_data = dl.collect_universe(
                client, idx_syms, interval=interval, oi_period=oi_period,
                limit=limit, out_dir=out_dir,
            )
            for sym, df in idx_data.items():
                funding_rows = fetch_funding(client, sym, limit=limit)
                pushed[f"[idx]{sym}"] = push_to_supabase(
                    persist, sym, df, funding_rows,
                    interval=interval, oi_period=oi_period, recent=push_recent,
                )

        # CMC 글로벌 지표(도미넌스·총시총·F&G) 1행 적재(P5a)
        if push_global:
            push_market_global(persist, cmc or _build_cmc())

        try:
            persist.flush_outbox()       # 이전 장애로 밀린 분 재전송
        except Exception as e:  # noqa: BLE001
            logger.warning("[CollectOI] outbox flush 실패(무시): %s", e)
        logger.info("[CollectOI] Supabase 적재: %s", pushed)

    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("[CollectOI] python-dotenv 미설치 — 환경변수 직접 export 필요")

    ap = argparse.ArgumentParser(description="OI+OHLCV+펀딩 누적 수집 (CSV + Supabase)")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--oi-period", default="1h")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--no-supabase", action="store_true",
                    help="Supabase 적재 비활성화(로컬 CSV 캐시만)")
    ap.add_argument("--backfill", action="store_true",
                    help="최근 N봉이 아니라 수집 전체를 Supabase 에 1회 시딩")
    args = ap.parse_args()

    summary = collect_once(
        interval=args.interval, oi_period=args.oi_period, limit=args.limit,
        push_supabase=not args.no_supabase,
        push_recent=None if args.backfill else _DEFAULT_PUSH_RECENT,
    )
    print(f"OI collection done ({len(summary)} symbols): {summary}")


if __name__ == "__main__":
    main()
