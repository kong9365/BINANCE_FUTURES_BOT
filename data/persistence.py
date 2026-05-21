"""
data/persistence.py
=====================================================================
영속 저장 어댑터 — Supabase(Postgres) **주저장** + 로컬 sqlite **폴백 큐**.

설계 (P0, docs/STRATEGY_REALISM_REVIEW.md §3):
  - 모든 운영 데이터(거래·시그널·자본·OI/펀딩/OHLCV·이벤트)를 Supabase 에 기록.
  - 네트워크/Supabase 장애 시 **거래 루프를 막지 않도록** 로컬 sqlite outbox 에
    적재(enqueue)하고 True 대신 False 반환. 재연결되면 flush_outbox() 로 순서대로
    재전송(at-least-once, 누적 테이블은 upsert 라 멱등).
  - SUPABASE_URL/KEY 미설정 시 **outbox 전용(degraded) 모드**로 동작(테스트/오프라인 개발).

보안(CLAUDE.md TIER 1):
  - service_role 키는 .env 에서만 읽고 코드/로그에 출력하지 않는다.
  - 봇은 service_role 키로 접속(RLS 우회). 키는 절대 커밋 금지.

사용:
  p = SupabasePersistence()                     # env 에서 클라이언트 생성
  p.log_event("INFO", "main", "봇 시작")
  p.save_oi("SOLUSDT", ts, 12345.6)             # upsert(멱등)
  테스트: SupabasePersistence(client=MagicMock(), outbox_path=tmp)
=====================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 누적 테이블의 upsert 충돌 키 (멱등 재전송). Supabase 스키마의 UNIQUE 제약과 일치.
_UPSERT_CONFLICT: Dict[str, str] = {
    "oi_history": "symbol,ts,period",
    "funding_history": "symbol,ts",
    "ohlcv": "symbol,interval,ts",
}

_DEFAULT_OUTBOX = os.path.join("data", "persistence_outbox.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(row: Dict[str, Any]) -> Dict[str, Any]:
    """datetime → ISO 문자열. 그 외 값은 그대로(jsonb/숫자/문자)."""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
        else:
            out[k] = v
    return out


class SupabasePersistence:
    """Supabase 주저장 + 로컬 sqlite outbox 폴백 어댑터.

    Args:
        client: 주입 Supabase 클라이언트(테스트는 mock). None 이면 env 로 생성 시도.
        url/key: 명시 자격(미지정 시 SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY).
        outbox_path: 로컬 폴백 sqlite 경로.
        auto_flush: 생성 시 클라이언트가 있으면 미전송분 1회 flush 시도.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        url: Optional[str] = None,
        key: Optional[str] = None,
        outbox_path: str = _DEFAULT_OUTBOX,
        auto_flush: bool = True,
    ) -> None:
        self._outbox_path = outbox_path
        self._lock = threading.Lock()
        self._init_outbox()
        self.client = client if client is not None else self._build_client(url, key)
        if self.client is None:
            logger.warning("[Persistence] Supabase 클라이언트 없음 → 로컬 outbox 전용 모드")
        elif auto_flush:
            try:
                self.flush_outbox()
            except Exception as e:  # noqa: BLE001 — flush 실패는 치명적이지 않음
                logger.warning("[Persistence] 초기 flush 실패(무시): %s", e)

    # ── 클라이언트 / outbox 초기화 ──────────────────────────────────────
    def _build_client(self, url: Optional[str], key: Optional[str]):
        url = url or os.environ.get("SUPABASE_URL")
        key = key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            logger.warning("[Persistence] SUPABASE_URL/SERVICE_ROLE_KEY 미설정")
            return None
        try:
            from supabase import create_client
            return create_client(url, key)
        except Exception as e:  # noqa: BLE001
            logger.error("[Persistence] 클라이언트 생성 실패 → outbox 전용: %s", e)
            return None

    def _connect(self) -> sqlite3.Connection:
        d = os.path.dirname(self._outbox_path)
        if d:
            os.makedirs(d, exist_ok=True)
        return sqlite3.connect(self._outbox_path)

    def _init_outbox(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_writes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    op          TEXT NOT NULL,      -- insert / upsert
                    table_name  TEXT NOT NULL,
                    payload     TEXT NOT NULL,      -- JSON
                    conflict    TEXT,              -- upsert 충돌 키
                    created_at  TEXT NOT NULL,
                    attempts    INTEGER DEFAULT 0,
                    last_error  TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # ── 공개 API ────────────────────────────────────────────────────────
    def insert(self, table: str, row: Dict[str, Any]) -> bool:
        """단건 insert. 성공 True, Supabase 실패 시 outbox 적재 후 False."""
        return self._write("insert", table, _normalize(row))

    def upsert(self, table: str, row: Dict[str, Any], on_conflict: Optional[str] = None) -> bool:
        """단건 upsert(멱등). on_conflict 미지정 시 누적 테이블 기본 키 사용."""
        conflict = on_conflict or _UPSERT_CONFLICT.get(table)
        return self._write("upsert", table, _normalize(row), conflict)

    # 편의 래퍼 (이후 단계에서 사용) ----------------------------------------
    def log_event(self, level: str, component: str, message: str,
                  context: Optional[Dict[str, Any]] = None) -> bool:
        return self.insert("app_events", {
            "ts": _now_iso(), "level": level, "component": component,
            "message": message, "context": context,
        })

    def record_signal(self, **fields: Any) -> bool:
        fields.setdefault("ts", _now_iso())
        return self.insert("signals", fields)

    def record_equity(self, **fields: Any) -> bool:
        fields.setdefault("ts", _now_iso())
        return self.insert("equity_snapshots", fields)

    def save_oi(self, symbol: str, ts: Any, open_interest: float, period: str = "1h") -> bool:
        return self.upsert("oi_history", {
            "symbol": symbol, "ts": ts, "open_interest": open_interest, "period": period,
        })

    def save_funding(self, symbol: str, ts: Any, funding_rate: float) -> bool:
        return self.upsert("funding_history", {
            "symbol": symbol, "ts": ts, "funding_rate": funding_rate,
        })

    def save_ohlcv(self, symbol: str, interval: str, ts: Any, o: float, h: float,
                   low: float, c: float, v: float, taker_buy_base: Optional[float] = None) -> bool:
        return self.upsert("ohlcv", {
            "symbol": symbol, "interval": interval, "ts": ts,
            "open": o, "high": h, "low": low, "close": c, "volume": v,
            "taker_buy_base": taker_buy_base,
        })

    # ── 내부 쓰기 / outbox ────────────────────────────────────────────────
    def _write(self, op: str, table: str, payload: Dict[str, Any],
               conflict: Optional[str] = None) -> bool:
        if self.client is not None:
            try:
                self._send(op, table, payload, conflict)
                return True
            except Exception as e:  # noqa: BLE001 — 실패 시 로컬 폴백
                logger.warning("[Persistence] %s %s 실패 → outbox 적재: %s", op, table, e)
                self._enqueue(op, table, payload, conflict, error=str(e))
                return False
        # degraded 모드: 클라이언트 없음 → 항상 outbox
        self._enqueue(op, table, payload, conflict, error="no client")
        return False

    def _send(self, op: str, table: str, payload: Dict[str, Any],
              conflict: Optional[str]) -> None:
        q = self.client.table(table)
        if op == "upsert":
            (q.upsert(payload, on_conflict=conflict) if conflict else q.upsert(payload)).execute()
        else:
            q.insert(payload).execute()

    def _enqueue(self, op: str, table: str, payload: Dict[str, Any],
                 conflict: Optional[str], error: Optional[str] = None) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO pending_writes (op, table_name, payload, conflict, created_at, last_error)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (op, table, json.dumps(payload, default=str), conflict, _now_iso(), error),
                )
                conn.commit()
            finally:
                conn.close()

    def flush_outbox(self, limit: int = 500) -> int:
        """미전송분을 순서대로 재전송. 첫 실패에서 멈춤(순서 보존·오프라인 추정).

        Returns: 이번에 전송 성공한 건수.
        """
        if self.client is None:
            return 0
        sent = 0
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, op, table_name, payload, conflict FROM pending_writes ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()
                for rid, op, table, payload, conflict in rows:
                    try:
                        self._send(op, table, json.loads(payload), conflict)
                        conn.execute("DELETE FROM pending_writes WHERE id = ?", (rid,))
                        conn.commit()
                        sent += 1
                    except Exception as e:  # noqa: BLE001 — 여전히 장애로 추정, 중단
                        conn.execute(
                            "UPDATE pending_writes SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                            (str(e), rid),
                        )
                        conn.commit()
                        logger.warning("[Persistence] flush 중단(재시도 보류): %s", e)
                        break
            finally:
                conn.close()
        if sent:
            logger.info("[Persistence] outbox flush: %d건 전송", sent)
        return sent

    def pending_count(self) -> int:
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0]
        finally:
            conn.close()
