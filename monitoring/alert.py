"""
monitoring/alert.py
=====================================================================
관찰 알림 메시지 포맷터 — 초보자가 *판단*하도록 3블록. *추천 아님 = 관찰 보고*.

★ 손익구조(R:R·SL·TP)는 측정 가능·룩어헤드0 → 정직한 정보.
  *방향 적중 확률은 절대 제시하지 않는다*(가짜확률 금지). "확률/적중률" 문구 자체를 안 쓴다.
  "검증된 엣지 없음" 경고를 항상 포함한다.
=====================================================================
"""

from __future__ import annotations

from typing import Optional

from config.settings import MONITORING_CONFIG, MonitoringConfig
from monitoring.observer import Observation
from monitoring.paper_log import compute_levels

EDGE_WARNING = "⚠️ 검증된 엣지 없음. 정보일 뿐. 진입·판단·책임은 본인."


def format_alert(obs: Observation, symbol: str, ts: Optional[str] = None,
                 cfg: Optional[MonitoringConfig] = None) -> Optional[str]:
    """STRONG/WEAK 관찰을 3블록 메시지로. NONE 이거나 손익구조 산출 불가면 None.

    [1] 무슨 일이(4지표 상태) / [2] 만약 진입한다면(손익구조) / [3] 해석 & 경고.
    """
    cfg = cfg or MONITORING_CONFIG
    if obs.grade == "NONE" or obs.direction is None:
        return None
    st = obs.state
    side = obs.direction
    entry = st.close
    lv = compute_levels(side, entry, st.atr_val, cfg)
    if lv is None:
        return None
    sl_pct = (lv.sl - entry) / entry * 100.0
    tp_pct = (lv.tp - entry) / entry * 100.0
    vol = st.vol_ratio if st.vol_ratio is not None else 0.0
    taker = (st.taker_ratio or 0.0) * 100.0
    head = "LONG(상승 정렬)" if side == "LONG" else "SHORT(하락 정렬)"

    L = []
    L.append(f"📊 [{symbol}] 관찰 보고 · 등급 {obs.grade} · {head}")
    L.append("")
    L.append("【1. 무슨 일이?】 4지표 정렬 상태")
    L.append(f"  • 거래량: 평소의 {vol:.1f}배 (실체 동반 정도)")
    L.append(f"  • 추세: {side} (종가가 200EMA·Donchian20 {'위' if side == 'LONG' else '아래'})")
    L.append(f"  • taker 매수비율: {taker:.0f}% (매수 공격성)")
    if st.oi_change_pct is not None:
        L.append(f"  • OI 변화: {st.oi_change_pct:+.1f}% (포지션 활발도)")
    else:
        L.append("  • OI: 실시간 미확인")
    L.append("")
    L.append("【2. 만약 진입한다면 — 손익구조】 (가상, 예산 기준)")
    L.append(f"  진입예상 {entry:.6g}")
    L.append(f"  SL {lv.sl:.6g} ({sl_pct:+.2f}%)  /  TP {lv.tp:.6g} ({tp_pct:+.2f}%)")
    L.append(f"  R:R {lv.rr:.1f} : 1  (이기면 지는 것보다 {lv.rr:.1f}배 — 손익구조 정보)")
    L.append(f"  위험 {cfg.risk_pct * 100:.1f}% (${lv.risk_usdt:.2f}) → 수량 약 ${lv.size_usdt:,.0f} 상당")
    L.append("")
    L.append("【3. 해석 & 경고】")
    L.append("  • 4지표 같은 방향 정렬 = 지금의 '분위기'일 뿐, 방향 보장이 아닙니다.")
    L.append("  • 손익구조(R:R·SL·TP)는 측정값이지만, *방향이 맞을지는 검증된 엣지가 없습니다*.")
    L.append(f"  {EDGE_WARNING}")
    return "\n".join(L)
