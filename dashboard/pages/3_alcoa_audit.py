"""dashboard/pages/3_alcoa_audit.py — ALCOA+ 감사 로그 + chain 무결성."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from audit.chain import verify_chain
from dashboard.charts import event_type_distribution
from dashboard.data_loader import count_audit_events_by_type, load_audit_log

st.set_page_config(page_title="ALCOA+ 감사", page_icon="🔒", layout="wide")
st.title("🔒 ALCOA+ 감사 로그")
st.caption("청사진 §6.4 + §7.2 — blockchain-like chain hash (audit_log UPDATE/DELETE 차단)")

# Chain 무결성 검증 (전체)
all_rows = load_audit_log(limit=10000)
# ts ASC 로 정렬 (verify_chain 입력)
ts_sorted = list(reversed(all_rows))
valid, broken_at = verify_chain([
    {"log_id": r["log_id"], "payload_hash": r["payload_hash"],
     "previous_log_hash": r["previous_log_hash"]}
    for r in ts_sorted
])

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("총 audit_log row", f"{len(all_rows)}")
with col2:
    st.metric("Chain valid", "✅ YES" if valid else "❌ NO")
with col3:
    if not valid:
        st.metric("깨진 index", f"{broken_at}", delta="ALCOA+ Accurate 위반")
    else:
        st.metric("깨진 index", "—")

if not valid:
    st.error(
        f"⚠️ Chain 무결성 위반! broken_at_index={broken_at}\n\n"
        "ALCOA+ Accurate 위반 가능성. audit_log 가 *append-only* 가 아닐 수도. "
        "SQLite trigger 확인 + 운영자 검토 필요."
    )
else:
    st.success(f"✅ Chain 무결성 OK — {len(all_rows)} row 모두 정상 연결")

st.divider()

# Event type 분포
event_counts = count_audit_events_by_type()
st.subheader("📊 Event Type 분포")
fig = event_type_distribution(event_counts)
st.plotly_chart(fig, use_container_width=True)

st.divider()

# 최근 50건 표
st.subheader("📋 최근 50건")
st.dataframe(
    [
        {
            "log_id": r["log_id"][:8] + "...",
            "ts": r["ts"],
            "event_type": r["event_type"],
            "actor": r["actor"],
            "related_signal_id": (r.get("related_signal_id") or "")[:8],
            "payload_hash": (r["payload_hash"] or "")[:12] + "...",
            "previous_log_hash": (r.get("previous_log_hash") or "")[:12] + ("..." if r.get("previous_log_hash") else "—"),
        }
        for r in all_rows[:50]
    ],
    use_container_width=True,
)
