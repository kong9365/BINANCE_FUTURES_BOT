"""
mcp/binance_mcp.py
=====================================================================
BinanceMCPClient — shadow opt-in client (python-binance 와 결과 비교).

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.1.2 (Binance Futures MCP)
  - 운영자 결정 #v1-4: shadow opt-in (실거래 X, 검증만)
  - ENABLE_BINANCE_MCP_SHADOW env flag 로 ON/OFF

설계:
  - shadow 모드: python-binance 결과를 *정답*으로, MCP 응답을 *비교*
  - diff > 1% 시 audit_log + Telegram 알림
  - 본 모듈은 *연결 인터페이스*만 — 실제 MCP 호출은 외부 라이브러리 주입
=====================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPDiffResult:
    """python-binance vs MCP 비교 결과."""

    method: str
    matched: bool
    diff_pct: float = 0.0       # 응답 차이 비율 (숫자만)
    python_binance_response: Optional[dict] = None
    mcp_response: Optional[dict] = None
    error: Optional[str] = None


class BinanceMCPClient:
    """Binance MCP shadow client.

    사용 (shadow 모드):
        client = BinanceMCPClient(db_path="data/bot_live.db", mcp_call_fn=mcp_fn)
        diff = await client.compare_method(
            method="futures_account",
            python_binance_response={"totalWalletBalance": "1000.5"},
        )
        # diff.matched == True 면 정합, False 면 audit_log + 알림
    """

    SHADOW_ENV_FLAG = "ENABLE_BINANCE_MCP_SHADOW"
    DIFF_THRESHOLD_PCT = 1.0    # 1% 이상 diff 면 알림

    def __init__(
        self,
        db_path: str,
        mcp_call_fn: Optional[Callable[[str, dict], Awaitable[dict]]] = None,
    ) -> None:
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = db_path
        self.mcp_call_fn = mcp_call_fn

    @classmethod
    def is_shadow_enabled(cls) -> bool:
        """env flag 확인 (기본 OFF)."""
        raw = os.environ.get(cls.SHADOW_ENV_FLAG, "false")
        return raw.strip().lower() in ("1", "true", "yes", "on")

    async def compare_method(
        self,
        method: str,
        python_binance_response: dict,
        mcp_args: Optional[dict] = None,
    ) -> MCPDiffResult:
        """python-binance 응답과 MCP 응답 비교.

        Args:
            method: API 메서드 이름 (e.g. "futures_account").
            python_binance_response: 권위 응답 (정답).
            mcp_args: MCP 호출 인자.

        Returns:
            MCPDiffResult — matched=False 시 audit_log 적재.
        """
        if not self.is_shadow_enabled():
            return MCPDiffResult(
                method=method, matched=True,
                python_binance_response=python_binance_response,
                error="shadow disabled",
            )

        if self.mcp_call_fn is None:
            return MCPDiffResult(
                method=method, matched=False,
                python_binance_response=python_binance_response,
                error="mcp_call_fn 미주입",
            )

        try:
            mcp_response = await self.mcp_call_fn(method, mcp_args or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("[BinanceMCP] %s 호출 실패: %s", method, e)
            result = MCPDiffResult(
                method=method, matched=False,
                python_binance_response=python_binance_response,
                error=str(e),
            )
            self._log_diff(result)
            return result

        # 숫자 필드 비교 (단순 — futures_account 위주)
        diff_pct = self._compute_diff_pct(
            python_binance_response, mcp_response,
        )
        matched = diff_pct < self.DIFF_THRESHOLD_PCT
        result = MCPDiffResult(
            method=method,
            matched=matched,
            diff_pct=diff_pct,
            python_binance_response=python_binance_response,
            mcp_response=mcp_response,
        )
        if not matched:
            self._log_diff(result)
        return result

    @staticmethod
    def _compute_diff_pct(a: dict, b: dict) -> float:
        """주요 숫자 필드 평균 diff %."""
        if not isinstance(a, dict) or not isinstance(b, dict):
            return 100.0  # 다름
        common = set(a.keys()) & set(b.keys())
        diffs = []
        for k in common:
            va, vb = a[k], b[k]
            try:
                fa = float(va)
                fb = float(vb)
                if fa == 0 and fb == 0:
                    diffs.append(0.0)
                elif fa == 0 or fb == 0:
                    diffs.append(100.0)
                else:
                    diffs.append(abs(fa - fb) / abs(fa) * 100)
            except (TypeError, ValueError):
                # 숫자 아닌 필드 — 무시
                continue
        if not diffs:
            return 0.0
        return sum(diffs) / len(diffs)

    def _log_diff(self, result: MCPDiffResult) -> None:
        """mcp_diff_log 테이블 INSERT (감사)."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO mcp_diff_log
                        (ts, method, matched, diff_pct,
                         python_binance_response, mcp_response, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        result.method,
                        1 if result.matched else 0,
                        result.diff_pct,
                        json.dumps(result.python_binance_response or {}, sort_keys=True),
                        json.dumps(result.mcp_response or {}, sort_keys=True),
                        result.error,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            # 마이그레이션 v3.2.1 미적용 시
            logger.warning("[BinanceMCP] _log_diff 실패: %s", e)
