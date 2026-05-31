"""
strategy/smc.py
=====================================================================
ICT/SMC 프리미티브 — *재량 0의 객관 규칙* (순수 함수, 룩어헤드 0).

배경: SMC(Smart Money Concepts)는 가장 과적합되기 쉽다. "그럴듯한 박스"를 눈으로
  고르는 재량을 0으로 만들기 위해 모든 프리미티브를 정확한 수식으로 정의한다.

설계 원칙 (strategy/breakout.py 와 동일):
  - 순수 함수: 입력은 마감봉 (o,h,l,c,v,ts) 리스트뿐. 외부상태·네트워크 없음.
  - **룩어헤드 0 (SMC 1번 함정)**: swing 은 좌우 N봉으로 *확정*되므로, 결정봉 t 에서는
    인덱스 i ≤ (len-1) - N 인 swing만 사용한다(우측 N봉이 닫혀야 확정). FVG/OB/sweep 도
    데이터 ≤ t 만 사용. "미래 봉 주입 시 신호 불변" 을 단위테스트로 강제(tests/test_smc.py).
  - 지표 ATR 은 strategy/breakout.atr(Wilder) 재사용.

부호/방향 규약: trend +1=강세 / −1=약세 / 0=미정. 숏은 롱의 거울상.

⚠ 라이브 카운터파트 없음(백테스트/연구 전용, HOLD). 1d 적용은 "일봉 구조 SMC" 이며
  인트라데이 ICT 와 다르고 B-3(post_only 15초)도 해소하지 않는다.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from strategy.breakout import atr  # Wilder ATR (순수함수 재사용)


@dataclass
class SMCConfig:
    """SMC 사전확정 파라미터 (스윕 금지 — 운영자 확정 단일값)."""

    swing_n: int = 2             # fractal 좌우 봉수 (5봉). i+N 이후에만 확정
    fvg_atr_mult: float = 0.25   # 최소 FVG 갭 ≥ 0.25·ATR14
    sweep_lookback: int = 3      # 직전 K봉 내 sweep
    poi_lookback: int = 10       # FVG/OB(point-of-interest) 신선도 윈도우(고정)
    atr_period: int = 14
    sl_atr_buffer: float = 0.1   # SL = sweep 극값 ∓ 0.1·ATR

    def min_bars(self) -> int:
        # ATR(period+1) + swing 확정(2N+1) + POI 윈도우. 실측 백테스트는 12mo 워밍업이라
        # 이 값은 비구속 — 진짜 하한만(임의 cushion 없음).
        return max(self.atr_period + 2, 2 * self.swing_n + 3, self.poi_lookback + 3)


@dataclass
class SMCSignal:
    """SMC 진입 신호 1건. 진입가/사이징은 엔진 책임."""

    action: str            # "LONG" | "SHORT"
    atr: float
    sweep_extreme: float   # sweep 저점(LONG)/고점(SHORT) → SL 기준
    tp_level: float        # 활성 범위 반대측 유동성(LONG=직전 SH / SHORT=직전 SL)


# ─────────────────────────────────────────────────────
# 프리미티브 (순수 함수)
# ─────────────────────────────────────────────────────
def is_swing_high(highs: Sequence[float], i: int, n: int) -> bool:
    """bar i 가 fractal swing high (좌우 n봉 모두보다 strict 높음). [i-n, i+n] 만 참조."""
    if i - n < 0 or i + n >= len(highs):
        return False
    hi = highs[i]
    for k in range(1, n + 1):
        if not (hi > highs[i - k] and hi > highs[i + k]):
            return False
    return True


def is_swing_low(lows: Sequence[float], i: int, n: int) -> bool:
    if i - n < 0 or i + n >= len(lows):
        return False
    lo = lows[i]
    for k in range(1, n + 1):
        if not (lo < lows[i - k] and lo < lows[i + k]):
            return False
    return True


def find_swings(highs: Sequence[float], lows: Sequence[float], n: int
                ) -> List[Tuple[int, str, float]]:
    """확정 가능한 swing 전체 → [(index, 'H'|'L', price)] (index 오름차순).

    index i ∈ [n, len-1-n] 만(우측 n봉 존재 = 확정). 따라서 배열 prefix-안정:
    미래 봉을 덧붙여도 기존 확정 swing 집합은 불변(룩어헤드 0).
    """
    out: List[Tuple[int, str, float]] = []
    m = len(highs)
    for i in range(n, m - n):
        if is_swing_high(highs, i, n):
            out.append((i, "H", highs[i]))
        elif is_swing_low(lows, i, n):
            out.append((i, "L", lows[i]))
    return out


def analyze_structure(highs: Sequence[float], lows: Sequence[float],
                      closes: Sequence[float], n: int) -> dict:
    """BOS/CHOCH 구조 상태기계 (종가 돌파 기준, 룩어헤드 0).

    각 bar j 에서 *그 시점 확정* swing(인덱스 ≤ j-n)만 사용. close[j] 가 직전 확정
    swing high 를 넘으면 BOS_up(추세 유지) 또는 CHOCH_up(추세 전환, 첫 반대돌파),
    하단은 대칭. 돌파된 swing 은 소진 → 다음 확정 swing 이 재장전.

    반환: trend(+1/−1/0), last_sh/last_sl(최근 확정 swing 가격), events[(j,type,level)].
    """
    swings = find_swings(highs, lows, n)
    by_confirm: dict = {}
    for (i, kind, price) in swings:
        by_confirm.setdefault(i + n, []).append((kind, price))  # bar i+n 에 확정

    trend = 0
    active_high: Optional[float] = None   # 돌파 대상 저항(미돌파 최근 확정 SH)
    active_low: Optional[float] = None
    last_sh: Optional[float] = None
    last_sl: Optional[float] = None
    events: List[Tuple[int, str, float]] = []

    for j in range(len(closes)):
        for (kind, price) in by_confirm.get(j, []):
            if kind == "H":
                active_high, last_sh = price, price
            else:
                active_low, last_sl = price, price
        c = closes[j]
        if active_high is not None and c > active_high:
            events.append((j, "BOS_up" if trend == 1 else "CHOCH_up", active_high))
            trend, active_high = 1, None       # 소진 → 다음 확정 SH 재장전
        elif active_low is not None and c < active_low:
            events.append((j, "BOS_down" if trend == -1 else "CHOCH_down", active_low))
            trend, active_low = -1, None
    return {"trend": trend, "last_sh": last_sh, "last_sl": last_sl, "events": events}


def premium_discount(price: float, sl: float, sh: float) -> str:
    """활성 범위 [sl, sh] 의 50% 기준. (셋업 B 미사용 — 계산 전용.)"""
    if sh <= sl:
        return "mid"
    eq = (sh + sl) / 2.0
    return "discount" if price <= eq else "premium"


def _recent_sweep(highs, lows, closes, n, side, lookback):
    """직전 lookback 봉 내 liquidity sweep. bull=매도측(저점 wick 돌파 후 종가 복귀).

    bull: low[j] < (확정 SL 가격, 인덱스 ≤ j-n) AND close[j] > 그 SL. bear 대칭.
    반환: 가장 최근 sweep {'j','extreme'} 또는 None.
    """
    swings = find_swings(highs, lows, n)
    L = len(closes)
    start = max(0, L - lookback)
    found = None
    for j in range(start, L):
        # bar j 시점 확정 swing(인덱스 ≤ j-n) 중 방향에 맞는 가장 최근 것
        ref = None
        for (i, kind, price) in swings:
            if i > j - n:
                break
            if (side == "bull" and kind == "L") or (side == "bear" and kind == "H"):
                ref = price
        if ref is None:
            continue
        if side == "bull" and lows[j] < ref and closes[j] > ref:
            found = {"j": j, "extreme": lows[j]}
        elif side == "bear" and highs[j] > ref and closes[j] < ref:
            found = {"j": j, "extreme": highs[j]}
    return found


def _poi_zone(opens, highs, lows, closes, structure, a, cfg, side):
    """결정봉 t 가 복귀(retrace)한 bull/bear FVG 또는 OB zone → (zlo, zhi) 또는 None.

    FVG(bull): low[i] > high[i-2], gap ≥ mult·ATR, i ∈ 직전 poi_lookback, i<t.
    OB(bull): 직전 bullish BOS/CHOCH 직전 마지막 음봉 zone=[low,high]. bear 대칭.
    복귀: 결정봉 t 의 low ≤ zhi AND close ≥ zlo (zone 진입·하단 유지). FVG 우선, 없으면 OB.
    """
    L = len(closes)
    t = L - 1
    candidates: List[Tuple[float, float]] = []

    # FVG — 직전 poi_lookback 내 가장 최근(결정봉 이전 i<t 형성)
    fvg: Optional[Tuple[float, float]] = None
    for i in range(max(2, L - cfg.poi_lookback), L - 1):
        if side == "bull" and lows[i] > highs[i - 2]:
            if lows[i] - highs[i - 2] >= cfg.fvg_atr_mult * a:
                fvg = (highs[i - 2], lows[i])
        elif side == "bear" and highs[i] < lows[i - 2]:
            if lows[i - 2] - highs[i] >= cfg.fvg_atr_mult * a:
                fvg = (highs[i], lows[i - 2])
    if fvg is not None:
        candidates.append(fvg)

    # OB — 직전 동방향 구조이벤트 직전 마지막 반대색 캔들
    ev_type = "up" if side == "bull" else "down"
    ev_idx = None
    for (j, etype, _lvl) in structure["events"]:
        if etype.endswith(ev_type):
            ev_idx = j
    if ev_idx is not None:
        want_down = (side == "bull")          # bull OB = 마지막 음봉
        for k in range(ev_idx - 1, max(-1, ev_idx - 1 - cfg.poi_lookback), -1):
            if k < 0:
                break
            if (closes[k] < opens[k]) == want_down:
                candidates.append((lows[k], highs[k]))
                break

    for (zlo, zhi) in candidates:             # 복귀하는 첫 zone 반환
        if lows[t] <= zhi and closes[t] >= zlo:
            return (zlo, zhi)
    return None


def evaluate_smc(closed: List[tuple], cfg: Optional[SMCConfig] = None
                 ) -> Optional[SMCSignal]:
    """셋업 B(3중 코어): trend==방향 + 직전 K봉 sweep + FVG/OB zone 복귀 → 신호.

    closed: (o,h,l,c,v,ts) 오름차순, 모두 평가시점 ts '미만' 마감봉. 결정봉 = closed[-1].
    discount 는 미사용(B). 신호 없으면 None.
    """
    cfg = cfg or SMCConfig()
    if len(closed) < cfg.min_bars():
        return None
    opens = [c[0] for c in closed]
    highs = [c[1] for c in closed]
    lows = [c[2] for c in closed]
    closes = [c[3] for c in closed]

    a = atr(highs, lows, closes, cfg.atr_period)
    if a <= 0:
        return None
    st = analyze_structure(highs, lows, closes, cfg.swing_n)
    trend = st["trend"]
    if trend == 0:
        return None

    if trend == 1:
        side, action, tp_level = "bull", "LONG", st["last_sh"]
    else:
        side, action, tp_level = "bear", "SHORT", st["last_sl"]
    if tp_level is None:
        return None

    sweep = _recent_sweep(highs, lows, closes, cfg.swing_n, side, cfg.sweep_lookback)
    if sweep is None:
        return None
    zone = _poi_zone(opens, highs, lows, closes, st, a, cfg, side)
    if zone is None:
        return None

    return SMCSignal(action=action, atr=a, sweep_extreme=sweep["extreme"], tp_level=tp_level)
