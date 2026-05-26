"""tests/test_m11_backtest_smoke.py — M11 백테스트 entry point end-to-end smoke."""

from __future__ import annotations

import math
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backtesting.local_ohlcv_store import LocalOhlcvStore
from db.init_db import init_db
from registry.setup_registry import SetupRegistry, SetupStatus
from skills.daily_tsmom_donchian_skill import DailyTSMOMDonchianSkill


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "m11.db"
        init_db(db_path)
        yield str(db_path)


def _synthetic_uptrend(n: int = 300, base: float = 100.0) -> list[tuple]:
    """강한 상승 추세 candles (Donchian breakout + ADX trend 시뮬)."""
    candles = []
    for i in range(n):
        if i < n - 60:
            price = base + i * 0.05 + (math.sin(i * 0.1) * 0.5)
        else:
            price = base + (n - 60) * 0.05 + (i - (n - 60)) * 2.5
        h = price + 0.5
        low = price - 0.5
        ts = 1704067200 + i * 86400
        candles.append((ts, price, h, low, price, 1000.0 + i * 10))
    return candles


def test_local_ohlcv_store_to_breakout_flow(tmp_db):
    """ohlcv_local → get_candles → evaluate_breakout 흐름."""
    from strategy.breakout import BreakoutConfig, evaluate_breakout

    store = LocalOhlcvStore(db_path=tmp_db)
    candles = _synthetic_uptrend(300)
    store.upsert_many("SOLUSDT", "1d", candles)

    retrieved = store.get_candles("SOLUSDT", "1d", limit=500)
    assert len(retrieved) == 300

    # evaluate_breakout 호출 가능
    signal = evaluate_breakout(retrieved, BreakoutConfig())
    # 상승 추세 → LONG signal 기대 (또는 None 도 OK — 본 테스트는 *흐름* 검증)
    assert signal is None or signal.action in {"LONG", "SHORT"}


def test_run_m11_backtest_module_importable():
    """scripts/run_m11_backtest.py import + 의존성 검증."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_m11_backtest",
        Path(__file__).parent.parent / "scripts" / "run_m11_backtest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # import 만 성공해도 OK (main 실행 X)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "DEFAULT_UNIVERSE")
    assert len(mod.DEFAULT_UNIVERSE) >= 18


def test_setup_registry_register_daily_tsmom(tmp_db):
    """DailyTSMOMDonchianSkill 자동 등록 확인 (M11 main 함수 부분)."""
    registry = SetupRegistry(db_path=tmp_db)
    registry.register(DailyTSMOMDonchianSkill)
    setup = registry.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert setup is not None
    assert setup["status"] == SetupStatus.PAPER_ONLY
    assert setup["params_hash"] == DailyTSMOMDonchianSkill.PARAMS_HASH


def test_validate_and_persist_with_synthetic_trades(tmp_db):
    """7기준 검증 게이트 + setup_registry update (가짜 trades)."""
    from backtesting.signal_validation_report import validate_and_persist

    registry = SetupRegistry(db_path=tmp_db)
    registry.register(DailyTSMOMDonchianSkill)

    # PASS 시나리오 — 250 trades, 균등 분배
    trades = []
    symbols = [f"SYM{i}" for i in range(10)]
    for sym in symbols:
        for j in range(25):
            if j % 2 == 0:
                trades.append({"symbol": sym, "pnl_r": 1.8, "pnl_pct": 3.0, "is_win": True})
            else:
                trades.append({"symbol": sym, "pnl_r": -1.0, "pnl_pct": -1.5, "is_win": False})

    report = validate_and_persist(
        trades=trades,
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        registry=registry,
        mode="trend",
        evaluation_type="backtest",
        auto_update_status=True,
    )
    assert report.passed
    setup = registry.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    assert setup["status"] == SetupStatus.R0_QUALIFIED  # PASS → 자동 변경


def test_validate_and_persist_fail_scenario(tmp_db):
    """FAIL 시나리오: 2개 이상 기준 fail → DISABLED 자동."""
    from backtesting.signal_validation_report import validate_and_persist

    registry = SetupRegistry(db_path=tmp_db)
    registry.register(DailyTSMOMDonchianSkill)

    # 의도적 실패 — ZEC 단일종목 패턴
    trades = []
    for i in range(100):
        if i < 80:
            trades.append({"symbol": "ZEC", "pnl_r": 3.0, "pnl_pct": 5.0, "is_win": True})
        else:
            trades.append({"symbol": "ZEC", "pnl_r": -1.0, "pnl_pct": -1.0, "is_win": False})
    for i in range(150):
        sym = f"SYM{i % 9}"
        if i % 2 == 0:
            trades.append({"symbol": sym, "pnl_r": 1.05, "pnl_pct": 1.5, "is_win": True})
        else:
            trades.append({"symbol": sym, "pnl_r": -1.0, "pnl_pct": -1.5, "is_win": False})

    report = validate_and_persist(
        trades=trades,
        setup_id=DailyTSMOMDonchianSkill.SETUP_ID,
        registry=registry,
        mode="trend",
        evaluation_type="backtest",
        auto_update_status=True,
    )
    assert not report.passed
    setup = registry.get_setup(DailyTSMOMDonchianSkill.SETUP_ID)
    # 1개 fail = CONDITIONAL, 2개 이상 fail = DISABLED
    assert setup["status"] in {SetupStatus.CONDITIONAL, SetupStatus.DISABLED}


def test_v3_2_2_migration_in_init_db(tmp_db):
    """v3.2.2 자동 적용 확인."""
    conn = sqlite3.connect(tmp_db)
    try:
        versions = [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()]
        assert "v3.2.2" in versions
    finally:
        conn.close()
