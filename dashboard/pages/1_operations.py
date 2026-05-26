"""dashboard/pages/1_operations.py — 운영 상태 (자본 / 포지션 / P&L)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.charts import equity_curve
from dashboard.data_loader import load_capital_state, load_trades

st.set_page_config(page_title="운영 상태", page_icon="📈", layout="wide")
st.title("📈 운영 상태")
st.caption("자본 + 포지션 + 일일 P&L (trades 테이블 + capital_initial/daily_snapshot)")

# 자본 메트릭
capital = load_capital_state()
col1, col2, col3, col4 = st.columns(4)

if capital:
    init = capital.get("initial") or {}
    daily = capital.get("daily") or {}
    with col1:
        st.metric("초기 자본", f"${init.get('initial_wallet_balance', 0):.2f}")
    with col2:
        st.metric("현재 wallet_balance", f"${daily.get('wallet_balance', 0):.2f}")
    with col3:
        st.metric("available_balance", f"${daily.get('available_balance', 0):.2f}")
    with col4:
        st.metric(
            "margin_utilization %",
            f"{daily.get('margin_utilization_pct', 0):.2f}%",
        )
else:
    st.warning("자본 데이터 없음 (capital_initial / capital_daily_snapshot 비어있음)")

st.divider()

# Trades 테이블
trades = load_trades(limit=200)
st.subheader(f"📋 거래 이력 (최근 {len(trades)}건)")

if trades:
    # 요약 지표
    closed = [t for t in trades if t.get("exit_price") is not None]
    open_pos = [t for t in trades if t.get("exit_price") is None]
    total_pnl = sum(float(t.get("pnl_usd") or 0) for t in closed)
    wins = [t for t in closed if (t.get("pnl_usd") or 0) > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0

    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        st.metric("총 거래", f"{len(trades)}")
    with sc2:
        st.metric("청산 완료", f"{len(closed)}")
    with sc3:
        st.metric("열린 포지션", f"{len(open_pos)}")
    with sc4:
        st.metric("누적 P&L (USDT)", f"${total_pnl:.2f}", f"win rate {win_rate:.1f}%")

    # Equity curve
    fig = equity_curve(trades)
    st.plotly_chart(fig, use_container_width=True)

    # 최근 10건 표
    st.dataframe(
        [
            {
                "id": t["id"], "timestamp": t["timestamp"], "symbol": t["symbol"],
                "action": t["action"], "entry": t["entry_price"],
                "exit": t.get("exit_price"), "qty": t["quantity"],
                "pnl_usd": t.get("pnl_usd"), "pnl_pct": t.get("pnl_pct"),
                "exit_reason": t.get("exit_reason"),
                "regime": t.get("regime"),
                "trade_status": t.get("trade_status"),
            }
            for t in trades[:20]
        ],
        use_container_width=True,
    )
else:
    st.info("거래 이력 없음")
