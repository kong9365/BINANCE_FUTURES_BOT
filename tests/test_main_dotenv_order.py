"""
tests/test_main_dotenv_order.py
=====================================================================
M15-fix 회귀 방지: main_7590 이 config.settings import *전에* .env 를 로드하는지.

배경 (2026-05-29 실측 버그):
  config/settings.py 는 import 시점에 os.environ 에서 ACTIVE_STRATEGY /
  LIVE_PROBE_BUDGET_USDT / PROTECTED_SYMBOLS 를 읽어 *_CONFIG 를 만든다.
  과거 main_7590 은 load_dotenv() 를 main() 안(=import 이후)에서 호출해서,
  STRATEGY_CONFIG 가 .env 의 ACTIVE_STRATEGY=daily_tsmom 을 보지 못하고
  기본값 oi_surge 로 굳었다 → Paper 봇이 9개 실패전략 중 하나로 가동.

  수정: main_7590 모듈 상단(프로젝트 import 전)에서 load_dotenv() 호출.

이 테스트는 *별도 프로세스* 에서 임시 .env(ACTIVE_STRATEGY=daily_tsmom)를 두고
main_7590 → config.settings import 순서를 그대로 재현해 검증한다(import 캐시
회피 위해 subprocess 필수).
=====================================================================
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def _run_in_subprocess(env_text: str, tmp_path: Path) -> str:
    """임시 cwd 에 .env 를 쓰고, 거기서 main_7590 import 후 active_strategy 출력."""
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")

    code = (
        "import main_7590\n"  # 모듈 상단 load_dotenv() 가 cwd 의 .env 로드
        "from config.settings import STRATEGY_CONFIG\n"
        "print('RESULT=' + STRATEGY_CONFIG.active_strategy)\n"
    )

    # 부모 env 에서 ACTIVE_STRATEGY 제거 — load_dotenv 는 기존 키를 덮지 않으므로
    # 이게 남아있으면 .env 로드 순서를 검증하지 못한다.
    child_env = {k: v for k, v in os.environ.items() if k != "ACTIVE_STRATEGY"}
    child_env["PYTHONPATH"] = PROJECT_ROOT
    child_env["PYTHONIOENCODING"] = "utf-8"
    # 네트워크 클라이언트 생성 방지 위해 키 미설정 유지 (import 만으로는 생성 안 함).

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess 실패: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    for line in result.stdout.splitlines():
        if line.startswith("RESULT="):
            return line[len("RESULT="):].strip()
    raise AssertionError(f"RESULT 라인 없음. stdout={result.stdout}")


def test_active_strategy_from_dotenv_is_respected(tmp_path):
    """`.env` 의 ACTIVE_STRATEGY=daily_tsmom 이 STRATEGY_CONFIG 에 반영되어야 한다.

    import-order 버그가 재발하면 oi_surge(기본값)가 나와 실패한다.
    """
    strat = _run_in_subprocess("ACTIVE_STRATEGY=daily_tsmom\n", tmp_path)
    assert strat == "daily_tsmom", (
        f"import-order 회귀: .env=daily_tsmom 인데 STRATEGY_CONFIG={strat} "
        "(load_dotenv 가 config.settings import 이후에 호출됨)"
    )


def test_active_strategy_dotenv_breakout(tmp_path):
    """다른 값(breakout)도 동일하게 .env 에서 반영되는지(고정 daily_tsmom 아님)."""
    strat = _run_in_subprocess("ACTIVE_STRATEGY=breakout\n", tmp_path)
    assert strat == "breakout", f".env=breakout 인데 STRATEGY_CONFIG={strat}"


def test_no_dotenv_falls_back_to_default(tmp_path):
    """.env 에 ACTIVE_STRATEGY 미설정 시 기본값(oi_surge)으로 폴백."""
    strat = _run_in_subprocess("# 빈 .env\n", tmp_path)
    assert strat == "oi_surge", f"기본값 기대 oi_surge, 실제={strat}"
