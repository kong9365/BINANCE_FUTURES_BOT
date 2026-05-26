"""OHLCV/funding 데이터 로더 — Supabase 우선, 로컬 backfill 캐시 폴백."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path("backtests/cache/verification")
_PAGE = 1000


def build_ohlcv_client():
    """SUPABASE_OHLCV_* 클라이언트. 실패 시 None."""
    url = os.environ.get("SUPABASE_OHLCV_URL") or os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_OHLCV_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:  # noqa: BLE001
        logger.warning("[VerifyData] supabase client 실패: %s", e)
        return None


def _fetch_paged(query_factory, page_size: int = _PAGE, max_pages: int = 500) -> List[dict]:
    out: List[dict] = []
    for p in range(max_pages):
        start = p * page_size
        end = start + page_size - 1
        res = query_factory().range(start, end).execute()
        chunk = res.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
    return out


def load_ohlcv_supabase(
    client, symbol: str, interval: str = "1d",
) -> pd.DataFrame:
    def _q():
        return (
            client.table("ohlcv")
            .select("ts,open,high,low,close,volume,taker_buy_base")
            .eq("symbol", symbol)
            .eq("interval", interval)
            .order("ts")
        )
    data = _fetch_paged(_q)
    return _rows_to_df(data)


def load_ohlcv_local(symbol: str, interval: str = "1d") -> pd.DataFrame:
    for ext in (".csv", ".parquet"):
        path = CACHE_DIR / f"{symbol}_{interval}{ext}"
        if not path.exists():
            continue
        if ext == ".csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_parquet(path)
        if "ts" in df.columns:
            records = df.to_dict("records")
        else:
            df = df.reset_index()
            if "index" in df.columns and "ts" not in df.columns:
                df = df.rename(columns={"index": "ts"})
            records = df.to_dict("records")
        return _rows_to_df(records)
    return pd.DataFrame()


def _rows_to_df(rows: List[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    if "taker_buy_base" in df.columns:
        df = df.rename(columns={"taker_buy_base": "taker_buy_base_asset_volume"})
    cols = ["open", "high", "low", "close", "volume"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df


def list_local_symbols(interval: str = "1d") -> List[str]:
    syms: set[str] = set()
    for path in CACHE_DIR.glob(f"*_{interval}.csv"):
        syms.add(path.name.replace(f"_{interval}.csv", ""))
    for path in CACHE_DIR.glob(f"*_{interval}.parquet"):
        syms.add(path.name.replace(f"_{interval}.parquet", ""))
    return sorted(syms)


def load_universe_ohlcv(
    symbols: List[str],
    interval: str = "1d",
    client=None,
    local_only: bool = False,
) -> Dict[str, pd.DataFrame]:
    """심볼별 OHLCV dict — 로컬 캐시 우선, Supabase는 alive 일 때만."""
    out: Dict[str, pd.DataFrame] = {}
    use_supabase = False
    if not local_only:
        client = client if client is not None else build_ohlcv_client()
        if client is not None:
            try:
                client.table("ohlcv").select("symbol").limit(1).execute()
                use_supabase = True
            except Exception:  # noqa: BLE001
                use_supabase = False
    for sym in symbols:
        df = load_ohlcv_local(sym, interval)
        if df.empty and use_supabase and client is not None:
            try:
                df = load_ohlcv_supabase(client, sym, interval)
            except Exception as e:  # noqa: BLE001
                logger.debug("[VerifyData] supabase %s 실패: %s", sym, e)
        if not df.empty:
            out[sym] = df
    return out


def trading_universe_symbols(client=None) -> List[str]:
    """보호종목 제외 + BTC/ETH 인덱스 포함 backfill 유니버스."""
    from backtesting.backfill_history import resolve_backfill_symbols
    from backtesting.collect_oi import _build_live_client

    c = client or _build_live_client()
    return resolve_backfill_symbols(
        c, symbols_top_n=50, exclude_protected=True, include_index=True,
    )
