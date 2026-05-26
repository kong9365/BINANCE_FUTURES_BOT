"""dashboard/pages/4_kill_switch.py — KillSwitch 이력 + 현재 상태 + 수동 해제."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.charts import kill_switch_history
from dashboard.data_loader import load_kill_switch_events
from governance.kill_switch import KillSwitch

st.set_page_config(page_title="Kill Switch", page_icon="🛑", layout="wide")
st.title("🛑 Kill Switch")
st.caption("청사진 §3.4.3 — file OR btc_is_halted 어댑터 + 6조건 자동 활성화 (M4)")

# 현재 상태
active = KillSwitch.is_active()
status = KillSwitch.get_status()

col1, col2, col3 = st.columns(3)
with col1:
    if active:
        st.error(f"⚠️ ACTIVE")
    else:
        st.success("✅ INACTIVE")
with col2:
    st.metric("KILLSWITCH 파일", str(KillSwitch.path()))
with col3:
    if status:
        st.write(f"**Reason**: {status.get('reason', '—')}")
        st.write(f"**Source**: {status.get('source', '—')}")

st.divider()

# 수동 해제 (운영자만, double confirm)
if active:
    st.subheader("🔓 수동 해제 (운영자만)")
    st.warning("KillSwitch 해제는 운영자 책임. 시스템 이상 원인 파악 후 진행하세요.")

    confirm = st.text_input(
        "확인: 'DEACTIVATE' 입력 후 버튼 클릭",
        value="",
        help="대문자로 DEACTIVATE 입력",
    )
    if st.button("🔓 KillSwitch 해제", disabled=(confirm != "DEACTIVATE")):
        try:
            KillSwitch.deactivate(by="operator")
            st.success("✅ KillSwitch 해제 완료. 페이지 새로고침으로 확인.")
        except Exception as e:  # noqa: BLE001
            st.error(f"해제 실패: {e}")
else:
    st.info("KillSwitch 비활성 — 봇 정상 운영 가능")

st.divider()

# 이력
events = load_kill_switch_events(limit=100)
st.subheader(f"📋 활성화 이력 (n={len(events)})")

if events:
    fig = kill_switch_history(events)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        [
            {
                "activated_at": e["activated_at"],
                "deactivated_at": e.get("deactivated_at") or "—",
                "reason": e["reason"],
                "source": e["source"],
                "deactivated_by": e.get("deactivated_by") or "—",
            }
            for e in events
        ],
        use_container_width=True,
    )
else:
    st.info("kill_switch_events 비어있음")
