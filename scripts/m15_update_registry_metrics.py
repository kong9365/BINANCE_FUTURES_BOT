"""
scripts/m15_update_registry_metrics.py
=====================================================================
M15-1 — setup_registry 메트릭을 M13 v2 실측값으로 갱신 (일회성, 멱등).

배경:
  - 아키텍처 검토 (2026-05-28) 부수 발견: setup_registry 의 last_metric_* 가
    seed/예시값 (n=250, PF=1.35, MDD=20%, top3=1.15) 으로, M13 v2 실측과 불일치.
  - 본 스크립트는 register(멱등) → update_metrics(M13 v2 실측) →
    set_status(CONDITIONAL) 순으로 registry 를 *진실되게* 만든다.

근거:
  - docs/SETUP_VERIFICATION_REPORT_v3_2_0.md (M13 v2 7기준 실측)
  - CLAUDE.md TIER 1 #7 (MDD=9.92% 채택, 204.74% 는 모델 결함)
  - 운영자 결정 (2026-05-26 GO): 6/7 통과 → CONDITIONAL

설계:
  - 라이브러리 코드에 메트릭 값을 박지 않는다 (seed-value drift 재발 방지).
    값은 본 스크립트에만 명시 + 출처 문자열 동반.
  - update_metrics.passed 는 bool (tristate 아님) → CONDITIONAL 은 set_status 로 인코딩.
  - 멱등: 반복 실행해도 setup_evaluations 에 평가 1건 추가 + last_metric_* 덮어쓰기.

사용:
  python scripts/m15_update_registry_metrics.py                 # 기본 data/bot.db
  python scripts/m15_update_registry_metrics.py --db-path data/bot_live.db
=====================================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# 프로젝트 루트 sys.path (python scripts/xxx.py 직접 실행 지원)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from registry.setup_registry import SetupMetrics, SetupRegistry, SetupStatus
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("m15_update_registry")

# ── M13 v2 실측 (docs/SETUP_VERIFICATION_REPORT_v3_2_0.md) ──
M13_V2_METRICS = SetupMetrics(
    n=216,
    pf=1.260,
    expectancy_r=0.1178,
    avg_win_loss_ratio=1.357,
    mdd_pct=9.92,                 # CLAUDE.md TIER 1 #7: 9.92% 채택 (raw 204.74% 는 모델 결함)
    single_symbol_max_pct=8.07,
    top3_excluded_pf=1.102,
    win_rate=0.481,
)
FAILED_CRITERIA = ["min_avg_win_loss_ratio"]   # 1.357 < 1.5 (borderline -0.143)

PROVENANCE = {
    "source_report": "docs/SETUP_VERIFICATION_REPORT_v3_2_0.md",
    "evaluation": "M13 v2 mainnet 16종목 × 3년 1d (옵션 A)",
    "verdict": "6/7 통과 → CONDITIONAL",
    "mdd_note": (
        "MDD=9.92% (자본대비% + position 5% 보정). "
        "M13 v1 raw 204.74% 는 signal_validation 누적 pnl 모델 결함 — 폐기. "
        "HANDOFF A1 신뢰 하네스 8.9% 와 정합."
    ),
    "top3_note": "top3_excluded_pf=1.102 — ZEC 단일 97% 행운 패턴 회피 확인 (2026-05-22 A2-②)",
    "operator_go": "2026-05-26 — 7기준 조건부 완화 GO (TIER 1 #7)",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="M15-1 registry 메트릭 갱신 (M13 v2 실측)")
    parser.add_argument("--db-path", default="data/bot.db", help="sqlite DB 경로")
    args = parser.parse_args()

    registry = SetupRegistry(db_path=args.db_path)
    setup_id = DailyTSMOMDonchianSkill.SETUP_ID

    logger.info("[M15-1] db=%s setup_id=%s", args.db_path, setup_id)

    # 1) register (멱등 — 없으면 생성, 있으면 params_hash 갱신)
    registry.register(DailyTSMOMDonchianSkill)

    # 2) update_metrics — M13 v2 실측 (passed=False; CONDITIONAL 은 set_status 로)
    full_report = json.dumps(
        {
            "metrics": {
                "n": M13_V2_METRICS.n,
                "pf": M13_V2_METRICS.pf,
                "expectancy_r": M13_V2_METRICS.expectancy_r,
                "avg_win_loss_ratio": M13_V2_METRICS.avg_win_loss_ratio,
                "mdd_pct": M13_V2_METRICS.mdd_pct,
                "single_symbol_max_pct": M13_V2_METRICS.single_symbol_max_pct,
                "top3_excluded_pf": M13_V2_METRICS.top3_excluded_pf,
                "win_rate": M13_V2_METRICS.win_rate,
            },
            "failed_criteria": FAILED_CRITERIA,
            "provenance": PROVENANCE,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    registry.update_metrics(
        setup_id=setup_id,
        metrics=M13_V2_METRICS,
        evaluation_type="backtest",
        passed=False,                       # 7기준 중 1개 fail (avg_win/loss)
        failed_criteria=FAILED_CRITERIA,
        full_report_json=full_report,
    )

    # 3) set_status — CONDITIONAL (6/7, 운영자 GO)
    registry.set_status(
        setup_id,
        SetupStatus.CONDITIONAL,
        reason=(
            "M13 v2: 6/7 통과 (avg_win/loss 1.357 borderline). "
            "운영자 GO 2026-05-26 → Paper 운영 CONDITIONAL."
        ),
    )

    # 4) 검증 출력
    s = registry.get_setup(setup_id)
    if s is None:
        logger.error("[M15-1] 갱신 후 setup 조회 실패")
        return 1
    logger.info(
        "[M15-1] 완료 — status=%s n=%s PF=%s MDD=%s%% top3=%s",
        s["status"], s["last_metric_n"], s["last_metric_pf"],
        s["last_metric_mdd_pct"], s["last_metric_top3_excluded_pf"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
