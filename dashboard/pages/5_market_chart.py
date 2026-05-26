"""dashboard/pages/5_market_chart.py — 실시간 시장 차트 (1m OHLC + CVD)."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from dashboard.charts import candlestick_chart
from dashboard.data_loader import get_db_path

st.set_page_config(page_title="시장 차트", page_icon="📊", layout="wide")
st.title("📊 시장 가격 실시간 차트")
st.caption("data/aggregator_1m.py + agg_trades_1m 테이블 (Phase 2-E) 데이터")

# Symbol 선택
default_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
symbol = st.selectbox("Symbol", default_symbols, index=0)
limit = st.slider("최근 N분 캔들", min_value=30, max_value=720, value=120, step=30)


@contextmanager
def _connect():
    conn = sqlite3.connect(get_db_path())
    try:
        yield conn
    finally:
        conn.close()


def _load_1m_ohlc(symbol: str, limit: int) -> list[tuple]:
    """agg_trades_1m 테이블에서 1m OHLC 로드 (없으면 빈 리스트)."""
    with _connect() as conn:
        try:
            rows = conn.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM agg_trades_1m
                WHERE symbol = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    # 시간순 정렬 (오름차순)
    return list(reversed(rows))


candles = _load_1m_ohlc(symbol, limit)

if candles:
    st.success(f"✅ {symbol} 1m 캔들 {len(candles)} 개 로드")
    fig = candlestick_chart(candles, symbol=symbol)
    st.plotly_chart(fig, use_container_width=True)

    # 최근 N분 통계
    closes = [c[4] for c in candles]
    volumes = [c[5] for c in candles]
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("현재가", f"${closes[-1]:.4f}")
    with col2:
        st.metric("최고", f"${max(closes):.4f}")
    with col3:
        st.metric("최저", f"${min(closes):.4f}")
    with col4:
        st.metric("총 거래량", f"{sum(volumes):.2f}")
else:
    st.warning(f"⚠️ {symbol} 의 agg_trades_1m 데이터 없음. WSCollector 실행 + Phase 2-E aggregator_1m 가동 필요.")
    st.info("""
    **확인 방법**:
    - `scripts/run_ws_collector.bat` 실행 (운영자 환경 Windows Task `BinanceWSCollect`)
    - 1분 후 첫 캔들 생성 (aggregator_1m._periodic_flush_loop 30s 주기)
    - 다른 symbol 시도 (Tier 0 core: BTC/ETH/SOL/XRP/DOGE/BNB 우선 적재)
    """)
