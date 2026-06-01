"""
monitoring/paper_log.py
=====================================================================
페이퍼(가상) 트레이딩 로그 — 가상 진입 기록 · SL/TP/time_stop 추적 · 집계.

★ 가상·손실 0. 어떤 실주문도 트리거하지 않음. 기존 trades DB 와 *분리된* SQLite 파일.
  신호는 5회 검증 무엣지 신호들 → 페이퍼도 무엣지 예상(정직). 미래 데이터·돈 0 로 재확인.
  net_R 비용 = 백테스트 post_only 와 동일: (maker + taker + slip)·notional (진입 maker·슬립0,
  청산 taker+슬립). risk_usdt = risk_pct·budget(= |entry−sl|/entry·notional 와 동치).
=====================================================================
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from config.settings import MONITORING_CONFIG, MonitoringConfig

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  side          TEXT NOT NULL,
  alert_grade   TEXT NOT NULL,        -- STRONG | WEAK
  entry_price   REAL NOT NULL,
  sl_price      REAL NOT NULL,
  tp_price      REAL NOT NULL,
  rr            REAL NOT NULL,
  atr           REAL NOT NULL,
  size_usdt     REAL NOT NULL,
  risk_usdt     REAL NOT NULL,
  snapshot      TEXT,
  status        TEXT NOT NULL,        -- OPEN | CLOSED
  exit_ts       TEXT,
  exit_price    REAL,
  exit_reason   TEXT,                 -- SL | TP | TIME_STOP
  gross_pnl_usd REAL,
  fees_usd      REAL,
  net_pnl_usd   REAL,
  net_r         REAL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_log (status);
CREATE INDEX IF NOT EXISTS idx_paper_symbol_status ON paper_log (symbol, status);
"""


@dataclass
class Levels:
    sl: float
    tp: float
    rr: float
    size_usdt: float
    risk_usdt: float


def compute_levels(side: str, entry: float, atr: float,
                   cfg: Optional[MonitoringConfig] = None) -> Optional[Levels]:
    """진입 손익구조: SL=entry∓sl_mult·ATR, TP=entry±tp_mult·ATR, 위험 risk_pct·budget."""
    cfg = cfg or MONITORING_CONFIG
    if entry <= 0 or atr <= 0:
        return None
    if side == "LONG":
        sl = entry - cfg.atr_sl_mult * atr
        tp = entry + cfg.atr_tp_mult * atr
    else:
        sl = entry + cfg.atr_sl_mult * atr
        tp = entry - cfg.atr_tp_mult * atr
    if sl <= 0 or tp <= 0:
        return None
    risk_usdt = cfg.risk_pct * cfg.budget_usdt
    stop_dist = abs(entry - sl)
    size_usdt = risk_usdt * entry / stop_dist if stop_dist > 0 else 0.0
    rr = cfg.atr_tp_mult / cfg.atr_sl_mult           # = |TP−entry| / |entry−SL|
    return Levels(sl, tp, rr, size_usdt, risk_usdt)


def resolve_virtual_exit(
    side: str, sl: float, tp: float, bars: Sequence[Tuple[float, float, float]],
    time_stop_bars: int,
) -> Optional[Tuple[float, str, int]]:
    """진입 이후 봉들(high,low,close)로 가상 청산 결정. 백테스트와 동일: 한 봉 내
    SL·TP 동시 충족 시 SL 우선(보수적), 그다음 time_stop. 미청산이면 None(아직 OPEN).
    """
    for i, (h, l, c) in enumerate(bars):
        held = i + 1
        if side == "LONG":
            if l <= sl:
                return sl, "SL", held
            if h >= tp:
                return tp, "TP", held
        else:
            if h >= sl:
                return sl, "SL", held
            if l <= tp:
                return tp, "TP", held
        if held >= time_stop_bars:
            return c, "TIME_STOP", held
    return None


