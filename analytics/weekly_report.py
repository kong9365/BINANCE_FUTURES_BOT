"""
analytics/weekly_report.py
=====================================================================
WeeklyGPTAnalyst — 오프라인 주간 분석 (GPT 활용)

근거: docs/SPEC_v3.1.md §8-4-2 (전체 코드)

GPT를 실시간 의사결정에서 완전히 분리한다 (CLAUDE.md TIER 3: GPT 실시간
의사결정 호출 금지). 주 1회 배치 실행, 결과는 텍스트 보고서로만 저장하며
파라미터 자동 수정은 하지 않는다 — 운영자가 읽고 판단.

SPEC §8-4-2 대비 변경 (모두 사용자 지시 — 세션 10):
  - 보강 A: run() / _call_gpt() 를 async 화. 동기 openai SDK 호출은
            asyncio.to_thread 로 래핑 (main 루프 블로킹 방지).
  - 보강 B: ExpectancyAnalyzer 를 run() 내부 생성 대신 생성자 주입.
  - 보강 C: _save_report_db() 가 weekly_reports 외에 gpt_call_log 도 기록
            (GPT 호출 감사 추적 — §13-2 gpt_call_log 테이블).
  - 보강 D: GPT 실패 시 gpt_insights = "(GPT 호출 실패, 통계만 포함)".
            에러 상세는 action_items 로 옮김.
  - 보강 E: 보고서 파일명 weekly_YYYY-MM-DD.json (SPEC 은 초단위 타임스탬프).

v3.1.2 정정 ① (CORRECTIONS_v3.1.2.md 참조):
  - SPEC §8-4-2 는 datetime.now() (naive) 로 generated_at 및 조회 윈도우
    (since) 를 산출한다. trades.timestamp / gpt_call_log.timestamp 는 UTC
    ISO 로 저장되므로, naive 로컬시각(예: KST)과 비교하면 윈도우가 9시간
    어긋나 일부 거래가 누락된다. → 모든 datetime 을 UTC tz-aware 로 정정.
=====================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    """주간 분석 보고서 (SPEC §8-4-2 필드 그대로).

    Attributes:
        generated_at: 생성 시각 (UTC ISO).
        period_days: 집계 기간 (일).
        performance_summary: 전체·셋업별 성과 요약 dict.
        gpt_insights: GPT 분석 텍스트 (실패 시 폴백 문구).
        parameter_suggestions: GPT 의 파라미터 조정 제안 (자동 적용 X).
        regime_accuracy: 레짐별 성과 dict (직렬화 가능).
        action_items: 즉시 실행 개선사항.
        cost_metrics: 슬리피지 평균, GPT 비용 등.
        gpt_call_cost_usd: 이번 GPT 호출 비용 (USD).
    """

    generated_at: str
    period_days: int
    performance_summary: dict[str, Any]
    gpt_insights: str
    parameter_suggestions: list[str]
    regime_accuracy: dict[str, Any]
    action_items: list[str]
    cost_metrics: dict[str, Any]
    gpt_call_cost_usd: float = 0.0


class WeeklyGPTAnalyst:
    """주간 성과 분석 + GPT 인사이트 생성.

    의존성:
        - analytics.expectancy.ExpectancyAnalyzer (생성자 주입 — 보강 B)
        - openai.OpenAI client (동기 클라이언트, asyncio.to_thread 로 래핑)
    """

    def __init__(
        self,
        openai_client,
        expectancy_analyzer,
        db_path: str,
        model: str = "gpt-4o-mini",
        report_dir: str = "reports",
        max_tokens: int = 1500,
        temperature: float = 0.3,
        timeout_seconds: int = 30,
        gpt_input_price_per_1m: float = 0.150,
        gpt_output_price_per_1m: float = 0.600,
    ) -> None:
        """WeeklyGPTAnalyst 초기화.

        Args:
            openai_client: openai.OpenAI 인스턴스 (동기 클라이언트).
            expectancy_analyzer: ExpectancyAnalyzer 인스턴스 (보강 B).
            db_path: sqlite3 DB 경로 (cost_metrics 조회 + 보고서 기록용).
            model: GPT 모델명.
            report_dir: 보고서 JSON 저장 디렉토리.
            max_tokens: GPT 응답 최대 토큰.
            temperature: GPT temperature.
            timeout_seconds: GPT 호출 타임아웃 (초).
            gpt_input_price_per_1m: 입력 토큰 1M당 단가 (USD). gpt-4o-mini 기본 $0.150.
                하드코딩 회피용 파라미터 — 가격 변경 시 호출부에서 주입.
            gpt_output_price_per_1m: 출력 토큰 1M당 단가 (USD). gpt-4o-mini 기본 $0.600.
        """
        self.openai = openai_client
        self.expectancy = expectancy_analyzer
        self.db_path = db_path
        self.model = model
        self.report_dir = report_dir
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.gpt_input_price_per_1m = gpt_input_price_per_1m
        self.gpt_output_price_per_1m = gpt_output_price_per_1m

        os.makedirs(report_dir, exist_ok=True)

    # ── 메인 ────────────────────────────────────────────────────

    async def run(self, days: int = 7) -> WeeklyReport:
        """주간 분석 실행 → WeeklyReport 반환 + 파일 저장 + DB 기록.

        보강 A·B: 비동기 메서드이며, 주입된 ExpectancyAnalyzer 를 사용한다.
        GPT 호출이 실패해도 폴백 보고서로 파일·DB 기록은 항상 수행한다.

        Args:
            days: 집계 기간 (일).
        Returns:
            생성된 WeeklyReport.
        """
        overall = self.expectancy.overall(days=days)
        by_setup = self.expectancy.by_setup(days=days)
        by_regime_stats = self.expectancy.by_regime(days=days)
        by_regime = self._regime_stats_to_dict(by_regime_stats)
        cost_metrics = self._get_cost_metrics(days)

        # ── GPT 호출 ──
        prompt = self._build_prompt(overall, by_setup, by_regime, cost_metrics, days)
        gpt = await self._call_gpt(prompt)
        gpt_result = gpt["result"]

        # ── 보고서 생성 ──
        report = WeeklyReport(
            # v3.1.2 정정: naive datetime.now() → UTC tz-aware
            generated_at=datetime.now(timezone.utc).isoformat(),
            period_days=days,
            performance_summary={
                "trade_count": overall.trade_count,
                "win_rate": overall.win_rate,
                "expectancy_R": overall.expectancy_R,
                "avg_R": overall.avg_R,
                "total_pnl_usdt": overall.total_pnl_usdt,
                "by_setup": [
                    {
                        "tag": s.setup_tag,
                        "n": s.trade_count,
                        "wr": s.win_rate,
                        "exp": s.expectancy_R,
                    }
                    for s in by_setup
                ],
            },
            gpt_insights=gpt_result.get("insights", ""),
            parameter_suggestions=gpt_result.get("suggestions", []),
            regime_accuracy=by_regime,
            action_items=gpt_result.get("action_items", []),
            cost_metrics=cost_metrics,
            gpt_call_cost_usd=gpt["cost_usd"],
        )

        # ── 저장 ──
        self._save_report_file(report)
        self._save_report_db(report, prompt, gpt)
        return report

    async def _call_gpt(self, prompt: str) -> dict[str, Any]:
        """GPT 호출. 실패 시 폴백 결과 반환 (보강 A·D).

        동기 openai SDK 호출을 asyncio.to_thread 로 래핑한다.

        Args:
            prompt: 사용자 프롬프트.
        Returns:
            dict — result(파싱된 응답), response_text, prompt_tokens,
            completion_tokens, cost_usd, latency_ms, success, error_msg.
        """
        start = time.time()
        try:
            response = await asyncio.to_thread(
                self.openai.chat.completions.create,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 알고리즘 트레이딩 성과 분석 전문가입니다. "
                            "주어진 거래 데이터를 분석하고 실용적인 개선 방안을 제안하세요. "
                            "JSON 형식으로만 응답하며, 파라미터 자동 수정을 지시하지 말고 "
                            "운영자가 검토할 제안만 텍스트로 제시하세요."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                timeout=self.timeout_seconds,
            )
            content = response.choices[0].message.content
            usage = response.usage
            # gpt-4o-mini 가격: input $0.150/1M, output $0.600/1M (생성자 파라미터로 주입)
            cost = (
                usage.prompt_tokens * self.gpt_input_price_per_1m
                + usage.completion_tokens * self.gpt_output_price_per_1m
            ) / 1_000_000
            latency_ms = (time.time() - start) * 1000
            return {
                "result": json.loads(content),
                "response_text": content,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_usd": round(cost, 6),
                "latency_ms": round(latency_ms, 1),
                "success": True,
                "error_msg": None,
            }
        except Exception as e:  # noqa: BLE001 — GPT 실패는 폴백으로 흡수
            logger.error("[WeeklyAnalyst] GPT 호출 실패: %s: %s", type(e).__name__, e)
            latency_ms = (time.time() - start) * 1000
            return {
                # 보강 D: 폴백 insights 문구 고정
                "result": {
                    "insights": "(GPT 호출 실패, 통계만 포함)",
                    "suggestions": [],
                    "action_items": [
                        f"GPT 호출 실패 ({type(e).__name__}) — API 키/네트워크 확인"
                    ],
                },
                "response_text": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": round(latency_ms, 1),
                "success": False,
                "error_msg": f"{type(e).__name__}: {e}",
            }

    def _build_prompt(
        self, overall, by_setup, by_regime: dict, cost_metrics: dict, days: int
    ) -> str:
        """GPT 프롬프트 문자열을 구성한다 (SPEC §8-4-2 프롬프트 1:1).

        Args:
            overall: 전체 Stats.
            by_setup: setup_tag별 Stats 리스트.
            by_regime: 레짐별 성과 (직렬화 가능한 dict).
            cost_metrics: 비용 지표 dict.
            days: 집계 기간 (일).
        Returns:
            완성된 프롬프트 문자열.
        """
        underperforming = [
            s for s in by_setup if s.expectancy_R < 0 and s.trade_count >= 3
        ]
        overperforming = [
            s for s in by_setup if s.expectancy_R > 0.3 and s.trade_count >= 3
        ]

        return f"""## 지난 {days}일 거래 성과 분석

