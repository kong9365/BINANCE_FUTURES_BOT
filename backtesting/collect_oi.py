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
from typing import Dict, List, Optional

from backtesting import data_loader as dl

logger = logging.getLogger(__name__)

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


def collect_once(
    interval: str = "1h",
    oi_period: str = "1h",
    limit: int = 500,
    symbols: Optional[List[str]] = None,
    client=None,
    out_dir=dl.DEFAULT_CACHE_DIR,
) -> Dict[str, int]:
    """1회 수집 패스 — 유니버스 OI+OHLCV 를 받아 캐시에 누적하고 행수 요약 반환.

    Args:
        interval/oi_period/limit: data_loader.collect_universe 인자.
        symbols: 수집 종목(None 이면 resolve_symbols()).
        client: 주입 클라이언트(None 이면 live read-only 생성). 테스트는 mock 주입.
        out_dir: 저장 디렉토리(기본 backtests/cache/, gitignore).

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

    ap = argparse.ArgumentParser(description="OI+OHLCV 누적 수집")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--oi-period", default="1h")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    summary = collect_once(
        interval=args.interval, oi_period=args.oi_period, limit=args.limit
    )
    print(f"OI collection done ({len(summary)} symbols): {summary}")


if __name__ == "__main__":
    main()
