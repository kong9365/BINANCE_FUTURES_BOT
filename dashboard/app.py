"""
dashboard/app.py
=====================================================================
Streamlit 봇 운영 + 시장 차트 통합 대시보드 entry (v3.2.0 M10).

실행:
    scripts/run_dashboard.bat                                              # Windows
    streamlit run dashboard/app.py --server.address localhost              # Linux/Mac

브라우저: http://localhost:8501
=====================================================================
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 프로젝트 루트 sys.path 추가 (Streamlit 실행 시)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from dashboard.data_loader import (  # noqa: E402
    get_db_path, count_audit_events_by_type, load_capital_state,
)


st.set_page_config(
    page_title="Binance Futures Bot — v3.2.0 대시보드",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    st.title("🤖 Binance Futures Bot — v3.2.0 대시보드")
    st.caption(
        "청사진 v1.0 + 운영자 결정 (2026-05-27) 반영. "
        "읽기 전용 UI — 거래 결정 영향 없음 (TIER 1 #10)."
    )

    # 사이드바 — 현재 상태 요약
    with st.sidebar:
        st.header("📊 현재 상태")
        st.markdown(f"**DB**: `{get_db_path()}`")

        capital = load_capital_state()
        if capital and capital.get("initial"):
            init = capital["initial"]
            st.metric("초기 자본", f"${init.get('initial_wallet_balance', 0):.2f}")
        if capital and capital.get("daily"):
            d = capital["daily"]
            st.metric("최근 wallet_balance", f"${d.get('wallet_balance', 0):.2f}")
            st.metric("available", f"${d.get('available_balance', 0):.2f}")

        st.divider()
        st.markdown("**📍 페이지 안내**")
        st.markdown("""
        1. **운영 상태** — 자본/포지션/P&L/Heartbeat
        2. **5-Agent 검토** — 매트릭스 + 최근 verdicts
        3. **ALCOA+ 감사 로그** — chain 무결성
        4. **Kill Switch** — 이력 + 수동 해제
        5. **시장 차트** — 1m OHLC + CVD
        6. **Setup Registry** — 7기준 메트릭
        """)

    # 메인 — 요약 메트릭
    col1, col2, col3, col4 = st.columns(4)

    event_counts = count_audit_events_by_type()
    total_events = sum(event_counts.values())

    with col1:
        st.metric("총 audit_log", f"{total_events}")
    with col2:
        st.metric("SIGNAL_GENERATED", f"{event_counts.get('SIGNAL_GENERATED', 0)}")
    with col3:
        st.metric("AGENT_REVIEW", f"{event_counts.get('AGENT_REVIEW', 0)}")
    with col4:
        st.metric(
            "KILL_SWITCH_ACTIVATED",
            f"{event_counts.get('KILL_SWITCH_ACTIVATED', 0)}",
        )

    st.divider()

    st.subheader("📌 안내")
    st.info("""
    좌측 **사이드바 페이지**에서 상세 정보 확인하세요.

    - 데이터는 **5초 캐시** (DB 부하 ↓). 갱신은 페이지 우상단 ⟳ 또는 새로고침.
    - 본 대시보드는 **read-only**. 거래·KillSwitch 해제 등 *수동 작업*은 별도 명시 confirm 필요.
    - 봇 (main_7590) 은 *별도 프로세스* — 대시보드 영향 0.
    """)

    st.markdown("---")

    # event_type 분포 차트
    from dashboard.charts import event_type_distribution
    fig = event_type_distribution(event_counts)
    st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