### 전체 성과
- 총 거래: {overall.trade_count}건
- 승률: {overall.win_rate * 100:.1f}%
- Expectancy: {overall.expectancy_R:.3f}R

### 셋업별 성과
부진 (expectancy < 0, n>=3):
{json.dumps([{"tag": s.setup_tag, "n": s.trade_count, "wr": round(s.win_rate, 2), "exp": round(s.expectancy_R, 3)} for s in underperforming], ensure_ascii=False)}

우수 (expectancy > 0.3, n>=3):
{json.dumps([{"tag": s.setup_tag, "n": s.trade_count, "wr": round(s.win_rate, 2), "exp": round(s.expectancy_R, 3)} for s in overperforming], ensure_ascii=False)}

### 레짐별 성과
{json.dumps(by_regime, ensure_ascii=False, indent=2)}

### 비용 지표
{json.dumps(cost_metrics, ensure_ascii=False, indent=2)}

## 요청 사항
다음 JSON 형식으로만 응답하세요. 코드 블록 없이 순수 JSON.

{{
  "insights": "거래 패턴 분석 및 주요 관찰사항 (한국어, 300자 이내)",
  "suggestions": [
    "운영자가 검토할 파라미터 조정 제안 1 (텍스트, 숫자 직접 수정 지시 금지)",
    "제안 2",
    "제안 3"
  ],
  "action_items": [
    "이번 주 즉시 실행할 개선사항 1",
    "실행할 개선사항 2"
  ]
}}

