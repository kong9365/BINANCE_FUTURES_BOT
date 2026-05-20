"""
tests/test_weekly_report.py
=====================================================================
WeeklyGPTAnalyst 단위 테스트 — 4개 시나리오.

근거: docs/SPEC_v3.1.md §8-4-3

구성:
  - DB는 tmp_path 임시 SQLite (db.init_db.init_db로 schema.sql + v3.1.1 적용)
  - report_dir도 tmp_path 하위 (repo의 reports/ 미오염)
  - ExpectancyAnalyzer는 실제 인스턴스 + 임시 DB
  - openai_client는 MagicMock (동기 호출 + asyncio.to_thread 래핑에 맞춤)
  - pytest asyncio_mode=auto 이므로 async def test_* 직접 사용

4개 시나리오 expected:
  1. 정상 호출 → reports/ JSON 파일 + weekly_reports 1행 + gpt_call_log 1행
  2. GPT 실패 (APITimeoutError) → 폴백 보고서, 파일+DB행 생성, cost=0
  3. trades 빈 DB → trade_count=0 보고서, GPT는 호출됨
  4. 비용 메트릭 정상 집계 (슬리피지 평균, GPT 비용 합산)
=====================================================================
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from analytics.expectancy import ExpectancyAnalyzer
from analytics.weekly_report import WeeklyGPTAnalyst, WeeklyReport
from db.init_db import init_db

VALID_CONTENT = json.dumps(
    {
        "insights": "주간 거래 패턴 분석 결과입니다.",
        "suggestions": ["TREND_PULLBACK 셋업 검토 권장", "RANGING 진입 기준 테스트 권장"],
        "action_items": ["부진 셋업 비활성화 검토"],
    },
    ensure_ascii=False,
)


# ── fixtures / helpers ──────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path) -> str:
    """schema.sql + v3.1.1 마이그레이션이 적용된 임시 DB 경로."""
    p = tmp_path / "bot.db"
    init_db(p)
    return str(p)


@pytest.fixture
def report_dir(tmp_path) -> str:
    """임시 보고서 디렉토리 (repo reports/ 미오염)."""
    return str(tmp_path / "reports")


def _make_response(content: str, p_tok: int = 1000, c_tok: int = 500) -> MagicMock:
    """openai chat.completions.create 응답 mock."""
    resp = MagicMock()
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp.choices = [choice]
    resp.usage.prompt_tokens = p_tok
    resp.usage.completion_tokens = c_tok
    return resp


def _make_client(response=None, side_effect=None) -> MagicMock:
    """openai 클라이언트 mock (동기 chat.completions.create)."""
    client = MagicMock()
    if side_effect is not None:
        client.chat.completions.create = MagicMock(side_effect=side_effect)
    else:
        client.chat.completions.create = MagicMock(return_value=response)
    return client


def _insert_trade(
    conn: sqlite3.Connection,
    *,
    net_pnl: float = 10.0,
    setup_tag: str = "TREND_PULLBACK",
    regime: str = "TREND_UP",
    slippage: float | None = None,
) -> None:
    """닫힌 거래 1건 삽입 (timestamp = 현재 UTC ISO)."""
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO trades "
        "(timestamp, symbol, action, entry_price, exit_price, quantity, "
        " stop_loss, pnl_usd, pnl_usd_net, setup_tag, regime, slippage_actual_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, "SOLUSDT", "LONG", 100.0, 102.0, 1.0,
         99.0, net_pnl, net_pnl, setup_tag, regime, slippage),
    )


def _count_rows(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ── 시나리오 1: 정상 호출 → 파일 + DB 2개 테이블 ──────────────────

async def test_scenario_1_normal_call(db_path, report_dir, tmp_path):
    conn = sqlite3.connect(db_path)
    try:
        for _ in range(5):
            _insert_trade(conn, net_pnl=10.0)
        conn.commit()
    finally:
        conn.close()

    client = _make_client(response=_make_response(VALID_CONTENT, 1000, 500))
    analyst = WeeklyGPTAnalyst(
        openai_client=client,
        expectancy_analyzer=ExpectancyAnalyzer(db_path),
        db_path=db_path,
        report_dir=report_dir,
    )

    report = await analyst.run(days=7)

    assert isinstance(report, WeeklyReport)
    assert report.gpt_insights == "주간 거래 패턴 분석 결과입니다."
    # gpt-4o-mini: (1000*0.15 + 500*0.60) / 1e6 = 0.00045
    assert report.gpt_call_cost_usd == pytest.approx(0.00045)

    # 보고서 파일 생성
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_file = tmp_path / "reports" / f"weekly_{date_str}.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text(encoding="utf-8"))
    assert saved["gpt_insights"] == "주간 거래 패턴 분석 결과입니다."

    # DB 2개 테이블 각 1행
    assert _count_rows(db_path, "weekly_reports") == 1
    assert _count_rows(db_path, "gpt_call_log") == 1


# ── 시나리오 2: GPT 실패 → 폴백 보고서 ────────────────────────────

async def test_scenario_2_gpt_failure_fallback(db_path, report_dir):
    timeout_err = openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )
    client = _make_client(side_effect=timeout_err)
    analyst = WeeklyGPTAnalyst(
        openai_client=client,
        expectancy_analyzer=ExpectancyAnalyzer(db_path),
        db_path=db_path,
        report_dir=report_dir,
    )

    report = await analyst.run(days=7)

    # 폴백 보고서
    assert report.gpt_insights == "(GPT 호출 실패, 통계만 포함)"
    assert report.gpt_call_cost_usd == 0.0
    assert any("API 키" in item for item in report.action_items)

    # 실패해도 파일 + DB 행은 생성
    assert _count_rows(db_path, "weekly_reports") == 1
    assert _count_rows(db_path, "gpt_call_log") == 1

    # gpt_call_log success=0
    conn = sqlite3.connect(db_path)
    try:
        success, cost = conn.execute(
            "SELECT success, cost_usd FROM gpt_call_log"
        ).fetchone()
    finally:
        conn.close()
    assert success == 0
    assert cost == 0.0


# ── 시나리오 3: trades 빈 DB → 0건 보고서, GPT는 호출됨 ───────────

async def test_scenario_3_empty_trades(db_path, report_dir):
    empty_content = json.dumps(
        {"insights": "데이터 부족 — 결론 보류", "suggestions": [], "action_items": []},
        ensure_ascii=False,
    )
    client = _make_client(response=_make_response(empty_content, 200, 50))
    analyst = WeeklyGPTAnalyst(
        openai_client=client,
        expectancy_analyzer=ExpectancyAnalyzer(db_path),
        db_path=db_path,
        report_dir=report_dir,
    )

    report = await analyst.run(days=7)

    assert report.performance_summary["trade_count"] == 0
    assert report.performance_summary["by_setup"] == []
    # trades 0건이어도 GPT 호출은 수행됨
    assert client.chat.completions.create.called
    assert report.gpt_insights == "데이터 부족 — 결론 보류"


# ── 시나리오 4: 비용 메트릭 정상 집계 ─────────────────────────────

async def test_scenario_4_cost_metrics(db_path, report_dir):
    conn = sqlite3.connect(db_path)
    try:
        # 슬리피지 0.001 / 0.003 → 평균 0.002
        _insert_trade(conn, slippage=0.001)
        _insert_trade(conn, slippage=0.003)
        # 사전 GPT 호출 비용 기록 (run() 호출 전 시점)
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO gpt_call_log "
            "(timestamp, caller, model, cost_usd, success) VALUES (?, ?, ?, ?, ?)",
            (ts, "weekly", "gpt-4o-mini", 0.05, 1),
        )
        conn.commit()
    finally:
        conn.close()

    client = _make_client(response=_make_response(VALID_CONTENT, 1000, 500))
    analyst = WeeklyGPTAnalyst(
        openai_client=client,
        expectancy_analyzer=ExpectancyAnalyzer(db_path),
        db_path=db_path,
        report_dir=report_dir,
    )

    report = await analyst.run(days=7)

    cm = report.cost_metrics
    assert cm["avg_slippage_pct"] == pytest.approx(0.002)
    assert cm["max_slippage_pct"] == pytest.approx(0.003)
    assert cm["trade_count"] == 2
    # _get_cost_metrics는 run()의 GPT 호출 전에 집계 → 사전 기록 0.05만 반영
    assert cm["total_gpt_cost_usd"] == pytest.approx(0.05)
