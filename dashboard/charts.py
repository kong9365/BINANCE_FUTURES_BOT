"""
dashboard/charts.py
=====================================================================
Plotly chart builder (v3.2.0 M10).

근거:
  - dashboard/data_loader.py (DB 쿼리 결과 입력)
  - plotly>=5.18 (캔들/타임라인/히트맵)

각 함수는 *plotly.graph_objects.Figure* 반환 (Streamlit `st.plotly_chart` 직접 입력 가능).
=====================================================================
"""

from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go


# Verdict → 색상 매핑 (5-Agent 매트릭스)
_VERDICT_COLOR = {
    # Strategy
    "PASS": "#22c55e",
    "CONDITIONAL": "#facc15",
    "FAIL": "#ef4444",
    # Risk
    "APPROVE": "#22c55e",
    "REJECT": "#ef4444",
    "ESCALATE": "#f97316",
    # Execution
    "EXECUTE": "#22c55e",
    "DELAY": "#facc15",
    "SKIP": "#94a3b8",
    # Data
    "VALID": "#22c55e",
    "INVALID": "#ef4444",
    "STALE": "#facc15",
    # Ops
    "HEALTHY": "#22c55e",
    "DEGRADED": "#facc15",
    "CRITICAL": "#ef4444",
}


def equity_curve(trades: list[dict]) -> go.Figure:
    """누적 P&L equity curve (운영 상태 페이지)."""
    fig = go.Figure()
    if not trades:
        fig.update_layout(title="Equity Curve (no trades)", height=400)
        return fig
    # trades 는 최신순 → 시간순으로 정렬
    closed = sorted(
        [t for t in trades if t.get("pnl_usd") is not None],
        key=lambda t: t.get("timestamp", ""),
    )
    if not closed:
        fig.update_layout(title="Equity Curve (no closed trades)", height=400)
        return fig
    ts = [t["timestamp"] for t in closed]
    cum = []
    s = 0.0
    for t in closed:
        s += float(t.get("pnl_usd") or 0)
        cum.append(s)
    fig.add_trace(go.Scatter(x=ts, y=cum, mode="lines+markers", name="Cumulative PnL"))
    fig.update_layout(
        title=f"Equity Curve (closed n={len(closed)}, last=${s:.2f})",
        xaxis_title="Time", yaxis_title="Cumulative PnL (USDT)",
        height=400, hovermode="x unified",
    )
    return fig


def signal_timeline(signal_decisions: list[dict]) -> go.Figure:
    """SignalDecision 타임라인 (action 별 색상)."""
    fig = go.Figure()
    if not signal_decisions:
        fig.update_layout(title="Signal Timeline (no signals)", height=400)
        return fig
    for action, color in [("LONG", "#22c55e"), ("SHORT", "#ef4444"), ("FLAT", "#94a3b8")]:
        subset = [s for s in signal_decisions if s.get("action") == action]
        if not subset:
            continue
        fig.add_trace(go.Scatter(
            x=[s["ts_signal_generated"] for s in subset],
            y=[s.get("confidence", 0) for s in subset],
            mode="markers",
            name=action,
            marker=dict(color=color, size=8),
            text=[f"{s.get('symbol')} {s.get('setup_id')} ev={s.get('ev_estimated', 0):.3f}"
                  for s in subset],
            hovertemplate="%{text}<br>confidence=%{y:.2f}<br>%{x}",
        ))
    fig.update_layout(
        title=f"Signal Decisions Timeline (n={len(signal_decisions)})",
        xaxis_title="ts_signal_generated", yaxis_title="Confidence",
        height=400, hovermode="closest",
    )
    return fig


def agent_verdict_matrix(agent_reviews: list[dict]) -> go.Figure:
    """5-Agent verdict 매트릭스 (시간순 stacked timeline)."""
    fig = go.Figure()
    if not agent_reviews:
        fig.update_layout(title="5-Agent Verdict Matrix (no reviews)", height=400)
        return fig
    # Agent 별 시계열
    agents = ["strategy_quant", "risk", "execution", "data_backtest", "ops_observability"]
    for agent in agents:
        subset = [r for r in agent_reviews if r.get("agent") == agent]
        if not subset:
            continue
        colors = [_VERDICT_COLOR.get(r["verdict"], "#94a3b8") for r in subset]
        fig.add_trace(go.Scatter(
            x=[r["ts"] for r in subset],
            y=[agent] * len(subset),
            mode="markers",
            name=agent,
            marker=dict(color=colors, size=12, symbol="square"),
            text=[r["verdict"] for r in subset],
            hovertemplate="%{y} → %{text}<br>%{x}",
        ))
    fig.update_layout(
        title=f"5-Agent Verdict Matrix (n={len(agent_reviews)})",
        xaxis_title="Time", yaxis_title="Agent",
        height=400, hovermode="closest", showlegend=False,
    )
    return fig