규칙:
- 파라미터 숫자 직접 수정 금지. "검토 권장" 또는 "테스트 권장"으로만 표현
- 데이터 부족(n<3)인 셋업은 결론 보류
- 비용이 음수 알파의 원인일 수 있는지 함께 분석
"""

    @staticmethod
    def _regime_stats_to_dict(by_regime: dict) -> dict[str, Any]:
        """ExpectancyAnalyzer.by_regime() 의 {regime: Stats} 를 직렬화 dict 로 변환한다.

        SPEC §8-4-2 의 _build_prompt 는 by_regime 을 json.dumps 하므로, Stats
        객체가 아닌 순수 dict 가 필요하다.

        Args:
            by_regime: {regime: Stats} 딕셔너리.
        Returns:
            {regime: {count, win_rate, avg_R, expectancy_R, total_pnl}} dict.
        """
        return {
            regime: {
                "count": s.trade_count,
                "win_rate": round(s.win_rate, 3),
                "avg_R": round(s.avg_R, 4),
                "expectancy_R": round(s.expectancy_R, 4),
                "total_pnl": round(s.total_pnl_usdt, 4),
            }
            for regime, s in by_regime.items()
        }

    def _get_cost_metrics(self, days: int) -> dict[str, Any]:
        """slippage_actual_pct, cost_guard_ev, GPT 비용 등의 메트릭을 집계한다.

        SPEC §8-4-2 1:1 + v3.1.2 정정 (UTC 윈도우).

        Args:
            days: 집계 기간 (일).
        Returns:
            비용 지표 dict. 조회 실패 시 {}.
        """
        # v3.1.2 정정: naive datetime.now() → UTC tz-aware
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    """SELECT
                         AVG(slippage_actual_pct), MAX(slippage_actual_pct),
                         AVG(cost_guard_ev),
                         COUNT(*) FROM trades WHERE timestamp >= ?""",
                    (since,),
                ).fetchone()
                gpt_cost = conn.execute(
                    """SELECT COALESCE(SUM(cost_usd), 0) FROM gpt_call_log
                       WHERE timestamp >= ?""",
                    (since,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 메트릭 조회 실패는 빈 dict 로 흡수
            logger.warning("[WeeklyAnalyst] cost 조회 실패: %s", e)
            return {}

        return {
            "avg_slippage_pct": round(row[0] or 0, 5),
            "max_slippage_pct": round(row[1] or 0, 5),
            "avg_cost_guard_ev": round(row[2] or 0, 4),
            "trade_count": row[3] or 0,
            "total_gpt_cost_usd": round(gpt_cost[0] or 0, 4),
        }

    def _save_report_file(self, report: WeeklyReport) -> str:
        """보고서를 JSON 파일로 저장한다 (보강 E).

        파일명은 weekly_YYYY-MM-DD.json. 같은 날 재실행 시 덮어쓰기가 발생하므로
        디버깅 시 운영자가 수동 백업해야 한다.

        Args:
            report: 저장할 WeeklyReport.
        Returns:
            저장된 파일 경로.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fname = os.path.join(self.report_dir, f"weekly_{date_str}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        logger.info("[WeeklyAnalyst] 보고서 파일 저장: %s", fname)
        return fname

    def _save_report_db(
        self, report: WeeklyReport, prompt: str, gpt: dict[str, Any]
    ) -> None:
        """보고서를 weekly_reports 에, GPT 호출 내역을 gpt_call_log 에 기록한다.

        보강 C: SPEC §8-4-2 는 weekly_reports 만 기록하나, GPT 호출 감사 추적을
        위해 gpt_call_log (§13-2) 도 함께 기록한다. 실패해도 예외를 전파하지 않는다.

        Args:
            report: 저장할 WeeklyReport.
            prompt: GPT 에 보낸 프롬프트 (해시·원문 기록용).
            gpt: _call_gpt 가 반환한 메타데이터 dict.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT INTO weekly_reports
                       (generated_at, period_days, report_json,
                        gpt_insights, gpt_cost_usd)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        report.generated_at,
                        report.period_days,
                        json.dumps(asdict(report), ensure_ascii=False),
                        report.gpt_insights,
                        report.gpt_call_cost_usd,
                    ),
                )
                # 보강 C: GPT 호출 감사 로그
                conn.execute(
                    """INSERT INTO gpt_call_log
                       (timestamp, caller, model, prompt_hash, prompt_text,
                        response_text, prompt_tokens, completion_tokens,
                        cost_usd, latency_ms, success, error_msg)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        report.generated_at,
                        "weekly",
                        self.model,
                        hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        prompt,
                        gpt["response_text"],
                        gpt["prompt_tokens"],
                        gpt["completion_tokens"],
                        gpt["cost_usd"],
                        gpt["latency_ms"],
                        1 if gpt["success"] else 0,
                        gpt["error_msg"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — DB 기록 실패는 로그만, 보고서는 반환
            logger.error("[WeeklyAnalyst] DB 저장 실패: %s", e)
