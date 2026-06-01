"""dashboard/pages/7_paper_log.py — 페이퍼(가상) 트레이딩 로그 (읽기전용·가상·HOLD).

관찰 알림 신호의 *가상* 진입을 미래 데이터로 추적해 누적(6번째 검증). 자동매매 아님.
신호는 5회 검증에서 무엣지로 확인된 신호들 → 페이퍼도 무엣지 예상(정직). PASS처럼 보여도 HOLD.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from monitoring.paper_log import aggregate

st.set_page_config(page_title="페이퍼 로그", page_icon="📝", layout="wide")
st.title("📝 페이퍼(가상) 트레이딩 로그")
st.caption("관찰 알림 신호의 *가상* 진입을 미래 데이터로 추적 — 돈 0, 6번째 검증. 자동매매 아님.")

st.warning(
    "⚠️ 신호는 5회 검증에서 *무엣지*로 확인된 신호들입니다. 페이퍼 로그도 무엣지로 나올 "
    "가능성이 높습니다. **PASS처럼 보여도 실거래 HOLD.** n≥200 누적까지 수주~수개월(급히 보지 말 것)."
)

db = os.environ.get("MONITORING_PAPER_DB") or str(PROJECT_ROOT / "data" / "paper_log.db")
st.markdown(f"DB: `{db}`")

if not os.path.exists(db):
    st.info("아직 페이퍼 로그 DB 없음 (관찰 러너 미가동). MONITORING_ENABLED=true 로 러너 가동 시 생성.")
    st.stop()

agg = aggregate(db)
a = agg["all"]
n = a["n"]

# ── 평결 박스 (신호등) ──
if n < 200:
    st.error(f"🔴 INSUFFICIENT_SAMPLE — n={n} < 200. 통계 판정 불가(표본 부족). 정보일 뿐.")
elif a["gross_r"] > a["cost_r"]:
    st.warning(
        f"🟡 1차 흥미(n={n}) — gross_R {a['gross_r']:.3f} > cost_R {a['cost_r']:.3f}. "
        "단 *검증된 엣지 아님*. 2차 신선 OOS + 재확인 전까지 HOLD."
    )
else:
    st.error(f"🔴 무엣지(n={n}) — gross_R {a['gross_r']:.3f} ≤ cost_R {a['cost_r']:.3f} (예상대로). HOLD.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("닫힌 가상거래", f"{n}")
c2.metric("가상 승률", f"{a['win_rate'] * 100:.1f}%")
c3.metric("평균 net_R", f"{a['avg_net_r']:.3f}")
c4.metric("열린 포지션", f"{agg['n_open']}")

st.divider()

# ── STRONG vs WEAK 분리 ──
st.subheader("STRONG(강한 정렬·알림) vs WEAK(로그) 분리 — 강한 신호가 실제로 더 나았나?")
rows = []
for g in ("STRONG", "WEAK"):
    x = agg[g]
    rows.append({"등급": g, "n": x["n"], "승률%": round(x["win_rate"] * 100, 1),
                 "gross_R": round(x["gross_r"], 3), "cost_R": round(x["cost_r"], 3),
                 "avg_net_R": round(x["avg_net_r"], 3)})
st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ── gross vs cost 막대 ──
st.subheader("gross_R vs cost_R (전체) — 단타 핵심: 비용이 엣지를 넘는가")
st.bar_chart(pd.DataFrame({"R": [a["gross_r"], a["cost_r"]]}, index=["gross_R", "cost_R"]))

# ── 가상 누적곡선 ──
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
closed = [dict(r) for r in conn.execute(
    "SELECT exit_ts, net_r FROM paper_log WHERE status='CLOSED' AND exit_ts IS NOT NULL "
    "ORDER BY exit_ts").fetchall()]
conn.close()
if closed:
    df = pd.DataFrame(closed)
    df["cum_net_R"] = df["net_r"].cumsum()
    st.subheader("가상 누적 net_R (전부 진입했다면)")
    st.line_chart(df.set_index("exit_ts")["cum_net_R"])

st.caption("페이퍼 로그는 directional 엣지를 *만들지 않습니다* — 측정할 뿐. 정직한 무엣지 재확인.")
