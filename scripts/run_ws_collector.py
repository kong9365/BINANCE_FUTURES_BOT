"""
scripts/run_ws_collector.py
=====================================================================
Microstructure WebSocket Collector CLI (Phase 1/2 — daemon entry).

사용:
  # 명시 심볼 (backward compat)
  python scripts/run_ws_collector.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --duration 1800

  # 거래대금 Top N (Supabase 기반, backward compat)
  python scripts/run_ws_collector.py --top-n 10

  # ★ Tiered Universe (Phase 2 — 권장)
  python scripts/run_ws_collector.py --tiered-universe \\
      --tier1-count 15 --tier2-count 30 --duration 300

  # ★ resolve dry-run (즉시 종료, lineage 적재 + universe 출력)
  python scripts/run_ws_collector.py --tiered-universe --print-universe

  # commodity-like 포함 (실험)
  python scripts/run_ws_collector.py --tiered-universe --include-commodity-like

  # testnet (개발)
  python scripts/run_ws_collector.py --top-n 3 --testnet --duration 300

알파/ML/R0/진입 로직 *없음*. 데이터 적재만.
=====================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

# ── aiohttp DNS 패치 (Windows + Python 3.14 default AsyncResolver 버그 회피) ──
# 진단 (Phase 1-B): 기본 aiohttp AsyncResolver 가 시스템 DNS 호출 실패
# ("Could not contact DNS servers"). ThreadedResolver 로 강제 교체하면 동작.
# python-binance 가 내부 ClientSession 을 만들기 *전*에 패치해야 함.
import aiohttp  # noqa: E402
_orig_tcp_connector_init = aiohttp.TCPConnector.__init__
def _patched_tcp_connector_init(self, *args, **kwargs):  # type: ignore[no-redef]
    if "resolver" not in kwargs:
        kwargs["resolver"] = aiohttp.ThreadedResolver()
    _orig_tcp_connector_init(self, *args, **kwargs)
aiohttp.TCPConnector.__init__ = _patched_tcp_connector_init  # type: ignore[method-assign]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# httpx/httpcore INFO 억제 (보안 — Telegram 토큰 URL 유출 방지 패턴과 동일)
for noisy in ("httpx", "httpcore", "websockets", "binance"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("ws_r0")


def _resolve_symbols(args) -> list[str]:
    """--symbols 또는 --top-n (backward compat) 으로 단순 심볼 리스트."""
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        logger.info("symbols (explicit): %s", syms)
        return syms
    from analytics.ccs_lite_backtest import _build_supabase_client
    from analytics.pair_arb_backtest import _fetch_universe_symbols
    client = _build_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client 미설정 — SUPABASE_URL/KEY 확인")
    syms = _fetch_universe_symbols(client, top_n=args.top_n)
    logger.info("symbols (top %d by notional): %s", args.top_n, syms)
    return syms


def _resolve_tiered_universe(args):
    """--tiered-universe — Binance ticker + exchangeInfo 로 Tier 산출.

    Returns:
        (universe, symbol_streams_dict)
    """
    from binance.client import Client
    import os
    from config.settings import MICROSTRUCTURE_UNIVERSE_CONFIG, MicrostructureUniverseConfig
    from data.microstructure_universe import (
        build_symbol_streams_dict, fetch_exchange_info_and_ticker,
        resolve_tiered_universe, save_universe_snapshot, symbols_by_stream,
    )

    # CLI override (운영자 보수적 시작값: tier1=15, tier2=30)
    base = MICROSTRUCTURE_UNIVERSE_CONFIG
    cfg = MicrostructureUniverseConfig(
        tier1_full_count=args.tier1_count if args.tier1_count is not None else base.tier1_full_count,
        tier2_light_count=args.tier2_count if args.tier2_count is not None else base.tier2_light_count,
        tier3_monitor_count=base.tier3_monitor_count,
        include_commodity_like=args.include_commodity_like,
        refresh_interval_hours=base.refresh_interval_hours,
        core_symbols=base.core_symbols,
        commodity_like_exact_symbols=base.commodity_like_exact_symbols,
        min_listing_days=base.min_listing_days,
        source=base.source,
    )

    # Binance public REST (futures_ticker + exchange_info) — testnet 무관
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    client = Client(api_key=api_key, api_secret=api_secret) if api_key else Client()
    logger.info("[Universe] fetching Binance futures_exchange_info + futures_ticker ...")
    ei, tk = fetch_exchange_info_and_ticker(client)
    logger.info("[Universe] exchange_info: %d symbols, ticker: %d entries",
                 len(ei.get("symbols", [])), len(tk))

    universe = resolve_tiered_universe(ei, tk, cfg)
    symbol_streams = build_symbol_streams_dict(universe)

    # Print universe
    by_stream = symbols_by_stream(universe)
    logger.info("[Universe] Tier 0 (core): %s",
                [u.symbol for u in universe if u.tier == "tier0"])
    logger.info("[Universe] Tier 1 (full): %s",
                [u.symbol for u in universe if u.tier == "tier1"])
    logger.info("[Universe] Tier 2 (light): %s",
                [u.symbol for u in universe if u.tier == "tier2"])
    logger.info("[Universe] streams — depth: %d, aggTrade: %d, forceOrder: %d",
                 len(by_stream.get("depth", [])),
                 len(by_stream.get("aggTrade", [])),
                 len(by_stream.get("forceOrder", [])))
    commodity_count = sum(1 for u in universe if u.category == "commodity_like")
    logger.info("[Universe] commodity-like included: %d", commodity_count)

    return universe, symbol_streams, cfg


async def _main_async(args) -> int:
    from data.persistence import SupabasePersistence
    from data.microstructure_universe import save_universe_snapshot
    from data.ws_collector import WSCollector, WSCollectorConfig

    persist = SupabasePersistence()
    if persist.client is None:
        logger.error("Supabase client 미설정 — degraded outbox mode (운영자 점검 필요)")

    # ── Universe resolution ──────────────────────────────────────
    symbol_streams: dict | None = None
    if args.tiered_universe:
        universe, symbol_streams, universe_cfg = _resolve_tiered_universe(args)
        # Lineage 적재 (Phase 3 IC 분석 시 point-in-time universe 복원용)
        if not args.no_save_snapshot:
            ok = save_universe_snapshot(universe, persist, universe_cfg)
            logger.info("[Universe] snapshot 적재: %s (rows=%d)",
                         "성공" if ok else "실패(outbox)", len(universe))
        # --print-universe → resolve + 적재 후 즉시 종료
        if args.print_universe:
            return 0
        symbols = list(symbol_streams.keys())   # cfg.symbols 호환 채움
    else:
        symbols = _resolve_symbols(args)

    cfg = WSCollectorConfig(
        symbols=symbols,
        symbol_streams=symbol_streams,
        use_testnet=args.testnet,
        duration_seconds=args.duration,
    )
    collector = WSCollector(cfg, persist)

    # Ctrl-C / SIGTERM 처리
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("[Signal] stop requested")
        stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    except (NotImplementedError, RuntimeError):
        # Windows 일부 환경에서 add_signal_handler 미지원
        pass

    collector_task = asyncio.create_task(collector.start())
    stop_task = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        {collector_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_task in done:
        await collector.stop()
    for t in pending:
        t.cancel()

    logger.info(
        "[Summary] l2=%d agg=%d liq=%d reconnects=%d avg_latency=%.1fms",
        collector.stats.l2_rows_total,
        collector.stats.agg_rows_total,
        collector.stats.liq_rows_total,
        collector.stats.reconnects,
        collector.stats.avg_latency_ms,
    )
    return 0 if collector.is_healthy() else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--symbols", type=str, default=None,
                    help="콤마 분리 심볼 리스트 (backward compat)")
    g.add_argument("--top-n", type=int, default=10,
                    help="Supabase 거래대금 상위 N (default 10, backward compat)")
    g.add_argument("--tiered-universe", action="store_true",
                    help="★ Phase 2 Tiered Universe (Binance ticker + exchangeInfo 기반)")
    # Tiered universe override (CLI 보수적 시작값 — 운영자 명시)
    ap.add_argument("--tier1-count", type=int, default=None,
                    help="Tier 1 (full depth+agg+force) 심볼 수 (default config: 20)")
    ap.add_argument("--tier2-count", type=int, default=None,
                    help="Tier 2 (light agg+force, no depth) 심볼 수 (default config: 50)")
    ap.add_argument("--include-commodity-like", action="store_true",
                    help="XAU/XAG/CL/BZ 등 commodity-like 포함 (기본 제외)")
    ap.add_argument("--print-universe", action="store_true",
                    help="resolve + lineage 적재 후 즉시 종료(dry-run)")
    ap.add_argument("--no-save-snapshot", action="store_true",
                    help="collection_universe_snapshots 적재 안 함(테스트용)")
    # 공통
    ap.add_argument("--duration", type=int, default=None,
                    help="N초 후 자동 종료 (default 무한)")
    ap.add_argument("--testnet", action="store_true",
                    help="Binance testnet WS 사용 (개발용)")
    args = ap.parse_args()

    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
