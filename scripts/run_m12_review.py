"""
scripts/run_m12_review.py
=====================================================================
M12 — setup_registry 결과 검토 + R0_QUALIFIED 결정 + 보고서 자동 생성.

근거:
  - REFACTOR_PLAN_v2_BLUEPRINT.md M12
  - M11 실측 결과 (setup_registry + setup_evaluations)
  - 청사진 §7.5 Stage 3 옵션 A/B/C

사용:
    python scripts/run_m12_review.py [--db PATH]

결과:
    docs/M12_R0_DECISION_REPORT.md 자동 생성.
    audit_log SETUP_STATUS_CHANGE 이벤트 확인 (M11 자동 적재된 것).
=====================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit.audit_log import EventType  # noqa: E402
from audit.audit_logger import AuditLogger  # noqa: E402
from db.init_db import init_db  # noqa: E402
from registry.setup_registry import SetupRegistry  # noqa: E402

logger = logging.getLogger("run_m12_review")


def parse_args():
    p = argparse.ArgumentParser(description="M12 — setup_registry 결과 검토")
    p.add_argument("--db", type=str, default=None, help="DB 경로 override")
    return p.parse_args()


def review_setup_registry(db_path: str) -> dict:
    """setup_registry 전체 + 7기준 판정 요약."""
    init_db(db_path)
    registry = SetupRegistry(db_path=db_path)
    active = registry.get_active_setups()

    # 비활성 (DISABLED/COOLING) 포함
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT setup_id, name, status, last_metric_n, last_metric_pf, "
            "       last_metric_expectancy_r, last_metric_avg_win_loss_ratio, "
            "       last_metric_mdd_pct, last_metric_single_symbol_max_pct, "
            "       last_metric_top3_excluded_pf, last_metric_win_rate, "
            "       last_evaluation_ts, reason "
            "FROM setup_registry ORDER BY updated_at DESC"
        ).fetchall()
        # setup_evaluations 카운트
        eval_counts = {}
        eval_rows = conn.execute(
            "SELECT setup_id, COUNT(*), MAX(ts) FROM setup_evaluations "
            "GROUP BY setup_id"
        ).fetchall()
        for r in eval_rows:
            eval_counts[r[0]] = {"count": r[1], "last_ts": r[2]}
    finally:
        conn.close()

    return {
        "all_setups": [dict(r) for r in rows],
        "active_count": len(active),
        "total_count": len(rows),
        "eval_counts": eval_counts,
    }


def evaluate_7_criteria(setup: dict) -> dict:
    """7기준 각 통과 여부 + 합계."""
    n = setup.get("last_metric_n") or 0
    pf = setup.get("last_metric_pf") or 0
    expR = setup.get("last_metric_expectancy_r") or 0
    ratio = setup.get("last_metric_avg_win_loss_ratio") or 0
    mdd = setup.get("last_metric_mdd_pct") or 0
    single = setup.get("last_metric_single_symbol_max_pct") or 0
    top3_excl = setup.get("last_metric_top3_excluded_pf") or 0

    criteria = {
        "n >= 200": n >= 200,
        "Net PF >= 1.25": pf >= 1.25,
        "Expectancy_R > 0": expR > 0,
        "avg_win/loss >= 1.5": ratio >= 1.5,
        "MDD <= 25%": mdd <= 25.0,
        "Single symbol < 25%": single < 25.0,
        "Top-3 excluded PF >= 1.0": top3_excl >= 1.0,
    }
    passed = sum(1 for ok in criteria.values() if ok)
    return {
        "criteria": criteria,
        "passed_count": passed,
        "total": 7,
        "verdict": (
            "PASS (R0_QUALIFIED)" if passed == 7
            else "CONDITIONAL (1 fail)" if passed == 6
            else f"FAIL (DISABLED, {7 - passed} fails)"
        ),
    }


def generate_decision_report(
    summary: dict, report_path: Path,
) -> None:
    """M12_R0_DECISION_REPORT.md 자동 생성."""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# M12 — R0_QUALIFIED 결정 보고서\n\n")
        f.write(f"> **Generated**: {datetime.now(timezone.utc).isoformat()}\n")
        f.write("> **Milestone**: M12 (setup_registry 결과 검토)\n")
        f.write("> **Plan**: docs/REFACTOR_PLAN_v2_BLUEPRINT.md M12\n\n")
        f.write("---\n\n")

        f.write("## 1. Setup Registry 전체 상태\n\n")
        f.write(f"- 총 setup: **{summary['total_count']}**\n")
        f.write(f"- 활성 (R0_QUALIFIED + PAPER_ONLY): **{summary['active_count']}**\n")
        f.write(f"- 평가 이력 (setup_evaluations): {len(summary['eval_counts'])} setups\n\n")

        f.write("## 2. Setup 별 7기준 판정\n\n")
        for s in summary["all_setups"]:
            f.write(f"### `{s['setup_id']}`\n\n")
            f.write(f"- **status**: {s.get('status', '—')}\n")
            f.write(f"- **last_evaluation_ts**: {s.get('last_evaluation_ts', '—')}\n")
            f.write(f"- **reason**: {s.get('reason', '—')}\n\n")

            eval_info = summary["eval_counts"].get(s["setup_id"])
            if eval_info:
                f.write(f"- 평가 횟수: {eval_info['count']}\n")
                f.write(f"- 최근 평가: {eval_info['last_ts']}\n\n")

            judgment = evaluate_7_criteria(s)
            f.write(f"**7기준 판정: {judgment['verdict']} ({judgment['passed_count']}/7)**\n\n")
            f.write("| 기준 | 측정값 | 통과 |\n")
            f.write("|---|---|---|\n")
            f.write(f"| n >= 200 | {s.get('last_metric_n')} | {'YES' if judgment['criteria']['n >= 200'] else 'NO'} |\n")
            f.write(f"| Net PF >= 1.25 | {s.get('last_metric_pf')} | {'YES' if judgment['criteria']['Net PF >= 1.25'] else 'NO'} |\n")
            f.write(f"| Expectancy_R > 0 | {s.get('last_metric_expectancy_r')} | {'YES' if judgment['criteria']['Expectancy_R > 0'] else 'NO'} |\n")
            f.write(f"| avg_win/loss >= 1.5 | {s.get('last_metric_avg_win_loss_ratio')} | {'YES' if judgment['criteria']['avg_win/loss >= 1.5'] else 'NO'} |\n")
            f.write(f"| MDD <= 25% | {s.get('last_metric_mdd_pct')}% | {'YES' if judgment['criteria']['MDD <= 25%'] else 'NO'} |\n")
            f.write(f"| Single symbol < 25% | {s.get('last_metric_single_symbol_max_pct')}% | {'YES' if judgment['criteria']['Single symbol < 25%'] else 'NO'} |\n")
            f.write(f"| Top-3 excluded PF >= 1.0 | {s.get('last_metric_top3_excluded_pf')} | {'YES' if judgment['criteria']['Top-3 excluded PF >= 1.0'] else 'NO'} |\n\n")

        f.write("---\n\n")
        f.write("## 3. 운영자 결정 옵션\n\n")
        # 적어도 1개 R0_QUALIFIED 있는지
        r0_setups = [s for s in summary["all_setups"]
                     if s.get("status") == "R0_QUALIFIED"]
        all_disabled = all(s.get("status") in ("DISABLED", "COOLING")
                           for s in summary["all_setups"])

        if r0_setups:
            f.write("### ✅ Phase 1.5 진입 가능\n\n")
            f.write(f"R0_QUALIFIED setup {len(r0_setups)}개:\n")
            for s in r0_setups:
                f.write(f"- `{s['setup_id']}` (PF {s.get('last_metric_pf')})\n")
            f.write("\n→ [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](PHASE1_5_MICRO_LIVE_PROMPT.md) 절차 진행\n\n")
        elif all_disabled:
            f.write("### ❌ 모든 setup DISABLED\n\n")
            f.write("M11 실측 결과 모든 setup 이 7기준 fail (데이터 부족 또는 알파 부재).\n\n")
            f.write("**선택지**:\n\n")
            f.write("**옵션 A — 데이터 확장 후 재실행** (권장):\n")
            f.write("```bash\n")
            f.write("# mainnet 데이터 read-only (USE_TESTNET=false 임시) + 18~50 종목 + 2~5년\n")
            f.write("USE_TESTNET=false python scripts/run_m11_backtest.py --backfill --universe-size 18 --years 3\n")
            f.write("```\n\n")
            f.write("**옵션 B — Stage 3 (청사진 §7.5)**:\n")
            f.write("- A. Manual semi-discretionary 전환\n")
            f.write("- B. Buy-and-hold + 50d MA cash exit (Grayscale 2023)\n")
            f.write("- C. 운영자 가설 청취 (다른 archetype)\n\n")
            f.write("**옵션 C — HANDOFF '알파 영구 중단' 결정 *재확정***:\n")
            f.write("- 2026-05-23 결정 (9개 전략군 모두 fail) 유지\n")
            f.write("- 본 리팩토링은 *거버넌스 인프라 완성* (M0~M10 + 대시보드) 의 가치만 인정\n")
            f.write("- 알파 추구는 *영구* 중단 (HANDOFF.md 라인 14-15 정합)\n\n")
        else:
            f.write("### ⚠️ CONDITIONAL — 운영자 검토 필요\n\n")
            f.write("일부 setup borderline (6/7 통과). 운영자 명시 결정 필요:\n\n")
            for s in summary["all_setups"]:
                judgment = evaluate_7_criteria(s)
                if 5 <= judgment['passed_count'] <= 6:
                    f.write(f"- `{s['setup_id']}`: {judgment['verdict']}\n")
            f.write("\n옵션:\n")
            f.write("- CONDITIONAL paper 운영 1~2주 추가 검증 (대시보드 모니터링)\n")
            f.write("- 운영자가 직접 status 변경 (`setup_registry.set_status`)\n\n")

        f.write("---\n\n")
        f.write("## 4. 다음 단계\n\n")
        f.write("1. 본 보고서 검토\n")
        f.write("2. 대시보드 페이지 6 (Setup Registry) 에서 시각화 확인:\n")
        f.write("   - `scripts/run_dashboard.bat` → http://localhost:8501\n")
        f.write("3. 운영자 명시 결정 (A/B/C 또는 데이터 확장)\n")
        f.write("4. [docs/HANDOFF.md](HANDOFF.md) 갱신 (재검토 최종 결과)\n")
        f.write("5. PASS 시: [docs/PHASE1_5_MICRO_LIVE_PROMPT.md](PHASE1_5_MICRO_LIVE_PROMPT.md) 절차 진행\n\n")


async def log_setup_status_change_audit(
    db_path: str, setup_id: str, new_status: str, reason: str,
) -> None:
    """audit_log SETUP_STATUS_CHANGE 이벤트 (재기록 옵션)."""
    audit_logger = AuditLogger(db_path=db_path)
    try:
        await audit_logger.log_event(
            event_type=EventType.SETUP_STATUS_CHANGE,
            payload={
                "setup_id": setup_id,
                "new_status": new_status,
                "reason": reason,
                "decision_by": "operator",
                "milestone": "M12",
            },
            actor="operator",
            related_setup_id=setup_id,
        )
        logger.info("[M12] SETUP_STATUS_CHANGE 이벤트 기록 (audit_log)")
    except Exception as e:  # noqa: BLE001
        logger.warning("[M12] audit_log 기록 실패: %s", e)


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = args.db or os.environ.get("DB_PATH") or (
        "data/bot.db" if os.environ.get("USE_TESTNET", "").strip().lower()
        in ("1", "true", "yes") else "data/bot_live.db"
    )

    logger.info("[M12] Setup Registry 검토 시작 (db=%s)", db_path)
    summary = review_setup_registry(db_path)

    # 콘솔 출력 (안전 — cp949 대응)
    try:
        print(f"\n=== M12 Setup Registry Review ===")
        print(f"Total setups: {summary['total_count']}")
        print(f"Active (R0/PAPER): {summary['active_count']}")
        print()
        for s in summary["all_setups"]:
            judgment = evaluate_7_criteria(s)
            print(f"  [{s.get('status', '?')}] {s['setup_id']}")
            print(f"     {judgment['verdict']} ({judgment['passed_count']}/7)")
            print(f"     PF={s.get('last_metric_pf')}, top3_excl={s.get('last_metric_top3_excluded_pf')}")
    except UnicodeEncodeError:
        pass

    # 보고서 자동 생성
    report_path = Path("docs/M12_R0_DECISION_REPORT.md")
    generate_decision_report(summary, report_path)
    logger.info("[M12] 보고서 저장: %s", report_path)

    # exit code: setup 모두 DISABLED → 2, 1개라도 R0 → 0
    has_r0 = any(s.get("status") == "R0_QUALIFIED" for s in summary["all_setups"])
    has_conditional = any(s.get("status") == "CONDITIONAL" for s in summary["all_setups"])
    if has_r0:
        sys.exit(0)
    elif has_conditional:
        sys.exit(1)
    sys.exit(2)


if __name__ == "__main__":
    main()
