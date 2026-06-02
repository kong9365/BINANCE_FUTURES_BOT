"""
scripts/run_observer.py
=====================================================================
관찰 알림 + 페이퍼(가상) 로그 데몬 가동 — *읽기전용·가상·HOLD*.

★ 시장데이터는 공개 keyless API. executor·실주문·실키 0. STRONG=Telegram 발신만(설정 시),
  WEAK+STRONG=가상 로그. 보호종목 제외. 자동매매 아님 = 관찰 보고 + 가상 로그(6번째 검증).

가동(운영자):
  1) MONITORING_ENABLED=true
  2) (선택) TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  ← 발신용(운영자 직접). 없으면 로그만.
  3) python scripts/run_observer.py [--cycles N]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# .env 로드 — config import 전(M15-fix 교훈: MONITORING_ENABLED 등은 import 시점에 읽힘).
# 운영자가 .env 에 둔 MONITORING_ENABLED / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를
# 환경에 주입한다. ★ CC 는 키 *값*을 보지도 쓰지도 않는다 — 로드만(값은 운영자 .env 소유).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

from config.settings import MONITORING_CONFIG, PAIR_WHITELIST_CONFIG  # noqa: E402
from monitoring.notify import build_telegram_sender  # noqa: E402
from monitoring.runner import run  # noqa: E402

# 기본 유니버스 — 유동성 있는 USDT 무기한(보호종목 BTC/ETH 등은 아래서 제외). env 로 교체 가능.
DEFAULT_SYMBOLS = [
    "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT",
    "TRXUSDT", "NEARUSDT", "BCHUSDT", "UNIUSDT", "FILUSDT", "SUIUSDT", "TONUSDT",
    "ONDOUSDT", "ENAUSDT", "RENDERUSDT", "WLDUSDT", "TAOUSDT",
]


def _symbols() -> list:
    raw = os.environ.get("MONITORING_SYMBOLS")
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else list(DEFAULT_SYMBOLS)
    prot = set(PAIR_WHITELIST_CONFIG.protected_symbols)
    return [s for s in syms if s not in prot]                 # ★ 보호종목 이중 제외


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("MONITORING_PAPER_DB")
                    or os.path.join(PROJECT_ROOT, "data", "paper_log.db"))
    ap.add_argument("--cycles", type=int, default=None, help="스캔 사이클 수(기본 무한)")
    args = ap.parse_args()

    cfg = MONITORING_CONFIG
    if not cfg.enabled:
        print("MONITORING_ENABLED 미설정 → 미실행. (켜려면 환경변수 MONITORING_ENABLED=true)")
        return
    syms = _symbols()
    print(f"관찰 데몬 시작 — {len(syms)}종목 | 스캔 {cfg.scan_interval_s}s 주기 | db={args.db}")
    print("★ 읽기전용·가상·실주문 0. STRONG=Telegram 발신만(설정 시), WEAK+STRONG=가상 로그.")
    print("★ 신호는 5회 검증 무엣지 신호 — 페이퍼도 무엣지 예상. PASS처럼 보여도 실거래 HOLD.")
    run(syms, args.db, build_telegram_sender(cfg), cfg, max_cycles=args.cycles)


if __name__ == "__main__":
    main()
