"""
scripts/run_m11_backtest.py
=====================================================================
M11 — Testnet/실제 데이터로 DailyTSMOMDonchianSkill 백테스트 7기준 실측.

근거:
  - REFACTOR_PLAN_v2_BLUEPRINT.md M11
  - skills/daily_tsmom_donchian_skill.py (M2)
  - backtesting/signal_validation_report.validate_and_persist (M2)
  - backtesting/local_ohlcv_store (M11 신규)

사용:
    python scripts/run_m11_backtest.py --universe-size 18 --years 2 --interval 1d

옵션:
    --backfill          Binance API 호출하여 ohlcv_local 재적재
    --skip-backfill     기존 ohlcv_local 만 사용 (재실행 시 빠름)
    --universe-size N   universe 종목 수 (default 18)
    --years N           backfill 기간 (default 2)
    --interval STR      1d / 4h / 1h (default 1d)
    --db PATH           DB 경로 override
=====================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.local_ohlcv_store import LocalOhlcvStore  # noqa: E402
from backtesting.signal_validation import validate_setup  # noqa: E402
from backtesting.signal_validation_report import (  # noqa: E402
    summarize, validate_and_persist,
)
from db.init_db import init_db  # noqa: E402
from registry.setup_registry import SetupRegistry  # noqa: E402
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill  # noqa: E402
from strategy.breakout import BreakoutConfig, evaluate_breakout  # noqa: E402

logger = logging.getLogger("run_m11_backtest")


# Universe 기본 (HANDOFF A2-① 정합 — CMC 시총 ≤50 ∩ $100M+ ∩ 비보호자산)
DEFAULT_UNIVERSE = [
    "SOLUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT",
    "ATOMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT", "TONUSDT",
    "PEPEUSDT", "SHIBUSDT", "WLDUSDT",
]


def parse_args():
    p = argparse.ArgumentParser(
        description="M11 — DailyTSMOMDonchianSkill 7기준 백테스트 실측"
    )
    p.add_argument("--universe-size", type=int, default=18)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--interval", type=str, default="1d")
    p.add_argument("--db", type=str, default=None, help="DB 경로 override")
    bf = p.add_mutually_exclusive_group()
    bf.add_argument("--backfill", action="store_true", help="Binance API backfill")
    bf.add_argument("--skip-backfill", action="store_true", help="ohlcv_local 만 사용")
    return p.parse_args()


def run_backfill(
    store: LocalOhlcvStore,
    universe: list[str],
    interval: str,
    years: int,
) -> dict[str, int]:
    """Binance API → ohlcv_local 백필 (backtesting.backfill_history 재사용)."""
    logger.info(
        "[M11] Backfill 시작 — universe %d종목, %d년, interval=%s",
        len(universe), years, interval,
    )

    try:
        from backtesting.backfill_history import fetch_klines_history
        from data.collector import BinanceDataCollector
    except ImportError as e:
        logger.error("[M11] backfill 모듈 import 실패: %s", e)
        return {}

    # Binance 클라이언트 (collector 의 _build_client 재사용)
    try:
        collector = BinanceDataCollector()
        client = collector.client
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[M11] BinanceDataCollector 초기화 실패: %s. "
            ".env 의 BINANCE_API_KEY/SECRET 또는 USE_TESTNET 확인.", e,
        )
        return {}

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (years * 365 * 86400 * 1000)
    counts = {}

    for sym in universe:
        try:
            rows = fetch_klines_history(
                client, symbol=sym, interval=interval,
                start_ms=start_ms, end_ms=end_ms,
                pause=0.15, max_pages=5000,
            )
            if rows:
                n = store.upsert_many(sym, interval, rows)
                counts[sym] = n
                logger.info("[M11] %s: %d 캔들 적재", sym, n)
            else:
                counts[sym] = 0
                logger.warning("[M11] %s: 응답 빈 결과", sym)
        except Exception as e:  # noqa: BLE001
            logger.error("[M11] %s backfill 실패: %s", sym, e)
            counts[sym] = 0
    return counts


def run_skill_backtest(
    store: LocalOhlcvStore,
    universe: list[str],
    interval: str,
) -> list[dict]:
    """DailyTSMOMDonchianSkill 으로 trades 시뮬레이션.

    *단순화 모델* (M11 smoke):
      1. 각 symbol 의 ohlcv_local → evaluate_breakout 평가
      2. LONG/SHORT signal 발생 시 → 다음 봉부터 +ATR target 또는 -ATR stop 까지 보유
      3. taker fee 0.04% × 2 + slippage 0.05% × 2 = 약 0.18% RT 비용 차감
      4. pnl_r (R 단위) + pnl_pct 계산

    M11 핵심: *7기준 검증 게이트* 입력 데이터 생성. 정밀 walk-forward 는
    backtesting/portfolio_backtest.py 사용 권장 (운영자 별도 호출).
    """
    skill = DailyTSMOMDonchianSkill()
    bcfg = BreakoutConfig(
        donchian_entry=skill.PARAMS["donchian_entry"],
        donchian_exit=skill.PARAMS["donchian_exit"],
        adx_period=skill.PARAMS["adx_period"],
        adx_trend_min=skill.PARAMS["adx_trend_min"],
        ema_period=skill.PARAMS["ema_period"],
        atr_period=skill.PARAMS["atr_period"],
        atr_stop_mult=skill.PARAMS["atr_stop_mult"],
        atr_target_mult=skill.PARAMS["atr_target_mult"],
    )

    trades = []
    cost_rt_pct = 0.18  # round-trip 비용 (taker + slippage 보수)

    for sym in universe:
        all_candles = store.get_candles(sym, interval, limit=10000)
        if len(all_candles) < bcfg.min_bars() + 10:
            logger.warning("[M11] %s: 캔들 부족 (%d) — skip", sym, len(all_candles))
            continue

        # rolling window 평가
        i = bcfg.min_bars()
        while i < len(all_candles) - 1:
            window = all_candles[:i]
            signal = evaluate_breakout(window, bcfg)
            if signal is None:
                i += 1
                continue
            # 진입 가격 = 다음 봉 open (룩어헤드 차단)
            entry = all_candles[i][0]  # next bar open
            stop = entry - signal.atr * bcfg.atr_stop_mult if signal.action == "LONG" \
                else entry + signal.atr * bcfg.atr_stop_mult
            target = entry + signal.atr * bcfg.atr_target_mult if signal.action == "LONG" \
                else entry - signal.atr * bcfg.atr_target_mult
            # 청산 — 이후 N봉 내 target 또는 stop 도달
            exit_price = None
            exit_reason = "time_stop"
            for j in range(i, min(i + 30, len(all_candles))):
                h = all_candles[j][1]
                low = all_candles[j][2]
                if signal.action == "LONG":
                    if low <= stop:
                        exit_price = stop
                        exit_reason = "SL"
                        break
                    if h >= target:
                        exit_price = target
                        exit_reason = "TP"
                        break
                else:
                    if h >= stop:
                        exit_price = stop
                        exit_reason = "SL"
                        break
                    if low <= target:
                        exit_price = target
                        exit_reason = "TP"
                        break
            if exit_price is None:
                exit_price = all_candles[min(i + 29, len(all_candles) - 1)][3]
            # pnl 계산
            if signal.action == "LONG":
                pnl_pct = (exit_price - entry) / entry * 100 - cost_rt_pct
            else:
                pnl_pct = (entry - exit_price) / entry * 100 - cost_rt_pct
            # pnl_r = pnl / atr_stop_mult (1R = ATR × atr_stop_mult)
            atr_pct = signal.atr / entry * 100
            stop_distance_pct = atr_pct * bcfg.atr_stop_mult
            pnl_r = pnl_pct / stop_distance_pct if stop_distance_pct > 0 else 0

            trades.append({
                "symbol": sym,
                "action": signal.action,
                "entry_ts": all_candles[i - 1][5],
                "exit_ts": all_candles[min(i + 29, len(all_candles) - 1)][5],
                "entry": entry,
                "exit": exit_price,
                "pnl_pct": pnl_pct,
                "pnl_r": pnl_r,
                "is_win": pnl_r > 0,
                "exit_reason": exit_reason,
            })
            # 다음 진입 후보는 청산 후 (rolling window)
            i += 30 if exit_reason != "time_stop" else 5
        logger.info("[M11] %s: trades so far = %d", sym, len(trades))

    return trades


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # .env 자동 로드 (main_7590 패턴 정합)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("[M11] python-dotenv 미설치 — .env 자동 로드 안 됨")

    db_path = args.db or "data/bot_live.db"
    init_db(db_path)
    store = LocalOhlcvStore(db_path=db_path)

    # universe
    universe = DEFAULT_UNIVERSE[:args.universe_size]
    logger.info("[M11] Universe: %s", universe)

    # backfill
    if args.backfill:
        run_backfill(store, universe, args.interval, args.years)
    elif args.skip_backfill:
        logger.info("[M11] --skip-backfill — 기존 ohlcv_local 만 사용")
    else:
        logger.info(
            "[M11] backfill flag 미지정 — 기존 ohlcv_local 사용 (없으면 trades=0)."
        )

    # 백테스트
    trades = run_skill_backtest(store, universe, args.interval)
    logger.info("[M11] Total trades: %d", len(trades))

    if not trades:
        logger.error(
            "[M11] trades=0 — ohlcv_local 비어있거나 캔들 부족. "
            "--backfill 옵션으로 데이터 적재 후 재실행."
        )
        sys.exit(2)

    # 7기준 검증 + setup_registry update
    registry = SetupRegistry(db_path=db_path)
    registry.register(DailyTSMOMDonchianSkill)

    report = validate_and_persist(
        trades=trades,
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        registry=registry,
        mode="trend",
        evaluation_type="backtest",
        auto_update_status=True,
    )

    # Windows cp949 console 인코딩 대응 — 출력은 try/except
    try:
        print(summarize(report))
    except UnicodeEncodeError:
        # 파일에는 UTF-8 로 저장됨 — 콘솔만 cp949
        logger.warning("[M11] console UnicodeEncodeError (cp949). 파일 보고서 참조.")

    # 결과 보고서 저장
    report_path = Path("docs/SETUP_VERIFICATION_REPORT_v3_2_0.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# M11 Setup Verification Report (v3.2.0)\n\n")
        f.write(f"> Generated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"> Setup: `{DailyTSMOMDonchianSkill.SETUP_ID}`\n")
        f.write(f"> PARAMS_HASH: `{DailyTSMOMDonchianSkill.PARAMS_HASH}`\n")
        f.write(f"> Universe: {len(universe)} symbols\n")
        f.write(f"> Interval: {args.interval}\n")
        f.write(f"> Total trades: {len(trades)}\n\n")
        f.write("## 운영자 권장 7기준 결과\n\n```\n")
        f.write(summarize(report))
        f.write("\n```\n\n")
        f.write(f"## Status\n\n")
        if report.passed:
            f.write("✅ **PASS** — `setup_registry.status = R0_QUALIFIED` (auto)\n")
            f.write("\n다음: Phase 1.5 micro-live 진입 가능 (운영자 명시 GO 후)\n")
        elif len(report.failed_criteria) == 1:
            f.write("⚠️ **CONDITIONAL** — 1 fail (운영자 명시 결정 필요)\n")
        else:
            f.write(f"❌ **FAIL** — {len(report.failed_criteria)} fails → DISABLED\n")
            f.write("\n청사진 §7.5 Stage 3 옵션 A/B/C 회의 권장\n")

    logger.info("[M11] 보고서 저장: %s", report_path)
    try:
        print(f"\nReport: {report_path}")
        print(f"Dashboard: scripts/run_dashboard.bat -> http://localhost:8501 (page 6)")
    except UnicodeEncodeError:
        pass

    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
