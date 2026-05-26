"""
scripts/run_ml_r0.py
=====================================================================
ML EV Engine v2 R0 실행 스크립트.

사용:
  python scripts/run_ml_r0.py [--write-supabase] [--top-n 30]
                                [--train-months 14] [--val-months 4] [--oos-months 6]

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
logger = logging.getLogger("ml_r0")


def _format_report(report) -> str:
    lines = []
    lines.append(f"=== ML EV Engine v2 R0 결과 ({report.run_id}) ===")
    lines.append(f"기간: {report.started_at} → {report.finished_at}")
    lines.append(f"심볼: {report.n_symbols}  피처: {report.n_features}")
    lines.append(f"표본: train={report.n_train}  val={report.n_val}  oos={report.n_oos}")
    lines.append("")
    lines.append("--- Regression sanity (OOS) ---")
    if report.ridge_eval:
        lines.append(f"Ridge:   Spearman={report.ridge_eval['spearman_corr']:+.4f}  MAE={report.ridge_eval['mae']:.4f}  decile_mono={report.ridge_eval['decile_monotonic']}")
    if report.lgb_eval:
        lines.append(f"LGBM:    Spearman={report.lgb_eval['spearman_corr']:+.4f}  MAE={report.lgb_eval['mae']:.4f}  decile_mono={report.lgb_eval['decile_monotonic']}")
    lines.append(f"Best model: {report.best_model}")
    lines.append("")
    lines.append("--- Trading 메트릭 (EV-진입 OOS) ---")
    lines.append(f"n_trades:    {report.n_trades}")
    lines.append(f"PF (net):    {report.pf:.3f}")
    lines.append(f"Sharpe:      {report.sharpe:.2f}")
    lines.append(f"avg trade:   {report.mean_trade_net*100:+.3f}%")
    lines.append(f"Max DD:      {report.max_dd*100:.1f}%")
    lines.append(f"횡단면 양(+) 종목 비율: {report.cross_pair_pos_share*100:.1f}%")
    lines.append(f"단일 종목 기여 최댓값:   {report.single_symbol_contrib*100:.1f}%")
    lines.append("")
    lines.append("=== 사전확정 10기준 평가 ===")
    for c in report.criteria:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name:32s}  value={c.value:.5f}  threshold={c.threshold:.5f}")
        if c.detail:
            lines.append(f"         {c.detail}")
    lines.append("")
    final = "PASS → R1 walk-forward 진행" if report.passed else "FAIL → 정직 종료"
    lines.append(f"=== 최종 판정: {final} ===")
    lines.append(f"노트: {report.notes}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-supabase", action="store_true")
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--train-months", type=int, default=14)
    ap.add_argument("--val-months", type=int, default=4)
    ap.add_argument("--oos-months", type=int, default=6)
    ap.add_argument("--horizon-bars", type=int, default=4)
    args = ap.parse_args()

    from analytics.ml_backtest import record_to_supabase, run_r0

    logger.info("Running ML EV Engine v2 R0 ...")
    report = run_r0(
        top_n=args.top_n,
        is_train_months=args.train_months,
        is_val_months=args.val_months,
        oos_months=args.oos_months,
        horizon_bars=args.horizon_bars,
    )
    print(_format_report(report), flush=True)

    if args.write_supabase:
        ok = record_to_supabase(report)
        print(f"\n[backtest_runs 적재] {'성공' if ok else '실패(outbox 적재)'}")

    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
