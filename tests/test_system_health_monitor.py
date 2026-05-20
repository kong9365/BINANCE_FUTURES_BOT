"""
tests/test_system_health_monitor.py
=====================================================================
SystemHealthMonitor 단위 테스트 — 명세서 §8-6 필수 6개 시나리오.

  1. 정상 상태 (WS 5초 전 수신, REST 200ms 평균, 시간차 0.5초)
     → healthy=True, critical=False, issues=[]
  2. WS kline 65초 미수신 (>60 warning, <180 critical)
     → healthy=False, critical=False, issues 1건 (warning)
  3. WS kline 200초 미수신 (>180 critical)
     → healthy=False, critical=True
  4. REST 평균 latency 2500ms (>2000 critical)
     → healthy=False, critical=True
  5. REST 에러율 35% (>30% critical)
     → healthy=False, critical=True
  6. 시간 차이 6초 (>5 critical)
     → healthy=False, critical=True

외부 호출(binance.get_server_time)은 unittest.mock 으로 격리.
시간 조작은 monitor 내부 타임스탬프 필드(_last_ws_kline_ts 등)를 직접 설정.
=====================================================================
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from ops.system_health_monitor import HealthReport, SystemHealthMonitor


def _make_binance(server_offset_s: float = 0.0) -> MagicMock:
    """get_server_time() 이 {"serverTime": <ms>} dict 를 반환하는 가짜 클라이언트.

    server_offset_s 만큼 로컬 시간보다 앞선 서버 시간을 흉내낸다.
    python-binance 의 실제 반환 형식(dict)을 따른다 — v3.1.2 정정 ① 검증용.
    """
    binance = MagicMock()
    binance.get_server_time = MagicMock(
        return_value={"serverTime": int((time.time() + server_offset_s) * 1000)}
    )
    return binance


# ── 시나리오 1: 정상 상태 → healthy=True, critical=False, issues=[] ──
def test_scenario_1_all_healthy():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=0.5))
    now = time.time()
    monitor._last_ws_kline_ts = now - 5.0       # WS kline 5초 전 수신
    monitor._last_ws_user_ts = now - 10.0       # WS userData 10초 전 수신
    for _ in range(5):
        monitor.record_rest_call(latency_ms=200.0, success=True)

    report = monitor.check()

    assert isinstance(report, HealthReport)
    assert report.healthy is True
    assert report.critical is False
    assert report.issues == []
    assert report.ws_kline_age_seconds is not None
    assert report.ws_kline_age_seconds < 60
    assert report.rest_avg_latency_ms == 200.0
    assert report.rest_error_rate == 0.0
    assert report.time_diff_seconds is not None
    assert abs(report.time_diff_seconds) < 1.0   # 0.5초 → warning(1.0) 미만


# ── 시나리오 2: WS kline 65초 미수신 → healthy=False, critical=False, warning 1건 ──
def test_scenario_2_ws_kline_warning():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=0.0))
    monitor._last_ws_kline_ts = time.time() - 65.0   # 60 < 65 < 180

    report = monitor.check()

    assert report.healthy is False
    assert report.critical is False
    assert len(report.issues) == 1
    assert "WS kline" in report.issues[0]
    assert "warning" in report.issues[0]
    assert "65" in report.issues[0]


# ── 시나리오 3: WS kline 200초 미수신 → healthy=False, critical=True ──
def test_scenario_3_ws_kline_critical():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=0.0))
    monitor._last_ws_kline_ts = time.time() - 200.0   # > 180 critical

    report = monitor.check()

    assert report.healthy is False
    assert report.critical is True
    assert any("WS kline" in i and "critical" in i for i in report.issues)


# ── 시나리오 4: REST 평균 latency 2500ms → healthy=False, critical=True ──
def test_scenario_4_rest_latency_critical():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=0.0))
    monitor._last_ws_kline_ts = time.time()           # WS 정상 → REST 단일 원인
    for _ in range(5):
        monitor.record_rest_call(latency_ms=2500.0, success=True)

    report = monitor.check()

    assert report.healthy is False
    assert report.critical is True
    assert report.rest_avg_latency_ms == 2500.0
    assert any("REST latency" in i and "critical" in i for i in report.issues)


# ── 시나리오 5: REST 에러율 35% → healthy=False, critical=True ──
def test_scenario_5_rest_error_rate_critical():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=0.0))
    monitor._last_ws_kline_ts = time.time()           # WS 정상 → REST 단일 원인
    # 20건 중 7건 실패 = 35% (i 0~6 실패, 7~19 성공)
    for i in range(20):
        monitor.record_rest_call(latency_ms=100.0, success=(i >= 7))

    report = monitor.check()

    assert report.healthy is False
    assert report.critical is True
    assert abs(report.rest_error_rate - 0.35) < 1e-9
    assert any("에러율" in i and "critical" in i for i in report.issues)


# ── 시나리오 6: 시간 차이 6초 → healthy=False, critical=True ──
def test_scenario_6_time_sync_critical():
    monitor = SystemHealthMonitor(binance_client=_make_binance(server_offset_s=6.0))
    monitor._last_ws_kline_ts = time.time()           # WS 정상 → 시간 동기 단일 원인
    # 시간 동기 5분 캐시 우회: _last_time_check 를 0 으로 설정 → 캐시 만료 상태 강제
    # → 다음 check() 호출 시 실제 _check_time_sync 가 실행된다.
    monitor._last_time_check = 0

    report = monitor.check()

    assert report.healthy is False
    assert report.critical is True
    assert report.time_diff_seconds is not None
    assert abs(report.time_diff_seconds) > 5.0
    assert any("시간 차이" in i and "critical" in i for i in report.issues)
