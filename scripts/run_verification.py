#!/usr/bin/env python3
"""4-엔진 사전 IC 검증 CLI (VERIFICATION_PLAN §5-D)."""
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

from analytics.verification.runner import CANDIDATES, run_verification  # noqa: E402


def _env_local_only() -> bool:
    import os
    return os.environ.get("VERIFICATION_LOCAL_ONLY", "").lower() in ("1", "true", "yes")


def main() -> None:
    ap = argparse.ArgumentParser(description="4후보 3-Gate 검증")
    ap.add_argument(
        "--candidates", default=",".join(CANDIDATES.keys()),
        help="콤마구분 후보 id",
    )
    ap.add_argument("--no-supabase", action="store_true", help="로컬 캐시만 (기본: VERIFICATION_LOCAL_ONLY)")
    ap.add_argument("--write-supabase", action="store_true", help="VERIFICATION_LOCAL_ONLY 무시하고 Supabase 적재 시도")
    args = ap.parse_args()
    cands = [c.strip() for c in args.candidates.split(",") if c.strip()]
    local_only = args.no_supabase or (_env_local_only() and not args.write_supabase)
    result = run_verification(
        candidates=cands,
        persist_supabase=not local_only,
        local_only=local_only,
    )
    for rep in result["reports"]:
        g1, g2, g3 = rep["gate1"]["status"], rep["gate2"]["status"], rep["gate3"]["status"]
        print(f"{rep['candidate_id']:25s} G1={g1} G2={g2} G3={g3} -> {rep['verdict']}")
    print(f"run_id={result['run_id']} symbols={result['symbols']}")


if __name__ == "__main__":
    main()