def event_type_distribution(event_counts: dict) -> go.Figure:
    """audit_log event_type 별 카운트 (bar chart)."""
    fig = go.Figure()
    if not event_counts:
        fig.update_layout(title="Event Type Distribution (empty)", height=300)
        return fig
    sorted_items = sorted(event_counts.items(), key=lambda x: -x[1])
    fig.add_trace(go.Bar(
        x=[k for k, _ in sorted_items],
        y=[v for _, v in sorted_items],
        text=[v for _, v in sorted_items],
        textposition="auto",
    ))
    fig.update_layout(
        title="Audit Log — Event Type Distribution",
        xaxis_title="event_type", yaxis_title="count",
        height=300,
    )
    return fig


def candlestick_chart(
    candles: list[tuple], symbol: str = "OHLC",
) -> go.Figure:
    """1m/5m/1h/1d candlestick (시장 차트 페이지).

    Args:
        candles: list of (timestamp, open, high, low, close, volume).
    """
    fig = go.Figure()
    if not candles:
        fig.update_layout(title=f"{symbol} Candlestick (no data)", height=500)
        return fig
    ts = [c[0] for c in candles]
    o = [c[1] for c in candles]
    h = [c[2] for c in candles]
    low = [c[3] for c in candles]
    c = [c[4] for c in candles]
    fig.add_trace(go.Candlestick(x=ts, open=o, high=h, low=low, close=c, name=symbol))
    fig.update_layout(
        title=f"{symbol} Candlestick (n={len(candles)})",
        xaxis_title="Time", yaxis_title="Price",
        height=500, xaxis_rangeslider_visible=False,
    )
    return fig


def setup_registry_table(setups: list[dict]) -> go.Figure:
    """setup_registry 표 (운영자 7기준 메트릭)."""
    if not setups:
        fig = go.Figure()
        fig.update_layout(title="Setup Registry (empty)", height=200)
        return fig

    cols = ["setup_id", "status", "n", "PF", "expR", "win/loss", "MDD%",
            "single%", "top3_excl_PF", "win_rate"]

    def _fmt(v, prec=2):
        if v is None:
            return "—"
        try:
            return f"{float(v):.{prec}f}"
        except (TypeError, ValueError):
            return str(v)

    rows = []
    for s in setups:
        rows.append([
            s.get("setup_id", "—"),
            s.get("status", "—"),
            _fmt(s.get("last_metric_n"), 0),
            _fmt(s.get("last_metric_pf")),
            _fmt(s.get("last_metric_expectancy_r"), 4),
            _fmt(s.get("last_metric_avg_win_loss_ratio")),
            _fmt(s.get("last_metric_mdd_pct")),
            _fmt(s.get("last_metric_single_symbol_max_pct")),
            _fmt(s.get("last_metric_top3_excluded_pf")),
            _fmt(s.get("last_metric_win_rate")),
        ])
    # 컬럼 단위로 transpose
    cells = [[row[i] for row in rows] for i in range(len(cols))]

    fig = go.Figure(data=[go.Table(
        header=dict(values=cols, fill_color="#1e293b", font=dict(color="white", size=12), align="left"),
        cells=dict(values=cells, align="left"),
    )])
    fig.update_layout(title=f"Setup Registry (n={len(setups)})", height=300)
    return fig


def kill_switch_history(events: list[dict]) -> go.Figure:
    """KillSwitch 활성화 이력 (timeline)."""
    fig = go.Figure()
    if not events:
        fig.update_layout(title="Kill Switch History (no events)", height=300)
        return fig
    # 활성화 시각 (red) vs 해제 시각 (green)
    activated_ts = [e["activated_at"] for e in events if e.get("activated_at")]
    activated_sources = [e.get("source", "unknown") for e in events if e.get("activated_at")]
    deactivated_ts = [e["deactivated_at"] for e in events if e.get("deactivated_at")]

    fig.add_trace(go.Scatter(
        x=activated_ts, y=[1] * len(activated_ts),
        mode="markers", name="ACTIVATED",
        marker=dict(color="#ef4444", size=14, symbol="x"),
        text=activated_sources,
        hovertemplate="ACTIVATED<br>source: %{text}<br>%{x}",
    ))
    if deactivated_ts:
        fig.add_trace(go.Scatter(
            x=deactivated_ts, y=[0] * len(deactivated_ts),
            mode="markers", name="DEACTIVATED",
            marker=dict(color="#22c55e", size=14, symbol="circle"),
            hovertemplate="DEACTIVATED<br>%{x}",
        ))
    fig.update_layout(
        title=f"Kill Switch History (events={len(events)})",
        xaxis_title="Time", yaxis_title="",
        yaxis=dict(tickvals=[0, 1], ticktext=["DEACTIVATED", "ACTIVATED"]),
        height=300, hovermode="closest",
    )
    return fig
