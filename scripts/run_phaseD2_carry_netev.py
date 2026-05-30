"""
scripts/run_phaseD2_carry_netev.py
=====================================================================
Phase D-2 — 델타중립 캐리 *오프라인* net-EV 검정 (baseline, threshold 없음).

핵심 질문: spot long + perp short 에서
   funding income + basis PnL − spot/futures fee − slippage − rebalance cost > 0
   이 OOS 에서 성립하는가?

★ 범위(엄수): 공개 시장데이터 파일 + 오프라인 계산만. **실거래/testnet/spot 주문/
  spot balance/계좌 API 0. executor·capital_manager·forbid_spot_access 미수정.**
  데이터는 커밋 금지 경로(backtests/cache/). 본 결과는 *오프라인 검정*이며 **라이브
  spot leg 구현 가능 판정이 아니다**(델타중립 라이브는 TIER-1 spot 격리와 충돌 — C).

baseline (사전확정, 무최적화·무스윕):
  - 매일 모든 eligible symbol 에 동일 notional N 으로 spot long + perp short(코인 매칭).
  - 일 단위 *동일-notional 복원* rebalance(turnover=N×|일수익률|, 양다리). ※ 보수적 상한:
    코인매칭 델타중립은 실제론 N×|Δbasis|(≈수십× 적음)만 rebalance 하면 되나, 운영자
    명세("매일 동일 notional")의 보수적 해석을 채택. rebalance cost 를 분해 항목으로 분리.
  - 미래 funding 미참조(항상 보유 → 진입판단 없음 → 룩어헤드 0).
  - 동일 가중(그 날 가용한 심볼 평균). 보호종목(BTC/ETH)은 데이터 벤치마크일 뿐 거래 유니버스 제외.

OOS: 파라미터가 *전혀 없으므로*(threshold·가중 모두 고정) in-sample 적합이 불가능 →
  공통 분석 전구간이 구조적으로 OOS. 워밍업 불필요.

사전확정 게이트 (구현 후 변경 금지):
  데이터: eligible≥20 / 공통기간≥2년 / funding 결손보정 명시 / 시간정합 가능
  성과:   net return>0(전비용) / Sharpe≥1.0 / MDD<15% / 월수익+비율≥55% /
          funding income>총비용 / 단일심볼<순이익30%(초과시 경고) / gross>0인데 net<0 = 실패
  판정:   eligible<20 or 기간<2년=판정불가 / funding≤총비용=실패 / net<0=실패 /
          단일심볼·단일구간 집중=통과보류 / 충족=오프라인 후보통과(라이브 판정 아님)

사용: python scripts/run_phaseD2_carry_netev.py [--max-syms N]
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtesting.carry_model import CarryCosts, basis_pnl, funding_income  # noqa: E402

CARRY_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "carry"
PERP_DIR = Path(PROJECT_ROOT) / "backtests" / "cache" / "verification"
PROTECTED = {"BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT"}
N = 10_000.0   # 심볼당 고정 notional (USD) — 분해 회계용 스케일

GATE_MIN_SYMS, GATE_MIN_YEARS = 20, 2.0
GATE_SHARPE, GATE_MDD, GATE_POS_MONTHS = 1.0, 15.0, 0.55
ALIGN_MIN, MISS_MAX, FUND_MIN = 300, 10.0, 100   # eligibility (= 품질 리포트와 동일)


def _perp_close(sym):
    p = PERP_DIR / f"{sym}_1d.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    ts = d["ts"]
    idx = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts)
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return pd.Series(d["close"].astype(float).values, index=idx.normalize(), name="perp_close")


def _load_symbol(sym):
    """perp/spot/funding 정렬된 일별 df[spot_close, perp_close, fsum] 또는 None."""
    sp_f, fr_f = CARRY_DIR / f"{sym}_spot_1d.csv", CARRY_DIR / f"{sym}_funding.csv"
    perp = _perp_close(sym)
    if perp is None or not sp_f.exists() or not fr_f.exists():
        return None
    spot = pd.read_csv(sp_f)
    sidx = pd.to_datetime(spot["ts"], format="ISO8601").dt.tz_localize(None).dt.normalize()
    spot = pd.Series(spot["close"].astype(float).values, index=sidx, name="spot_close")
    fund = pd.read_csv(fr_f)
    fidx = pd.to_datetime(fund["ts"], format="ISO8601").dt.tz_localize(None).dt.normalize()
    rate = pd.to_numeric(fund["funding_rate"], errors="coerce")
    fsum = rate.groupby(fidx).sum().rename("fsum")            # 일별 funding 합(8h/4h 자동 흡수)
    df = pd.concat([spot[~spot.index.duplicated()],
                    perp[~perp.index.duplicated()], fsum], axis=1, sort=True)
    df = df.dropna(subset=["spot_close", "perp_close"]).sort_index()
    df["fsum"] = df["fsum"].fillna(0.0)        # 결손 보정: 무펀딩일=0 (수입 과대 방지·보수적)
    return df if len(df) else None


def _eligible(max_syms):
    perp_syms = sorted(os.path.basename(p).replace("_1d.parquet", "")
                       for p in glob.glob(str(PERP_DIR / "*_1d.parquet")))
    out, bench = [], []
    for sym in perp_syms:
        df = _load_symbol(sym)
        if df is None or len(df) < ALIGN_MIN:
            continue
        # 품질 기준: 정합 일수/펀딩 충분 (결손율은 공통 구간 자체가 정합이라 0 으로 수렴)
        if df["fsum"].ne(0).sum() < FUND_MIN:
            continue
        if sym in PROTECTED:
            bench.append(sym)         # 데이터 벤치마크 — 거래 유니버스 제외
        else:
            out.append(sym)
    return out[:max_syms], bench


def _carry_series(sym, costs):
    """심볼 일별 net return 시퀀스 + 분해 누적(USD). 반환: (returns_by_date, comp, total_net)."""
    df = _load_symbol(sym)
    if df is None or len(df) < 2:
        return {}, defaultdict(float), 0.0
    spot = df["spot_close"].values
    perp = df["perp_close"].values
    fsum = df["fsum"].values
    dates = df.index
    comp = defaultdict(float)
    rets, total_net = {}, 0.0
    T = len(df)
    for i in range(T - 1):
        qty = N / spot[i]
        s_leg, p_leg, basis = basis_pnl(qty, spot[i], spot[i + 1], perp[i], perp[i + 1])
        fund = funding_income(qty * perp[i], fsum[i + 1])     # 보유일(i→i+1) = 달력일 i+1 펀딩
        s_ret, p_ret = spot[i + 1] / spot[i] - 1.0, perp[i + 1] / perp[i] - 1.0
        to_s, to_p = N * abs(s_ret), N * abs(p_ret)           # 일 rebalance turnover(양다리)
        spot_fee = to_s * costs.spot_fee
        fut_fee = to_p * costs.fut_fee
        slip = to_s * costs.spot_slip + to_p * costs.fut_slip
        rebal = spot_fee + fut_fee + slip
        day_net = basis + fund - rebal
        # 1회 개시(첫 보유일)·청산(마지막 보유일) 양다리 full-notional 비용
        if i == 0:
            o_sf, o_ff = N * costs.spot_fee, N * costs.fut_fee
            o_sl = N * (costs.spot_slip + costs.fut_slip)
            spot_fee += o_sf; fut_fee += o_ff; slip += o_sl
            comp["entry_exit"] += o_sf + o_ff + o_sl; day_net -= o_sf + o_ff + o_sl
        if i == T - 2:
            c_sf, c_ff = N * costs.spot_fee, N * costs.fut_fee
            c_sl = N * (costs.spot_slip + costs.fut_slip)
            spot_fee += c_sf; fut_fee += c_ff; slip += c_sl
            comp["entry_exit"] += c_sf + c_ff + c_sl; day_net -= c_sf + c_ff + c_sl
        comp["spot_leg"] += s_leg; comp["perp_leg"] += p_leg; comp["basis"] += basis
        comp["funding"] += fund
        comp["spot_fee"] += spot_fee; comp["fut_fee"] += fut_fee; comp["slip"] += slip
        comp["rebal"] += rebal
        rets[dates[i + 1]] = day_net / N
        total_net += day_net
    return rets, comp, total_net


def _mdd(equity):
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak * 100.0)
    return mdd


def _worst_window(dates, equity, days=90):
    """최악 90일 롤링 수익(%)."""
    worst, j = 0.0, 0
    for i in range(len(dates)):
        while (dates[i] - dates[j]).days > days:
            j += 1
        if equity[j] > 0:
            worst = min(worst, equity[i] / equity[j] - 1.0)
    return worst * 100.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-syms", type=int, default=60)
    args = ap.parse_args()
    costs = CarryCosts()

    syms, bench = _eligible(args.max_syms)
    print("=" * 80)
    print("Phase D-2 — 델타중립 캐리 오프라인 net-EV 검정 (baseline, threshold 없음)")
    print(f"eligible(거래 유니버스, 보호종목 제외) {len(syms)}종목 | 벤치마크(데이터만) {bench}")
    print(f"비용(보수적): spot {costs.spot_fee*100:.3f}%/fut {costs.fut_fee*100:.3f}% fee + "
          f"slip spot {costs.spot_slip*1e4:.0f}/fut {costs.fut_slip*1e4:.0f}bps. N=${N:,.0f}/심볼.")
    print("⚠ 오프라인 검정 — 라이브 spot leg 구현 가능 판정 아님(TIER-1 충돌, C 유지).")
    print("=" * 80)

    port_daily = defaultdict(list)
    comp_tot = defaultdict(float)
    sym_net, sym_span = {}, {}
    for sym in syms:
        rets, comp, tnet = _carry_series(sym, costs)
        if not rets:
            continue
        for d, r in rets.items():
            port_daily[d].append(r)
        for k, v in comp.items():
            comp_tot[k] += v
        sym_net[sym] = tnet
        ds = sorted(rets)
        sym_span[sym] = (ds[0], ds[-1], len(ds))

    if not port_daily:
        print("거래 가능 데이터 없음 — 판정불가"); return

    dates = sorted(port_daily)
    port_ret = [sum(port_daily[d]) / len(port_daily[d]) for d in dates]   # 동일 가중
    equity, e = [], 1.0
    for r in port_ret:
        e *= (1.0 + r); equity.append(e)
    span_years = (dates[-1] - dates[0]).days / 365.25
    total_ret = equity[-1] - 1.0
    ann = (equity[-1]) ** (1.0 / span_years) - 1.0 if span_years > 0 and equity[-1] > 0 else float("nan")
    mu = sum(port_ret) / len(port_ret)
    sd = (sum((r - mu) ** 2 for r in port_ret) / (len(port_ret) - 1)) ** 0.5 if len(port_ret) > 1 else 0.0
    sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else 0.0
    mdd = _mdd(equity)
    worst90 = _worst_window(dates, equity)

    s = pd.Series(port_ret, index=pd.DatetimeIndex(dates))
    monthly = s.groupby([s.index.year, s.index.month]).apply(lambda x: (1.0 + x).prod() - 1.0)
    pos_months = float((monthly > 0).mean()) if len(monthly) else 0.0

    funding_t = comp_tot["funding"]
    total_cost = comp_tot["spot_fee"] + comp_tot["fut_fee"] + comp_tot["slip"]
    gross = comp_tot["basis"] + funding_t
    net_pnl = gross - total_cost
    fcr = funding_t / total_cost if total_cost > 0 else float("inf")
    total_net_all = sum(sym_net.values())
    top_sym = max(sym_net, key=sym_net.get) if sym_net else None
    worst_sym = min(sym_net, key=sym_net.get) if sym_net else None
    if top_sym and total_net_all > 0:
        top_share = sym_net[top_sym] / total_net_all * 100.0
        conc_str = f"최대 {top_sym} {top_share:.1f}% of 순이익 (경고 임계 30%)"
    else:
        top_share = float("nan")
        conc_str = (f"N/A — 전체 순손실(${total_net_all:,.0f})이라 이익 집중도 무의미 "
                    f"(최다손실 {worst_sym} ${sym_net[worst_sym]:,.0f})")

    # ---- 보고 ----
    print(f"\n[1] eligible symbol 수      : {len(sym_net)} (게이트 ≥{GATE_MIN_SYMS})")
    print(f"[2] 분석 기간              : {dates[0].date()} ~ {dates[-1].date()} ({span_years:.2f}년, 게이트 ≥{GATE_MIN_YEARS})")
    print(f"[3] 데이터 결손율          : 공통구간 정합(spot∩perp∩funding) — 결손일 funding=0 보정(보수적)")
    print(f"[4] 시간정합 방식          : UTC 일 경계 normalize, funding 8h/4h→일별 합, perp∩spot∩funding 교집합")
    print(f"[5] portfolio net return   : {total_ret*100:+.2f}%")
    print(f"[6] annualized return      : {ann*100:+.2f}%")
    print(f"[7] Sharpe                 : {sharpe:+.2f} (게이트 ≥{GATE_SHARPE})")
    print(f"[8] Max Drawdown           : {mdd:.2f}% (게이트 <{GATE_MDD}%)")
    print(f"[9] 월수익 분포            : {len(monthly)}개월, +비율 {pos_months*100:.1f}% (게이트 ≥{GATE_POS_MONTHS*100:.0f}%) "
          f"| 월중앙값 {monthly.median()*100:+.3f}%")
    print(f"[10] symbol별 net (USD, N=${N:,.0f}/심볼, 상위/하위 5):")
    ranked = sorted(sym_net.items(), key=lambda kv: kv[1], reverse=True)
    for k, v in ranked[:5]:
        print(f"      +{k:12} {v:+10.2f}")
    for k, v in ranked[-5:]:
        print(f"      -{k:12} {v:+10.2f}")
    print(f"[11] funding income 총합   : {funding_t:+,.2f} USD")
    print(f"[12] basis PnL 총합         : {comp_tot['basis']:+,.2f} USD  (spot_leg {comp_tot['spot_leg']:+,.0f} + perp_leg {comp_tot['perp_leg']:+,.0f})")
    print(f"[13] spot/perp 거래비용 총합: spot_fee {comp_tot['spot_fee']:,.2f} + fut_fee {comp_tot['fut_fee']:,.2f} = {comp_tot['spot_fee']+comp_tot['fut_fee']:,.2f} USD")
    print(f"[14] rebalance cost(일 turnover): {comp_tot['rebal']:,.2f} USD | 개시·청산 {comp_tot['entry_exit']:,.2f} USD")
    print(f"[15] slippage/spread cost  : {comp_tot['slip']:,.2f} USD")
    print(f"[16] net PnL               : {net_pnl:+,.2f} USD  (gross {gross:+,.2f} − 총비용 {total_cost:,.2f})")
    print(f"[17] funding / total cost  : {fcr:.3f}  (게이트 >1.0)")
    print(f"[18] 단일심볼 의존도        : {conc_str}")
    print(f"[19] worst stress window   : 최악 90일 {worst90:+.2f}% | MDD {mdd:.2f}%")

    # ---- 판정 (사전확정) ----
    print("\n" + "=" * 80)
    if len(sym_net) < GATE_MIN_SYMS or span_years < GATE_MIN_YEARS:
        verdict = f"판정불가 — eligible {len(sym_net)}<{GATE_MIN_SYMS} 또는 기간 {span_years:.2f}<{GATE_MIN_YEARS}년 (데이터 부족)"
    elif funding_t <= total_cost:
        verdict = f"실패 — funding income({funding_t:,.0f}) ≤ 총비용({total_cost:,.0f}). 구조적 캐리가 비용 못 넘음"
    elif net_pnl < 0:
        verdict = f"실패 — net PnL {net_pnl:,.0f}<0 (gross {gross:,.0f}). 비용 차감 후 음수"
    else:
        gates = {
            "Sharpe≥1.0": sharpe >= GATE_SHARPE,
            "MDD<15%": mdd < GATE_MDD,
            "월+≥55%": pos_months >= GATE_POS_MONTHS,
            "funding>비용": funding_t > total_cost,
            "net>0": net_pnl > 0,
        }
        failed = [k for k, ok in gates.items() if not ok]
        concentrated = (not math.isnan(top_share)) and top_share >= 30.0
        if failed:
            verdict = f"실패 — net>0 이나 게이트 미달: {failed}"
        elif concentrated:
            verdict = f"통과보류 — 게이트 충족이나 단일심볼({top_sym}) {top_share:.1f}%≥30% 집중"
        else:
            verdict = "오프라인 후보 통과 (★ 라이브 가능 판정 아님 — spot leg/TIER-1 충돌로 C 유지)"
    print(f"[20] 판정 : {verdict}")
    print("[21] 한계 : 오프라인 검정일 뿐 live spot leg 구현 가능 판정이 아니다. "
          "rebalance=동일notional 일복원(보수적 상한). 수익성 주장 아님.")
    print("=" * 80)


if __name__ == "__main__":
    main()
