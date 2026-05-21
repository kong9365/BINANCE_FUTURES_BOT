"""
backtesting/data_loader.py
=====================================================================
OI + OHLCV 데이터 수집/적재 — OI-급증 전략 백테스트(검증) 데이터 파이프라인.

배경:
  Binance futures_open_interest_hist 는 ~30일치만 제공한다. 다년 OI 백테스트가
  불가능하므로, (1) 지금 가용한 ~30일을 부트스트랩으로 받고 (2) 주기적으로
  재실행하여 OI 시계열을 **누적**한다(forward accumulation). 누적된 CSV 를
  BacktestEngine(strategy="oi_surge")에 그대로 먹인다.

저장:
  기본 out_dir = backtests/cache/ (gitignore 대상). CSV(인덱스=UTC ISO timestamp,
  columns=open,high,low,close,volume,open_interest).

설계 메모:
  - client(python-binance Client)는 주입받는다(테스트 시 mock). 네트워크 호출은
    호출부 책임(여기서는 동기 호출 — 백테스트 준비 단계라 async 불필요).
  - klines 와 OI 이력의 교집합 timestamp 만 사용(OI 가 있는 봉만 백테스트 대상).
  - merge_into 로 기존 CSV 와 신규 fetch 를 timestamp union(신규 우선)으로 합쳐
    재실행 시 데이터가 누적되게 한다(멱등).
=====================================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("backtests/cache")
_COLUMNS = ["open", "high", "low", "close", "volume", "open_interest"]


def fetch_symbol(
    client,
    symbol: str,
    interval: str = "1h",
    oi_period: str = "1h",
    limit: int = 500,
) -> pd.DataFrame:
    """klines + OI 이력을 합쳐 OHLCV+open_interest DataFrame 을 만든다.

    Args:
        client: python-binance Client (futures_klines / futures_open_interest_hist).
        symbol: 거래 페어.
        interval: 캔들 간격(예: "1h"). oi_period 와 정합 권장.
        oi_period: OI 이력 집계 주기(예: "1h").
        limit: 각 엔드포인트 데이터 포인트 수(최대 500).

    Returns:
        index=UTC DatetimeIndex, columns=_COLUMNS. OI 가 있는 봉만 포함.
        데이터 없으면 빈 DataFrame.
    """
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit) or []
    oih = client.futures_open_interest_hist(
        symbol=symbol, period=oi_period, limit=limit
    ) or []

    ohlcv = {
        int(k[0]): (
            float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        )
        for k in klines
    }
    oi = {int(d["timestamp"]): float(d["sumOpenInterest"]) for d in oih}
    common = sorted(set(ohlcv) & set(oi))
    if not common:
        logger.warning("[DataLoader] %s klines∩OI 교집합 없음 — 빈 DF", symbol)
        return pd.DataFrame(columns=_COLUMNS)

    data = {c: [] for c in _COLUMNS}
    for t in common:
        o, h, low, c, v = ohlcv[t]
        data["open"].append(o)
        data["high"].append(h)
        data["low"].append(low)
        data["close"].append(c)
        data["volume"].append(v)
        data["open_interest"].append(oi[t])
    idx = pd.DatetimeIndex(
        [pd.Timestamp(t, unit="ms", tz="UTC") for t in common], name="timestamp"
    )
    return pd.DataFrame(data, index=idx, columns=_COLUMNS)


def save_dataframe(df: pd.DataFrame, path: Path | str) -> Path:
    """DataFrame 을 CSV 로 저장(index=UTC ISO timestamp)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="timestamp")
    return path


def load_dataframe(path: Path | str) -> pd.DataFrame:
    """CSV 를 DataFrame 으로 로드(UTC tz-aware DatetimeIndex)."""
    df = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "timestamp"
    return df.sort_index()


def merge_into(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """기존 + 신규를 timestamp union(신규 우선)으로 합쳐 누적(멱등)."""
    if existing is None or len(existing) == 0:
        return new.sort_index()
    if new is None or len(new) == 0:
        return existing.sort_index()
    combined = pd.concat([existing, new])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def collect_universe(
    client,
    symbols: List[str],
    interval: str = "1h",
    oi_period: str = "1h",
    limit: int = 500,
    out_dir: Path | str = DEFAULT_CACHE_DIR,
) -> Dict[str, pd.DataFrame]:
    """유니버스 각 심볼의 OI+OHLCV 를 받아 CSV 에 누적 저장하고 dict 로 반환.

    재실행하면 기존 CSV 와 merge_into 로 누적된다(forward accumulation).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            new = fetch_symbol(client, sym, interval, oi_period, limit)
        except Exception as e:  # noqa: BLE001 — 한 심볼 실패가 전체를 막지 않음
            logger.error("[DataLoader] %s 수집 실패: %s — 스킵", sym, e)
            continue
        path = out_dir / f"{sym}_{interval}.csv"
        merged = merge_into(load_dataframe(path), new) if path.exists() else new
        save_dataframe(merged, path)
        result[sym] = merged
        logger.info("[DataLoader] %s 저장: %d행 (%s)", sym, len(merged), path)
    return result


def load_universe(
    symbols: List[str],
    interval: str = "1h",
    out_dir: Path | str = DEFAULT_CACHE_DIR,
) -> Dict[str, pd.DataFrame]:
    """저장된 CSV 들을 로드해 {symbol: DataFrame} 반환(없는 심볼은 스킵)."""
    out_dir = Path(out_dir)
    result: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = out_dir / f"{sym}_{interval}.csv"
        if path.exists():
            result[sym] = load_dataframe(path)
    return result