def compute_net_r(side: str, entry: float, exit_price: float, sl: float,
                  size_usdt: float, cfg: Optional[MonitoringConfig] = None
                  ) -> Tuple[float, float, float, float]:
    """(gross_pnl, fees, net_pnl, net_R) — 백테스트 post_only 비용모델 일치."""
    cfg = cfg or MONITORING_CONFIG
    gross_pct = (exit_price - entry) / entry if side == "LONG" else (entry - exit_price) / entry
    gross_pnl = gross_pct * size_usdt
    fees = (cfg.maker_fee + cfg.taker_fee + cfg.slippage) * size_usdt
    net_pnl = gross_pnl - fees
    risk_usdt = abs(entry - sl) / entry * size_usdt
    net_r = net_pnl / risk_usdt if risk_usdt > 0 else 0.0
    return gross_pnl, fees, net_pnl, net_r


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def has_open(db_path: str, symbol: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM paper_log WHERE symbol=? AND status='OPEN' LIMIT 1", (symbol,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_signal(db_path: str, ts: str, symbol: str, side: str, grade: str,
                  entry: float, atr: float, snapshot: Optional[dict] = None,
                  cfg: Optional[MonitoringConfig] = None) -> Optional[int]:
    """가상 진입 기록(OPEN). 같은 코인 OPEN 존재 시 None(동시 1건만). 손익구조 산출 포함."""
    cfg = cfg or MONITORING_CONFIG
    lv = compute_levels(side, entry, atr, cfg)
    if lv is None or lv.size_usdt <= 0:
        return None
    if has_open(db_path, symbol):
        return None
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO paper_log (ts,symbol,side,alert_grade,entry_price,sl_price,"
            "tp_price,rr,atr,size_usdt,risk_usdt,snapshot,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN', ?)",
            (ts, symbol, side, grade, entry, lv.sl, lv.tp, lv.rr, atr, lv.size_usdt,
             lv.risk_usdt, json.dumps(snapshot or {}, ensure_ascii=False), ts),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def close_signal(db_path: str, log_id: int, exit_ts: str, exit_price: float,
                 exit_reason: str, cfg: Optional[MonitoringConfig] = None) -> None:
    """가상 청산 — net_R(비용 차감) 계산해 CLOSED 로 갱신."""
    cfg = cfg or MONITORING_CONFIG
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM paper_log WHERE id=?", (log_id,)).fetchone()
        if row is None or row["status"] == "CLOSED":
            return
        gross, fees, net, net_r = compute_net_r(
            row["side"], row["entry_price"], exit_price, row["sl_price"],
            row["size_usdt"], cfg)
        conn.execute(
            "UPDATE paper_log SET status='CLOSED', exit_ts=?, exit_price=?, exit_reason=?, "
            "gross_pnl_usd=?, fees_usd=?, net_pnl_usd=?, net_r=? WHERE id=?",
            (exit_ts, exit_price, exit_reason, gross, fees, net, net_r, log_id),
        )
        conn.commit()
    finally:
        conn.close()


def open_positions(db_path: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM paper_log WHERE status='OPEN'").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def aggregate(db_path: str) -> dict:
    """누적 집계 — 총/승률/평균net_R/gross_R/cost_R + STRONG/WEAK 분리. (CLOSED 기준)"""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM paper_log WHERE status='CLOSED'").fetchall()]
    finally:
        conn.close()

    def _agg(items: List[dict]) -> dict:
        n = len(items)
        if n == 0:
            return {"n": 0, "win_rate": 0.0, "avg_net_r": 0.0, "gross_r": 0.0, "cost_r": 0.0}
        wins = sum(1 for x in items if x["net_pnl_usd"] > 0)
        rk = [x for x in items if x["risk_usdt"] > 0]
        gross_r = sum(x["gross_pnl_usd"] / x["risk_usdt"] for x in rk) / len(rk) if rk else 0.0
        cost_r = sum(x["fees_usd"] / x["risk_usdt"] for x in rk) / len(rk) if rk else 0.0
        return {"n": n, "win_rate": wins / n,
                "avg_net_r": sum(x["net_r"] for x in items) / n,
                "gross_r": gross_r, "cost_r": cost_r}

    return {
        "all": _agg(rows),
        "STRONG": _agg([r for r in rows if r["alert_grade"] == "STRONG"]),
        "WEAK": _agg([r for r in rows if r["alert_grade"] == "WEAK"]),
        "n_open": len(open_positions(db_path)),
    }
