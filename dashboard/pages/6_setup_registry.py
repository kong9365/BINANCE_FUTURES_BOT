"""dashboard/pages/6_setup_registry.py — Setup Registry + 7기준 메트릭."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.charts import setup_registry_table
from dashboard.data_loader import load_setup_registry

st.set_page_config(page_title="Setup Registry", page_icon="📚", layout="wide")
st.title("📚 Setup Registry")
st.caption("청사진 §6.5 — params_hash 기반 재현성 + 운영자 권장 7기준 메트릭")

setups = load_setup_registry()

# 요약
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("총 setup", f"{len(setups)}")
with col2:
    r0 = sum(1 for s in setups if s.get("status") == "R0_QUALIFIED")
    st.metric("R0_QUALIFIED", f"{r0}")
with col3:
    paper = sum(1 for s in setups if s.get("status") == "PAPER_ONLY")
    st.metric("PAPER_ONLY", f"{paper}")
with col4:
    disabled = sum(1 for s in setups if s.get("status") == "DISABLED")
    st.metric("DISABLED", f"{disabled}")

st.divider()

if setups:
    # 7기준 메트릭 표
    st.subheader("📊 7기준 메트릭")
    fig = setup_registry_table(setups)
    st.plotly_chart(fig, use_container_width=True)

    # 7기준 통과 여부 (실시간 판정)
    st.subheader("🎯 운영자 권장 7기준 게이트 자동 판정")
    for s in setups:
        with st.expander(f"**{s['setup_id']}** ({s.get('status', '—')})"):
            n = s.get("last_metric_n") or 0
            pf = s.get("last_metric_pf") or 0
            expR = s.get("last_metric_expectancy_r") or 0
            ratio = s.get("last_metric_avg_win_loss_ratio") or 0
            mdd = s.get("last_metric_mdd_pct") or 0
            single = s.get("last_metric_single_symbol_max_pct") or 0
            top3_excl = s.get("last_metric_top3_excluded_pf") or 0

            criteria = [
                ("n ≥ 200", n >= 200, f"n={n}"),
                ("Net PF ≥ 1.25", pf >= 1.25, f"PF={pf:.3f}"),
                ("Expectancy_R > 0", expR > 0, f"expR={expR:.4f}"),
                ("avg_win/loss ≥ 1.5", ratio >= 1.5, f"ratio={ratio:.3f}"),
                ("MDD ≤ 25%", mdd <= 25.0, f"MDD={mdd:.2f}%"),
                ("Single symbol < 25%", single < 25.0, f"single={single:.2f}%"),
                ("Top-3 excluded PF ≥ 1.0", top3_excl >= 1.0,
                 f"top3_excl_PF={top3_excl:.3f}"),
            ]
            passed = sum(1 for _, ok, _ in criteria if ok)
            st.markdown(f"**합격: {passed}/7**")
            for name, ok, val in criteria:
                st.markdown(f"- {'✅' if ok else '❌'} {name} ({val})")

            # 권장 status
            if passed == 7:
                st.success("→ R0_QUALIFIED 자격 (Phase 1.5 micro-live 진입 가능)")
            elif passed == 6:
                st.warning("→ CONDITIONAL (운영자 명시 결정)")
            else:
                st.error(f"→ DISABLED 권장 ({7 - passed}개 기준 fail)")
else:
    st.info("setup_registry 비어있음. M2 SetupRegistry.register() 호출 필요.")
