"""
tests/test_macro_event_analyzer.py
=====================================================================
MacroEventAnalyzer 단위 테스트 — 명세서 §8-5 필수 5개 시나리오.

  1. 이벤트 윈도우 안 (FOMC -30분)         → blocked=True, importance="HIGH"
  2. 이벤트 윈도우 밖 (FOMC -3시간)        → blocked=False
  3. affected_pairs 필터 (affects=BTCUSDT, symbol=SOLUSDT) → blocked=False
  4. 캘린더 파일 없음                       → 빈 리스트, blocked=False, logger.warning
  5. 캘린더 reload (파일 수정 후 reload)    → 새 이벤트 반영

외부 호출 없음. tmp_path 로 임시 YAML 생성, now_utc 주입으로 결정론적.
=====================================================================
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone

from analytics.macro_event_analyzer import (
    BLOCK_WINDOW_MIN,
    BlockCheckResult,
    EventImportance,
    MacroEvent,
    MacroEventAnalyzer,
)

# 모든 시나리오 공통 기준 이벤트 시각 (UTC tz-aware)
FOMC_TIME = datetime(2026, 6, 17, 18, 0, 0, tzinfo=timezone.utc)


def _write_calendar(path, events_yaml: str) -> None:
    """tmp 경로에 events YAML 문자열을 기록한다."""
    path.write_text(
        "events:\n" + textwrap.indent(textwrap.dedent(events_yaml), "  "),
        encoding="utf-8",
    )


# ── 시나리오 1: 이벤트 윈도우 안 (FOMC -30분) → blocked=True, HIGH ──
def test_scenario_1_inside_window_blocked(tmp_path):
    """FOMC 30분 전 → HIGH 윈도우(±120분) 안이므로 차단."""
    cal_path = tmp_path / "macro_events.yaml"
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2026-06-17T18:00:00"
          importance: "HIGH"
          source: "manual"
        """,
    )
    analyzer = MacroEventAnalyzer(calendar_path=str(cal_path))
    now = FOMC_TIME - timedelta(minutes=30)

    result = analyzer.check_block(symbol="SOLUSDT", now_utc=now)

    assert isinstance(result, BlockCheckResult)
    assert result.blocked is True
    assert len(result.active_events) == 1
    assert result.active_events[0].importance == EventImportance.HIGH
    assert result.active_events[0].importance == "HIGH"
    assert "FOMC Statement" in result.reasons[0]


# ── 시나리오 2: 이벤트 윈도우 밖 (FOMC -3시간) → blocked=False ──
def test_scenario_2_outside_window_not_blocked(tmp_path):
    """FOMC 3시간 전 → HIGH 윈도우(±120분) 밖이므로 차단 안 됨."""
    cal_path = tmp_path / "macro_events.yaml"
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2026-06-17T18:00:00"
          importance: "HIGH"
          source: "manual"
        """,
    )
    analyzer = MacroEventAnalyzer(calendar_path=str(cal_path))
    now = FOMC_TIME - timedelta(hours=3)

    result = analyzer.check_block(symbol="SOLUSDT", now_utc=now)

    assert result.blocked is False
    assert result.active_events == []
    # 3시간 전 → 윈도우 시작(이벤트 -120분)까지 60분 남음
    assert result.minutes_to_next_event == 60


# ── 시나리오 3: affected_pairs 필터 작동 → blocked=False ──
def test_scenario_3_affected_pairs_filter(tmp_path):
    """affected_pairs=[BTCUSDT] 인 이벤트는 symbol=SOLUSDT 검사 시 무시된다."""
    cal_path = tmp_path / "macro_events.yaml"
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2026-06-17T18:00:00"
          importance: "HIGH"
          affected_pairs: ["BTCUSDT"]
          source: "manual"
        """,
    )
    analyzer = MacroEventAnalyzer(calendar_path=str(cal_path))
    now = FOMC_TIME - timedelta(minutes=30)

    # SOLUSDT 는 affected_pairs 에 없음 → 차단 안 됨
    result_sol = analyzer.check_block(symbol="SOLUSDT", now_utc=now)
    assert result_sol.blocked is False
    assert result_sol.active_events == []

    # BTCUSDT 는 affected_pairs 에 있음 → 차단됨 (필터가 페어별로 동작함을 확인)
    result_btc = analyzer.check_block(symbol="BTCUSDT", now_utc=now)
    assert result_btc.blocked is True


# ── 시나리오 4: 캘린더 파일 없음 → 빈 리스트, blocked=False, logger.warning ──
def test_scenario_4_missing_calendar_file(tmp_path, caplog):
    """존재하지 않는 경로 → 이벤트 빈 리스트, 차단 안 됨, WARNING 로그."""
    missing_path = tmp_path / "does_not_exist.yaml"
    assert not missing_path.exists()

    with caplog.at_level("WARNING", logger="analytics.macro_event_analyzer"):
        analyzer = MacroEventAnalyzer(calendar_path=str(missing_path))

    # 빈 리스트로 안전하게 초기화
    assert analyzer.get_upcoming(hours=24 * 365) == []

    # 차단 판정도 정상 동작 (blocked=False)
    result = analyzer.check_block(symbol="SOLUSDT", now_utc=FOMC_TIME)
    assert result.blocked is False
    assert result.active_events == []

    # logger.warning 발생 확인
    assert any(
        "캘린더 파일 없음" in rec.message and rec.levelname == "WARNING"
        for rec in caplog.records
    )


