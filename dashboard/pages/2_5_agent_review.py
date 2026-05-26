"""dashboard/pages/2_5_agent_review.py — 5-Agent 검토 매트릭스."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.charts import agent_verdict_matrix
from dashboard.data_loader import load_agent_reviews, load_signal_decisions

st.set_page_config(page_title="5-Agent 검토", page_icon="🛡️", layout="wide")
st.title("🛡️ 5-Agent Subagent 검토단")
st.caption("청사진 §3.2.2 — Strategy / Risk / Execution / Data / Ops")

reviews = load_agent_reviews(limit=500)
signals = load_signal_decisions(limit=100)

# 요약
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("총 reviews", f"{len(reviews)}")
with col2:
    st.metric("SignalDecisions", f"{len(signals)}")
with col3:
    approved = sum(1 for s in signals if s.get("status") == "APPROVED")
    st.metric("APPROVED signals", f"{approved}")

st.divider()

# Verdict 매트릭스
if reviews:
    st.subheader("📊 Verdict Matrix (시간순)")
    fig = agent_verdict_matrix(reviews)
    st.plotly_chart(fig, use_container_width=True)

    # Agent 별 verdict 분포
    st.subheader("📈 Agent 별 Verdict 분포")
    agents = ["strategy_quant", "risk", "execution", "data_backtest", "ops_observability"]
    cols = st.columns(len(agents))
    for i, agent in enumerate(agents):
        subset = [r for r in reviews if r.get("agent") == agent]
        ct = Counter(r["verdict"] for r in subset)
        with cols[i]:
            st.markdown(f"**{agent}** (n={len(subset)})")
            for v, c in ct.most_common():
                st.write(f"- {v}: {c}")

    # 최근 review 표
    st.subheader("📋 최근 50건")
    st.dataframe(
        [
            {
                "ts": r["ts"], "agent": r["agent"], "verdict": r["verdict"],
                "confidence": r.get("confidence"), "signal_id": r["signal_id"][:8] + "...",
            }
            for r in reviews[:50]
        ],
        use_container_width=True,
    )
else:
    st.info("agent_reviews 비어있음 — M9 smoke test 또는 봇 가동 후 5-Agent shadow 실행 필요")
