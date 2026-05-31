"""
scripts/report_b3_effect.py
=====================================================================
B-3 효과 리포트 — 미체결(달아난 승자) vs 체결(되돌아온 패자) 방향 forward-return 비교.

★ 읽기 전용 + 공개 klines(체결측 fwd 재계산). 거래/계좌/주문/DB쓰기 0. 오프라인.
  B-3 효과 = mean(미체결 fwd) − mean(체결 fwd) (horizon별). 양(+)이면 역선택 확정.

사용: python scripts/report_b3_effect.py [--db data/bot.db] [--interval 15m]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analytics.probe_b3 import HORIZONS, b3_report  # noqa: E402


def _make_fetch_bars(client, interval: str):
    def fetch(symbol, start_ms):
        try:
            raw = client.futures_klines(
                symbol=symbol, interval=interval, startTime=start_ms, limit=10)
        except Exception:  # noqa: BLE001
            return []
        return [(int(k[0]), float(k[4])) for k in raw]
    return fetch


def _pct(x):
    return "n/a" if x is None else f"{x*100:+.3f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--interval", default="15m")
    args = ap.parse_args()
    from binance.client import Client
    client = Client()
    rep = b3_report(args.db, _make_fetch_bars(client, args.interval))

    fr = rep["fill_rate"]
    fr_str = "n/a" if fr is None else f"{fr*100:.1f}%"
    sl = rep["mean_slippage"]
    sl_str = "n/a" if sl is None else f"{sl:+.5f}"
    print("=" * 70)
    print("B-3 효과 리포트 (post_only 역선택) — 미체결 vs 체결 방향 forward-return")
    print("=" * 70)
    print(f"미체결 n={rep['n_unfilled']} / 체결 n={rep['n_filled']} / 체결률 {fr_str}")
    print(f"fill 슬리피지 평균(체결가−limit): {sl_str}")
    print(f"\n{'horizon':>8}{'미체결fwd':>12}{'체결fwd':>12}{'B-3효과(Δ)':>14}")
    for h in HORIZONS:
        print(f"{f'+{h}bar':>8}{_pct(rep['unfilled_fwd'][h]):>12}"
              f"{_pct(rep['filled_fwd'][h]):>12}{_pct(rep['b3_effect'][h]):>14}")
    print("\n해석: B-3효과(Δ) 양수 = 미체결(달아난 승자) > 체결(되돌아온 패자) = 역선택 확정.")
    print("⚠ 소표본이면 방향만 — 메커니즘 서명(부호)이 일관되면 증명. 수익성 주장 아님.")
    print("=" * 70)


if __name__ == "__main__":
    main()
