"""
scripts/run_ccs_lite_r0.py
=====================================================================
CCS-Lite v1.1 R0 실행 스크립트.

사용:
  python scripts/run_ccs_lite_r0.py [--write-supabase]

기본은 콘솔 출력만. --write-supabase 옵션 시 backtest_runs 적재.
=====================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가(스크립트로 직접 실행 시).
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
logger = logging.getLogger("r0")


def _format_report(report) -> str:
    """Human-readable report."""
    lines = []
    lines.append(f"=== CCS-Lite v1.1 R0 결과 ({report.run_id}) ===")
    lines.append(f"기간: {report.started_at} → {report.finished_at}")
    lines.append(f"표본: STRONG={report.n_strong}  NEUTRAL={report.n_neutral}  BLOCKED={report.n_blocked}")
    lines.append("")
    lines.append(f"+4h:   mean={report.mean_4h*100:+.3f}%  median={report.median_4h*100:+.3f}%  Sharpe={report.sharpe_4h:.2f}")
    lines.append(f"+24h:  mean={report.mean_24h*100:+.3f}%  median={report.median_24h*100:+.3f}%")
    lines.append(f"횡단면 양(+) 종목 비율: {report.cross_sectional_pos_share*100:.1f}%")
    lines.append(f"단일 종목 기여 최댓값:  {report.single_symbol_contribution*100:.1f}%")
    lines.append(f"BTC risk-off subset:    n={report.btc_riskoff_subset_n}  mean +4h={report.btc_riskoff_subset_mean_4h*100:+.3f}%")
    lines.append(f"단조성: +4h={report.monotonicity_4h}  +24h={report.monotonicity_24h}")
    lines.append("")
    lines.append("=== 사전확정 기준 평가 (검증 전 못박음) ===")
    for c in report.criteria:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name:30s}  value={c.value:.4f}  threshold={c.threshold:.4f}")
        if c.detail:
            lines.append(f"         {c.detail}")
    lines.append("")
    final = "PASS → R1 진행" if report.passed else "FAIL → 정직 종료(또는 데이터 충분성 보강 후 재시도)"
    lines.append(f"=== 최종 판정: {final} ===")
    lines.append(f"노트: {report.notes}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-supabase", action="store_true",
                    help="결과를 backtest_runs 테이블에 적재")
    ap.add_argument("--symbols", type=str, default=None,
                    help="콤마 분리 심볼 리스트(미지정 시 ohlcv 전체)")
    ap.add_argument("--oos-start", type=str, default=None,
                    help="OOS 시작 ISO (예: 2025-11-01T00:00:00Z)")
    args = ap.parse_args()

    from analytics.ccs_lite_backtest import record_to_supabase, run_r0

    symbols = args.symbols.split(",") if args.symbols else None
    logger.info("Running CCS-Lite v1.1 R0 ...")
    report = run_r0(symbols=symbols, oos_start_iso=args.oos_start)

    text = _format_report(report)
    print(text, flush=True)

    if args.write_supabase:
        ok = record_to_supabase(report)
        print(f"\n[backtest_runs 적재] {'성공' if ok else '실패(outbox 적재)'}")

    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
