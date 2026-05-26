"""
scripts/run_pair_arb_r0.py
=====================================================================
Pair Stat-Arb v1 R0 실행 스크립트.

사용:
  python scripts/run_pair_arb_r0.py [--write-supabase] [--top-n 50]
                                     [--since 2024-05-21T00:00:00Z]
                                     [--oos-start 2025-11-21T00:00:00Z]

기본: 콘솔 출력만. --write-supabase 옵션 시 backtest_runs 적재.
=====================================================================
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pair_r0")


def _format_report(report) -> str:
    lines = []
    lines.append(f"=== Pair Stat-Arb R0 결과 ({report.run_id}) ===")
    lines.append(f"기간: {report.started_at} → {report.finished_at}")
    lines.append(f"자격 페어: {report.n_qualified_pairs}  |  완료 트레이드: {report.n_trades}")
    lines.append("")
    lines.append(f"PF (net):                {report.pf:.3f}")
    lines.append(f"Sharpe (trade-level):    {report.sharpe:.2f}")
    lines.append(f"평균 트레이드 순수익:     {report.mean_trade_return*100:+.3f}%")
    lines.append(f"Max DD:                  {report.max_dd*100:.1f}%")
    lines.append(f"횡단면 양(+) 페어 비율:  {report.cross_pair_pos_share*100:.1f}%")
    lines.append(f"단일 페어 기여 최댓값:   {report.single_pair_contribution*100:.1f}%")
    lines.append("")
    lines.append("=== 사전확정 7기준 평가 (검증 전 못박음) ===")
    for c in report.criteria:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name:30s}  value={c.value:.4f}  threshold={c.threshold:.4f}")
        if c.detail:
            lines.append(f"         {c.detail}")
    lines.append("")
    final = "PASS → R1 OOS 평가 진행" if report.passed else "FAIL → 정직 종료 (8 전략 base rate 정합)"
    lines.append(f"=== 최종 판정: {final} ===")
    lines.append(f"노트: {report.notes}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-supabase", action="store_true",
                    help="결과를 backtest_runs 테이블에 적재")
    ap.add_argument("--top-n", type=int, default=50,
                    help="유니버스 상위 N 심볼(funding_history distinct 기준)")
    ap.add_argument("--since", type=str, default=None,
                    help="데이터 시작 ISO (예: 2024-05-21T00:00:00Z)")
    ap.add_argument("--oos-start", type=str, default=None,
                    help="OOS subset 분리(R1 모드)")
    args = ap.parse_args()

    from analytics.pair_arb_backtest import record_to_supabase, run_r0

    logger.info("Running Pair Stat-Arb v1 R0 ...")
    report = run_r0(
        top_n=args.top_n, since_iso=args.since, oos_start_iso=args.oos_start,
    )
    print(_format_report(report), flush=True)

    if args.write_supabase:
        ok = record_to_supabase(report)
        print(f"\n[backtest_runs 적재] {'성공' if ok else '실패(outbox 적재)'}")

    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
