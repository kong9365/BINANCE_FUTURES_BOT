"""
scripts/backfill_carry_data.py
=====================================================================
Phase D-1 — 델타중립 캐리 *오프라인 데이터* 백필 + 품질 리포트.

목적: spot OHLCV + funding history (+ perp OHLCV는 기존 보유) 를 확보해
"펀딩 수취 > 총비용 + basis 손실" net-EV 를 *오프라인* 검정할 수 있는지 확인.

★ 공개(public) 시장데이터만 — 키 없는 binance Client() 로 get_klines(spot) /
   futures_klines(perp) / futures_funding_rate(funding). **계좌 API·spot balance·
   주문 일절 없음.** forbid_spot_access(계좌 spot) 와 무충돌(공개 가격데이터일 뿐).
   executor/capital_manager 미수정. 실거래/testnet 주문 0.

저장: backtests/cache/carry/{sym}_spot_1d.csv, {sym}_funding.csv
   (backtests/cache/ = git 커밋 금지 경로 — 데이터는 커밋 안 함, 스크립트만).

사용: python scripts/backfill_carry_data.py [--max-syms N] [--start 2023-01-01]
       [--report-only]   # 수집 없이 기존 carry 캐시로 품질만
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CARRY_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "carry"
PERP_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "verification"
PROTECTED = {"BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"}


def _ms(dt: str) -> int:
    return int(datetime.strptime(dt, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _fetch_spot_klines(client, symbol, interval, start_ms, pause=0.2, max_pages=200):
    """공개 get_klines(spot) 순방향 페이지네이션 → [(ts_iso, o,h,l,c,v)]."""
    rows, cur, pages, seen = [], start_ms, 0, set()
    now = int(time.time() * 1000)
    while cur < now and pages < max_pages:
        raw = client.get_klines(symbol=symbol, interval=interval, startTime=cur, limit=1000)
        if not raw:
            break
        for k in raw:
            ot = int(k[0])
            if ot in seen:
                continue
            seen.add(ot)
            ts = datetime.fromtimestamp(ot / 1000.0, tz=timezone.utc).isoformat()
            rows.append(dict(ts=ts, open=float(k[1]), high=float(k[2]),
                             low=float(k[3]), close=float(k[4]), volume=float(k[5])))
        nxt = int(raw[-1][0]) + 1
        if nxt <= cur:
            break
        cur, pages = nxt, pages + 1
        time.sleep(pause)
    return rows


def _collect(symbols, interval, start_ms):
    from binance.client import Client  # 공개 엔드포인트만 (키 없음)
    from backtesting.backfill_history import fetch_funding_history
    client = Client()
    CARRY_DIR.mkdir(parents=True, exist_ok=True)
    got = {}
    for sym in symbols:
        try:
            sp = _fetch_spot_klines(client, sym, interval, start_ms)
        except Exception as e:  # noqa: BLE001 — 심볼별 실패는 스킵(공개 데이터)
            print(f"  [spot 실패] {sym}: {type(e).__name__} {str(e)[:80]}")
            sp = []
        try:
            fr = fetch_funding_history(client, sym, start_ms) if sp else []
        except Exception as e:  # noqa: BLE001
            print(f"  [funding 실패] {sym}: {type(e).__name__} {str(e)[:80]}")
            fr = []
        if sp:
            pd.DataFrame(sp).to_csv(CARRY_DIR / f"{sym}_spot_{interval}.csv", index=False)
        if fr:
            pd.DataFrame(fr).to_csv(CARRY_DIR / f"{sym}_funding.csv", index=False)
        got[sym] = (len(sp), len(fr))
        print(f"  {sym}: spot={len(sp)} funding={len(fr)}")
        time.sleep(0.3)
    return got


def _load_perp_1d(sym):
    p = PERP_DIR / f"{sym}_1d.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    ts = d["ts"]
    idx = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts)
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    d = d.copy(); d.index = idx.normalize()
    return d


def _quality(symbols, interval):
    print("\n" + "=" * 78)
    print("Phase D-1 데이터 품질 리포트 (perp / spot / funding 시간정합·basis·결손)")
    print("=" * 78)
    print(f"{'symbol':12}{'perp':>7}{'spot':>7}{'fund':>7}{'align日':>8}{'spotMiss%':>10}{'basis_med%':>11}{'fundIvl(h)':>11}")
    usable = []
    for sym in symbols:
        perp = _load_perp_1d(sym)
        sp_f = CARRY_DIR / f"{sym}_spot_{interval}.csv"
        fr_f = CARRY_DIR / f"{sym}_funding.csv"
        spot = pd.read_csv(sp_f) if sp_f.exists() else None
        fund = pd.read_csv(fr_f) if fr_f.exists() else None
        if perp is None or spot is None or len(spot) == 0:
            print(f"{sym:12}{'(perp 없음' if perp is None else '(spot 없음'})")
            continue
        spot["d"] = pd.to_datetime(spot["ts"], format="ISO8601").dt.tz_localize(None).dt.normalize()
        spot = spot.set_index("d")
        # 시간정합 — perp ∩ spot 일자
        common = perp.index.intersection(spot.index)
        n_align = len(common)
        # spot 결손율 = perp 일자 중 spot 없는 비율
        spot_miss = 100.0 * (1 - n_align / len(perp)) if len(perp) else 100.0
        # basis = (perp_close - spot_close)/spot_close, 중앙값(%)
        if n_align:
            pc = perp.loc[common, "close"].astype(float)
            sc = spot.loc[common, "close"].astype(float)
            basis_med = float(((pc - sc) / sc * 100).median())
        else:
            basis_med = float("nan")
        # funding 간격(h)
        fund_ivl = float("nan")
        if fund is not None and len(fund) > 2:
            ft = pd.to_datetime(fund["ts"], format="ISO8601").sort_values()
            fund_ivl = ft.diff().dt.total_seconds().median() / 3600.0
        nf = 0 if fund is None else len(fund)
        print(f"{sym:12}{len(perp):>7}{len(spot):>7}{nf:>7}{n_align:>8}"
              f"{spot_miss:>9.1f}%{basis_med:>10.3f}%{fund_ivl:>11.1f}")
        if n_align >= 300 and spot_miss < 10 and nf > 100:
            usable.append(sym)
    return usable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=8)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    perp_syms = [os.path.basename(p).replace("_1d.parquet", "")
                 for p in sorted(glob.glob(str(PERP_DIR / "*_1d.parquet")))]
    # 샘플: spot 페어가 존재하는 유동 종목 우선(알파벳 앞쪽은 1000x·신규 perp-only 라 spot 없음).
    # 보호종목 BTC/ETH 는 *데이터*만(거래 유니버스 제외). mid-liq 포함해 대표성 확보.
    _liquid = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
               "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "TRXUSDT", "BCHUSDT"]
    sample = [s for s in _liquid if s in perp_syms][:args.max_syms]
    if not sample:
        sample = perp_syms[:args.max_syms]

    print(f"Phase D-1 — 공개 시장데이터 백필 (키 없음, 계좌·주문 0). 샘플 {len(sample)}종목, 1d, {args.start}~")
    if not args.report_only:
        _collect(sample, "1d", _ms(args.start))
    usable = _quality(sample, "1d")
    print("\n" + "=" * 78)
    print(f"최소 백테스트 가능(정합日≥300·spot결손<10%·funding>100) 심볼: {len(usable)} / {len(sample)}")
    print(f"  → {usable}")
    print(f"보호종목(거래 유니버스 제외, 데이터만): {[s for s in sample if s in PROTECTED]}")
    print("=" * 78)


if __name__ == "__main__":
    main()
