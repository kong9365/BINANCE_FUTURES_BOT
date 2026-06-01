"""
monitoring/runner.py
=====================================================================
실시간 관찰 스캐너 — 공개 keyless API 5분 주기. *읽기 전용·가상*.

★ executor·실주문·실키 0. 공개 엔드포인트만(Client() 키없음). 보호종목 제외.
  STRONG = Telegram *발신만*(상위 1~2, 점수순·쿨다운·최소간격·일일캡).
  WEAK+STRONG = 페이퍼 로그 기록(grade 태그). 매 스캔 open 포지션 가상 추적(SL/TP/time_stop).
  자동매매 아님 = 관찰 보고 + 가상 로그(6번째 검증). 어떤 실주문도 트리거하지 않는다.
=====================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from config.settings import MONITORING_CONFIG, MonitoringConfig, PAIR_WHITELIST_CONFIG
from monitoring import paper_log
from monitoring.alert import format_alert
from monitoring.observer import Bar, classify, compute_indicators, strength_score

logger = logging.getLogger(__name__)


def _protected() -> set:
    return set(PAIR_WHITELIST_CONFIG.protected_symbols)


def _snapshot(st) -> dict:
    return {"vol_ratio": st.vol_ratio, "taker_ratio": st.taker_ratio,
            "trend_dir": st.trend_dir, "oi_change_pct": st.oi_change_pct,
            "close": st.close, "atr": st.atr_val}


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _alert_allowed(symbol: str, now_iso: str, state: dict, cfg: MonitoringConfig) -> bool:
    """일일캡 + 코인 24h 쿨다운. (알림 최소간격은 스캔 단위로 scan_once 에서 적용)."""
    now = _parse(now_iso)
    if now is None:
        return False
    day = now.date().isoformat()
    if state.setdefault("today", {}).get(day, 0) >= cfg.alert_daily_max:
        return False
    last_sym = state.setdefault("per_symbol", {}).get(symbol)
    if last_sym is not None:
        ls = _parse(last_sym)
        if ls is not None and now - ls < timedelta(hours=cfg.alert_cooldown_h):
            return False
    return True


def _mark_alert(symbol: str, now_iso: str, state: dict) -> None:
    now = _parse(now_iso)
    day = now.date().isoformat()
    state.setdefault("today", {})[day] = state["today"].get(day, 0) + 1
    state.setdefault("per_symbol", {})[symbol] = now_iso
    state["last_ts"] = now_iso


def track_open(bars_by_symbol: Dict[str, List[Bar]], db_path: str,
               cfg: Optional[MonitoringConfig] = None) -> int:
    """open 가상 포지션을 최신 봉으로 추적 → SL/TP/time_stop 도달 시 가상청산. 청산 건수 반환."""
    cfg = cfg or MONITORING_CONFIG
    closed = 0
    for pos in paper_log.open_positions(db_path):
        bars = bars_by_symbol.get(pos["symbol"])
        if not bars:
            continue
        after = [(b.high, b.low, b.close) for b in bars if str(b.ts) > str(pos["ts"])]
        if not after:
            continue
        res = paper_log.resolve_virtual_exit(
            pos["side"], pos["sl_price"], pos["tp_price"], after, cfg.time_stop_bars)
        if res is not None:
            exit_price, reason, held = res
            exit_bar = bars[min(len(bars) - 1, len(bars) - len(after) + held - 1)]
            paper_log.close_signal(db_path, pos["id"], str(exit_bar.ts), exit_price, reason, cfg)
            closed += 1
    return closed


def scan_once(bars_by_symbol: Dict[str, List[Bar]],
              oi_by_symbol: Dict[str, Optional[Tuple[float, float]]],
              db_path: str, now_iso: str, alert_state: dict,
              cfg: Optional[MonitoringConfig] = None) -> List[str]:
    """한 스캔 사이클(순수+DB). 보호종목 제외 → 분류 → WEAK/STRONG 로그 → STRONG 상위 알림 →
    open 추적. *실주문 0*. 반환 = 발신할 알림 메시지 리스트(데몬이 Telegram 발신).
    """
    cfg = cfg or MONITORING_CONFIG
    prot = _protected()
    candidates = []                                          # (score, symbol, obs)
    for sym, bars in bars_by_symbol.items():
        if sym in prot:                                      # ★ 보호종목 제외
            continue
        oi = oi_by_symbol.get(sym)
        oi_now, oi_prev = oi if oi else (None, None)
        st = compute_indicators(bars, oi_now, oi_prev, cfg)
        if st is None:
            continue
        obs = classify(st, cfg)
        if obs.grade in ("STRONG", "WEAK"):
            paper_log.record_signal(db_path, now_iso, sym, obs.direction, obs.grade,
                                    st.close, st.atr_val, _snapshot(st), cfg)
            if obs.grade == "STRONG":
                candidates.append((strength_score(st, cfg), sym, obs))
    # 알림 최소간격: 직전 스캔의 마지막 알림 이후 min_interval 경과해야 이번 스캔 알림 허용
    # (같은 스캔의 상위 1~2 배치는 함께 발신 — 일일캡·코인쿨다운이 빈도 통제).
    can_alert = True
    last_any = alert_state.get("last_ts")
    if last_any is not None:
        la, now = _parse(last_any), _parse(now_iso)
        if la is not None and now is not None and now - la < timedelta(minutes=cfg.alert_min_interval_min):
            can_alert = False
    alerts = []
    if can_alert:
        for score, sym, obs in sorted(candidates, key=lambda x: -x[0]):
            if _alert_allowed(sym, now_iso, alert_state, cfg):
                msg = format_alert(obs, sym, now_iso, cfg)
                if msg:
                    alerts.append(msg)
                    _mark_alert(sym, now_iso, alert_state)
    track_open(bars_by_symbol, db_path, cfg)
    return alerts


# ── 라이브 fetch (공개 keyless) + 데몬 — 얇은 래퍼(단위테스트는 scan_once) ──
def fetch_bars(client, symbol: str, cfg: MonitoringConfig, limit: int = 250) -> List[Bar]:
    """공개 futures klines → 마감봉 Bar 리스트(현재 형성봉 제외)."""
    kl = client.futures_klines(symbol=symbol, interval=cfg.interval, limit=limit)
    bars = []
    for k in kl[:-1]:                                        # 마지막=형성 중 → 제외(룩어헤드0)
        ts = datetime.utcfromtimestamp(k[0] / 1000).isoformat()
        o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        taker = float(k[9])                                 # taker buy base volume
        bars.append(Bar(o, h, l, c, v, taker, ts))
    return bars


def fetch_oi(client, symbol: str, cfg: MonitoringConfig) -> Optional[Tuple[float, float]]:
    """공개 OI 통계 → (now, prev). 실패/미지원 시 None(과거 호환·3지표 분류)."""
    try:
        hist = client.futures_open_interest_hist(
            symbol=symbol, period="15m", limit=max(2, cfg.oi_lookback_min // 15 + 1))
        if len(hist) >= 2:
            return float(hist[-1]["sumOpenInterest"]), float(hist[0]["sumOpenInterest"])
    except Exception as e:  # noqa: BLE001
        logger.debug("[observer] OI fetch 실패 %s: %s", symbol, e)
    return None


def run(symbols: List[str], db_path: str, send_alert: Callable[[str], None],
        cfg: Optional[MonitoringConfig] = None, max_cycles: Optional[int] = None) -> None:
    """데몬 루프 — 공개 keyless Client 로 스캔. send_alert = Telegram 발신 콜백(발신만)."""
    import time

    from binance.client import Client          # 공개 엔드포인트(키 없음)
    cfg = cfg or MONITORING_CONFIG
    if not cfg.enabled:
        logger.info("[observer] MONITORING_ENABLED=off → 미실행")
        return
    client = Client()                          # ★ 키 없음(읽기 전용 공개 데이터)
    paper_log.init_db(db_path)
    alert_state: dict = {}
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        bars_by, oi_by = {}, {}
        for sym in symbols:
            try:
                bars_by[sym] = fetch_bars(client, sym, cfg)
                oi_by[sym] = fetch_oi(client, sym, cfg)
            except Exception as e:  # noqa: BLE001
                logger.warning("[observer] fetch 실패 %s: %s", sym, e)
        now_iso = datetime.utcnow().isoformat()
        for msg in scan_once(bars_by, oi_by, db_path, now_iso, alert_state, cfg):
            try:
                send_alert(msg)
            except Exception as e:  # noqa: BLE001
                logger.warning("[observer] 알림 발신 실패: %s", e)
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            time.sleep(cfg.scan_interval_s)
