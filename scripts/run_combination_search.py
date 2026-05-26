#!/usr/bin/env python3
"""전략 조합 자동 탐색 — PASS 발견까지 반복."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from analytics.verification.combination_search import run_combination_search  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="전략 조합 3-Gate 탐색")
    ap.add_argument("--phase", default="1,2", help="1=카탈로그, 2=파라미터그리드")
    ap.add_argument("--no-stop", action="store_true", help="PASS 발견해도 전체 실행")
    args = ap.parse_args()
    phases = [int(x.strip()) for x in args.phase.split(",") if x.strip()]
    result = run_combination_search(
        phases=phases,
        stop_on_pass=not args.no_stop,
        local_only=True,
    )
    print(f"run_id={result['run_id']} tested={result['tested']} pass={result['pass_count']} conditional={result['conditional_count']}")
    if result["winner"]:
        w = result["winner"]
        print(f"WINNER: {w['combo_id']} -> {w['verdict']}")
    else:
        print("PASS none - top5:")
        for r in result["top10"][:5]:
            g1, g2, g3 = r["gate1"]["status"], r["gate2"]["status"], r["gate3"]["status"]
            print(f"  {r['combo_id']:30s} G1={g1} G2={g2} G3={g3} -> {r['verdict']}")


if __name__ == "__main__":
    main()
