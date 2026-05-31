"""
analytics/unfilled_signal_analyzer.py
=====================================================================
Track A [A1] — B-3(post_only 역선택) 통계 계측기 (순수).

질문: 체결된 post_only 주문이 미체결 주문보다 *통계적으로* 우월한가(역선택)?
  → 체결(되돌아온 패자) vs 미체결(달아난 승자)의 방향 forward-return 비교 +
     Welch t-test(p-value)·95% CI. 차이 유의(+) = B-3 확정 / 무 = B-3 폐기.

★ B-3 는 OHLCV·백테스트로 측정 불가 — *실제 post_only 주문* 필수(라이브/테스트넷).
  이 모듈은 순수 통계 + forward-return 계산. 가격 소스는 주입(테스트·네트워크 격리).
  probe_b3.py(봉 기반 DB fwd 컬럼 backfill)와 구별되는 *초단위·유의성* 계측기.

horizon: +15s / +30s / +60s / +5m / +15m (초단위 = B-3 의 인트라데이 본질).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats

HORIZONS_SEC: Tuple[int, ...] = (15, 30, 60, 300, 900)


def forward_return_at(
    prices: Sequence[Tuple[int, float]], order_time_ms: int, ref_price: Optional[float],
    side: str, horizon_sec: int,
) -> Optional[float]:
    """주문시각+horizon 시점(이상 첫 가격) 기준 *방향* forward-return.

    prices = [(ts_ms, price)] 오름차순(aggTrades/1m klines 등). LONG=+(상승 유리)/SHORT 반전.
    ref_price = 요청 limit(=신호가). 데이터 없으면 None.
    """
    if ref_price is None or ref_price <= 0 or not prices or order_time_ms is None:
        return None
    target = order_time_ms + horizon_sec * 1000
    px = None
    for ts, p in prices:
        if ts >= target:
            px = p
            break
    if px is None:
        return None
    sign = 1.0 if side == "LONG" else -1.0
    return sign * (px - ref_price) / ref_price


def compare(filled: List[Optional[float]], unfilled: List[Optional[float]]) -> dict:
    """filled vs unfilled forward-return 배열 비교(한 horizon).

    반환: sample_size·avg·median(양쪽) + difference(unfilled−filled) + Welch t_stat·p_value
    + 95% CI(차이). n<2 인 쪽이 있으면 통계량 None.
    """
    f = np.array([x for x in filled if x is not None], dtype=float)
    u = np.array([x for x in unfilled if x is not None], dtype=float)
    res = {
        "n_filled": int(len(f)), "n_unfilled": int(len(u)),
        "avg_filled": float(np.mean(f)) if len(f) else None,
        "avg_unfilled": float(np.mean(u)) if len(u) else None,
        "median_filled": float(np.median(f)) if len(f) else None,
        "median_unfilled": float(np.median(u)) if len(u) else None,
        "difference": None, "t_stat": None, "p_value": None,
        "ci_low": None, "ci_high": None,
    }
    if len(f) < 2 or len(u) < 2:
        return res
    diff = float(np.mean(u) - np.mean(f))            # B-3: unfilled(승자) − filled(패자)
    res["difference"] = diff
    vu = np.var(u, ddof=1) / len(u)
    vf = np.var(f, ddof=1) / len(f)
    se = float(np.sqrt(vu + vf))
    if se == 0:                                       # 분산 0(동일값) → t-test 불가, 차이만 보고
        return res
    t_stat, p_value = stats.ttest_ind(u, f, equal_var=False)   # Welch (이분산)
    denom = (vu ** 2 / (len(u) - 1) + vf ** 2 / (len(f) - 1))
    dof = ((vu + vf) ** 2 / denom) if denom > 0 else 1.0
    tcrit = float(stats.t.ppf(0.975, dof)) if dof > 0 else 0.0
    res.update({
        "t_stat": float(t_stat), "p_value": float(p_value),
        "ci_low": diff - tcrit * se, "ci_high": diff + tcrit * se,
    })
    return res


def analyze_horizons(events: List[dict], horizons=HORIZONS_SEC) -> dict:
    """events 리스트 → horizon별 filled vs unfilled 비교.

    각 event: {"filled": bool, "fwd": {horizon_sec: return|None}}.
    (forward-return 은 호출자가 forward_return_at 으로 미리 계산해 채운다.)
    반환: {horizon_sec: compare(...)}.
    """
    out = {}
    for h in horizons:
        f = [e["fwd"].get(h) for e in events if e.get("filled")]
        u = [e["fwd"].get(h) for e in events if not e.get("filled")]
        out[h] = compare(f, u)
    return out


def b3_verdict(horizon_results: dict, alpha: float = 0.05) -> str:
    """B-3 판정: 어느 horizon 이든 유의(p<alpha) & 방향(diff>0, 미체결>체결) = PASS.
    유의 차이 전무 = FAIL. 표본 부족(통계량 None 만) = INSUFFICIENT_SAMPLE.
    """
    have_stat = False
    for r in horizon_results.values():
        if r.get("p_value") is None:
            continue
        have_stat = True
        if r["p_value"] < alpha and r["difference"] is not None and r["difference"] > 0:
            return "PASS"           # 미체결 > 체결 유의 = 역선택 확정
    return "FAIL" if have_stat else "INSUFFICIENT_SAMPLE"


def measure(events: List[dict], fetch_prices, horizons=HORIZONS_SEC) -> Tuple[dict, str]:
    """events → forward-return 계산 → horizon별 비교 → (horizon_results, verdict).

    events: [{"symbol", "side", "order_time_ms", "ref_price", "filled"}].
    fetch_prices(symbol, start_ms, end_ms) -> [(ts_ms, price)] (주입 — 네트워크 격리).
    """
    enriched = []
    max_h = max(horizons) if horizons else 0
    for e in events:
        ot = e["order_time_ms"]
        prices = fetch_prices(e["symbol"], ot, ot + max_h * 1000 + 5000)
        fwd = {h: forward_return_at(prices, ot, e["ref_price"], e["side"], h) for h in horizons}
        enriched.append({"filled": bool(e["filled"]), "fwd": fwd})
    hr = analyze_horizons(enriched, horizons)
    return hr, b3_verdict(hr)