# ── 시나리오 5: 캘린더 reload (파일 수정 후 reload → 새 이벤트 반영) ──
def test_scenario_5_calendar_reload(tmp_path):
    """파일을 수정하고 load_calendar() 재호출 시 새 이벤트가 반영된다."""
    cal_path = tmp_path / "macro_events.yaml"

    # 최초: 이벤트 1개
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2026-06-17T18:00:00"
          importance: "HIGH"
          source: "manual"
        """,
    )
    analyzer = MacroEventAnalyzer(calendar_path=str(cal_path))
    now = FOMC_TIME - timedelta(days=10)

    before = analyzer.get_upcoming(hours=24 * 60, now_utc=now)
    assert len(before) == 1
    assert before[0].name == "FOMC Statement"

    # 파일 수정: 이벤트 2개로 갱신
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2026-06-17T18:00:00"
          importance: "HIGH"
          source: "manual"
        - name: "US CPI (May)"
          event_time_utc: "2026-06-10T12:30:00"
          importance: "HIGH"
          source: "manual"
        """,
    )

    # reload 전: 아직 옛 상태 (1개)
    assert len(analyzer.get_upcoming(hours=24 * 60, now_utc=now)) == 1

    # reload 후: 새 이벤트 반영 (2개, 시간순 정렬)
    count = analyzer.load_calendar()
    assert count == 2
    after = analyzer.get_upcoming(hours=24 * 60, now_utc=now)
    assert len(after) == 2
    assert [e.name for e in after] == ["US CPI (May)", "FOMC Statement"]


# ── 시나리오 6 (v3.1.2 감사 M5): block_on_missing_calendar ──
def test_block_on_missing_calendar_blocks(tmp_path):
    """캘린더 파일 부재 + block_on_missing_calendar=True → is_blocked True (보수적)."""
    missing_path = tmp_path / "does_not_exist.yaml"
    analyzer = MacroEventAnalyzer(
        calendar_path=str(missing_path), block_on_missing_calendar=True
    )
    assert analyzer.calendar_ok is False
    assert analyzer.is_blocked("SOLUSDT") is True


def test_missing_calendar_not_blocked_when_flag_off(tmp_path):
    """기본(block_on_missing_calendar=False) → 파일 없어도 차단 안 함(페이퍼/테스트)."""
    missing_path = tmp_path / "does_not_exist.yaml"
    analyzer = MacroEventAnalyzer(calendar_path=str(missing_path))
    assert analyzer.calendar_ok is False
    assert analyzer.is_blocked("SOLUSDT") is False


def test_calendar_ok_true_when_loaded(tmp_path):
    """정상 캘린더 로드 시 calendar_ok=True, block 플래그 켜져도 윈도우 밖이면 통과."""
    cal_path = tmp_path / "macro_events.yaml"
    _write_calendar(
        cal_path,
        """
        - name: "FOMC Statement"
          event_time_utc: "2099-06-17T18:00:00"
          importance: "HIGH"
          source: "manual"
        """,
    )
    analyzer = MacroEventAnalyzer(
        calendar_path=str(cal_path), block_on_missing_calendar=True
    )
    assert analyzer.calendar_ok is True
    # 먼 미래 이벤트 → 현재는 윈도우 밖 → 차단 안 됨
    assert analyzer.is_blocked("SOLUSDT") is False


# ── 보조 검증: MacroEvent from_dict / to_dict 왕복 ──
def test_macro_event_from_to_dict_roundtrip():
    """from_dict → to_dict 왕복 시 핵심 필드가 보존된다."""
    d = {
        "name": "FOMC Statement",
        "event_time_utc": "2026-06-17T18:00:00",
        "importance": "HIGH",
        "description": "rate decision",
        "affected_pairs": ["BTCUSDT", "ETHUSDT"],
        "source": "manual",
    }
    ev = MacroEvent.from_dict(d)
    assert ev.event_time_utc.tzinfo == timezone.utc
    assert ev.importance == "HIGH"

    out = ev.to_dict()
    assert out["name"] == "FOMC Statement"
    assert out["affected_pairs"] == ["BTCUSDT", "ETHUSDT"]
    # 왕복 후에도 동일 이벤트
    ev2 = MacroEvent.from_dict({**out, "event_time_utc": "2026-06-17T18:00:00"})
    assert ev2.event_time_utc == ev.event_time_utc


# ── 보조 검증: BLOCK_WINDOW_MIN 중요도별 윈도우 폭 ──
def test_block_window_min_constants():
    """중요도별 차단 윈도우가 명세서 §8-5-2 와 일치한다."""
    assert BLOCK_WINDOW_MIN[EventImportance.HIGH] == (-120, +120)
    assert BLOCK_WINDOW_MIN[EventImportance.MEDIUM] == (-60, +60)
    assert BLOCK_WINDOW_MIN[EventImportance.LOW] == (-30, +30)
