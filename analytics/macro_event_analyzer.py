"""
analytics/macro_event_analyzer.py
=====================================================================
MacroEventAnalyzer — 거시 이벤트 거래 차단

이벤트 ±N시간 윈도우 시 신규 진입 차단.
RegimeDetector의 HIGH_VOL 트리거로 통합됨.

캘린더 입력 옵션:
  (1) 정적 YAML 파일 (수동 등록, 기본)
  (2) 외부 API (Investing.com, Forex Factory 등 — 라이선스 주의)

GPT 사용 (선택):
  - 등록되지 않은 이벤트의 영향도 예측
  - 주 1회 자동 캘린더 동기화 시 사용

명세서: docs/SPEC_v3.1.md §8-5
=====================================================================
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import yaml  # pip install pyyaml

logger = logging.getLogger(__name__)


# 이벤트 중요도
class EventImportance:
    """거시 이벤트 중요도 상수."""

    HIGH = "HIGH"        # FOMC, CPI, ETF 결정
    MEDIUM = "MEDIUM"    # NFP, PCE, GDP, 옵션 만기
    LOW = "LOW"          # 기타


# 중요도별 차단 윈도우 (분) — (이벤트 전, 이벤트 후)
BLOCK_WINDOW_MIN = {
    EventImportance.HIGH: (-120, +120),     # 전 2시간 ~ 후 2시간
    EventImportance.MEDIUM: (-60, +60),
    EventImportance.LOW: (-30, +30),
}


@dataclass
class MacroEvent:
    """
    거시 이벤트 한 건.

    Attributes:
        name: 이벤트 이름 (예: "FOMC Statement")
        event_time_utc: 이벤트 발생 시각 (UTC tz-aware)
        importance: EventImportance.* 중 하나
        description: 상세 설명
        affected_pairs: 영향받는 페어 목록 (빈 리스트 = 모든 페어)
        source: 출처 ("manual", "gpt", "api" 등)
    """

    name: str
    event_time_utc: datetime           # UTC 기준
    importance: str                    # EventImportance.*
    description: str = ""
    affected_pairs: List[str] = field(default_factory=list)  # 빈 리스트 = 모든 페어
    source: str = "manual"

    @classmethod
    def from_dict(cls, d: dict) -> "MacroEvent":
        """
        dict(YAML 항목)로부터 MacroEvent 생성.

        Args:
            d: name / event_time_utc 키를 반드시 포함하는 dict.

        Returns:
            MacroEvent 인스턴스.

        Raises:
            KeyError: 필수 키(name, event_time_utc) 누락 시.
            ValueError: event_time_utc 형식이 ISO8601이 아닐 때.

        Note:
            event_time_utc 가 tz 정보 없이 들어와도 UTC 로 강제 해석한다.
            (호출부 load_calendar() 가 예외를 잡아 해당 이벤트만 스킵)
        """
        parsed = datetime.fromisoformat(d["event_time_utc"])
        # tz 표기가 없으면 UTC 로 강제, 있으면 UTC 로 변환
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)

        importance = d.get("importance", EventImportance.MEDIUM)
        if importance not in BLOCK_WINDOW_MIN:
            logger.warning(
                f"[MacroEvent] 알 수 없는 importance '{importance}' "
                f"(이벤트 '{d.get('name')}') — MEDIUM 으로 대체"
            )
            importance = EventImportance.MEDIUM

        return cls(
            name=d["name"],
            event_time_utc=parsed,
            importance=importance,
            description=d.get("description", ""),
            affected_pairs=d.get("affected_pairs", []),
            source=d.get("source", "manual"),
        )

    def to_dict(self) -> dict:
        """MacroEvent 를 YAML 직렬화용 dict 로 변환."""
        return {
            "name": self.name,
            "event_time_utc": self.event_time_utc.isoformat(),
            "importance": self.importance,
            "description": self.description,
            "affected_pairs": self.affected_pairs,
            "source": self.source,
        }


@dataclass
class BlockCheckResult:
    """
    차단 판정 결과.

    Attributes:
        blocked: 현재 시점이 차단 윈도우 안인지 여부.
        active_events: 현재 활성(윈도우 안) 이벤트 목록.
        minutes_to_next_event: 다음 미래 이벤트까지 남은 분 (없으면 None).
        reasons: 차단 사유 문자열 목록 (텔레그램/로그용).
    """

    blocked: bool
    active_events: List[MacroEvent] = field(default_factory=list)
    minutes_to_next_event: Optional[int] = None
    reasons: List[str] = field(default_factory=list)


class MacroEventAnalyzer:
    """
    거시 이벤트 캘린더 + 차단 판정.

    사용:
        analyzer = MacroEventAnalyzer(calendar_path="config/macro_events.yaml")
        result = analyzer.check_block(symbol="BTCUSDT")
        if result.blocked:
            block_trading(result.reasons)
    """

    def __init__(
        self,
        calendar_path: str = "config/macro_events.yaml",
        custom_window_min: Optional[dict] = None,
        reload_interval_seconds: int = 3600,
        block_on_missing_calendar: bool = False,
    ):
        """
        Args:
            calendar_path: 이벤트 캘린더 YAML 파일 경로.
            custom_window_min: 중요도별 차단 윈도우 오버라이드 (기본 BLOCK_WINDOW_MIN).
            reload_interval_seconds: check_block() 호출 시 자동 리로드 주기(초).
            block_on_missing_calendar: True 면 캘린더 파일 부재/형식오류 시
                모든 신규 진입을 차단한다(감사 M5 — live 운영 모드 보수적 처리).
                기본 False(페이퍼/테스트는 경고만).
        """
        self.calendar_path = calendar_path
        self.window_min = custom_window_min or BLOCK_WINDOW_MIN
        self.reload_interval = reload_interval_seconds
        self.block_on_missing_calendar = block_on_missing_calendar

        self._events: List[MacroEvent] = []
        self._last_loaded: Optional[datetime] = None
        # 감사 M5: 캘린더가 정상 로드됐는지 상태. 파일 부재/형식오류면 False.
        self.calendar_ok: bool = False
        self.load_calendar()

    def load_calendar(self) -> int:
        """
        YAML 파일에서 이벤트 로드. 파일이 없거나 오류 시 빈 리스트로 처리.

        형식 오류가 있는 개별 이벤트는 logger.warning 후 스킵하고,
        나머지 정상 이벤트는 그대로 로드한다.

        Returns:
            로드된 이벤트 개수.
        """
        if not os.path.exists(self.calendar_path):
            logger.warning(f"[MacroEvent] 캘린더 파일 없음: {self.calendar_path}")
            self._events = []
            self._last_loaded = datetime.now()
            self.calendar_ok = False
            return 0

        try:
            with open(self.calendar_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"[MacroEvent] 캘린더 로드 실패: {e}")
            self._events = []
            self._last_loaded = datetime.now()
            self.calendar_ok = False
            return 0

        if not isinstance(data, dict):
            logger.warning(
                f"[MacroEvent] 캘린더 형식 오류 (최상위가 dict 아님): {self.calendar_path}"
            )
            self._events = []
            self._last_loaded = datetime.now()
            self.calendar_ok = False
            return 0

        raw_events = data.get("events", [])
        if not isinstance(raw_events, list):
            logger.warning(
                f"[MacroEvent] 'events' 항목이 리스트 아님: {self.calendar_path}"
            )
            raw_events = []

        events: List[MacroEvent] = []
        for idx, raw in enumerate(raw_events):
            try:
                events.append(MacroEvent.from_dict(raw))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(
                    f"[MacroEvent] 이벤트 #{idx} 형식 오류 — 스킵: {e} (raw={raw!r})"
                )

        self._events = events
        self._last_loaded = datetime.now()
        self.calendar_ok = True

        # 다음 7일 이내 이벤트 개수 집계 (운영 가시성)
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=7)
        upcoming_7d = sum(1 for e in events if now <= e.event_time_utc <= cutoff)
        logger.info(
            f"[MacroEvent] {len(events)}개 이벤트 로드: 다음 7일 {upcoming_7d}개"
        )
        return len(events)

    def add_event(self, event: MacroEvent) -> None:
        """
        런타임에 이벤트를 추가한다 (예: GPT가 새 이벤트 발견).

        Args:
            event: 추가할 MacroEvent.
        """
        self._events.append(event)
        logger.info(f"[MacroEvent] 이벤트 추가: {event.name} @ {event.event_time_utc}")

    def save_calendar(self) -> None:
        """현재 이벤트 리스트를 YAML 파일로 저장한다."""
        try:
            data = {"events": [e.to_dict() for e in self._events]}
            dirname = os.path.dirname(self.calendar_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(self.calendar_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
            logger.info(f"[MacroEvent] 캘린더 저장: {self.calendar_path}")
        except Exception as e:
            logger.error(f"[MacroEvent] 캘린더 저장 실패: {e}")

    def check_block(
        self, symbol: str = "", now_utc: Optional[datetime] = None
    ) -> BlockCheckResult:
        """
        현재 시점이 차단 윈도우에 있는지 판정한다.

        Args:
            symbol: 거래 페어 (이벤트의 affected_pairs 와 매칭).
                빈 문자열이면 페어 필터 없이 모든 이벤트 검사.
            now_utc: 테스트용 현재 시각 주입 (None 이면 실제 UTC now 사용).

        Returns:
            BlockCheckResult — blocked / active_events / minutes_to_next_event / reasons.
        """
        # 주기적 리로드 (now_utc 주입과 무관하게 실제 경과 시간 기준)
        if (
            self._last_loaded is None
            or (datetime.now() - self._last_loaded).total_seconds() > self.reload_interval
        ):
            self.load_calendar()

        now = now_utc or datetime.now(timezone.utc)
        active: List[MacroEvent] = []
        reasons: List[str] = []
        next_event_minutes: Optional[int] = None

        for event in self._events:
            # 페어 필터: affected_pairs 가 지정되어 있고 symbol 이 그 안에 없으면 스킵
            if event.affected_pairs and symbol and symbol not in event.affected_pairs:
                continue

            window_before, window_after = self.window_min.get(
                event.importance, (-60, +60)
            )
            event_start = event.event_time_utc + timedelta(minutes=window_before)
            event_end = event.event_time_utc + timedelta(minutes=window_after)

            if event_start <= now <= event_end:
                active.append(event)
                reasons.append(
                    f"{event.name} ({event.importance}) "
                    f"@ {event.event_time_utc.strftime('%Y-%m-%d %H:%M UTC')}"
                )
            elif now < event_start:
                # 다음 이벤트까지 남은 시간 (가장 가까운 것)
                mins = int((event_start - now).total_seconds() / 60)
                if next_event_minutes is None or mins < next_event_minutes:
                    next_event_minutes = mins

        return BlockCheckResult(
            blocked=len(active) > 0,
            active_events=active,
            minutes_to_next_event=next_event_minutes,
            reasons=reasons,
        )

    def is_blocked(self, symbol: str = "") -> bool:
        """
        간편 호출 — 차단 여부 bool 만 반환한다.

        Args:
            symbol: 거래 페어.

        Returns:
            차단 윈도우 안이면 True. 감사 M5: block_on_missing_calendar 이고
            캘린더가 정상 로드되지 않았으면(파일 부재/형식오류) 무조건 True.
        """
        if self.block_on_missing_calendar and not self.calendar_ok:
            logger.warning(
                "[MacroEvent] 캘린더 미로드 + block_on_missing_calendar → 신규 진입 차단"
            )
            return True
        return self.check_block(symbol).blocked

    def get_upcoming(
        self, hours: int = 24, symbol: str = "", now_utc: Optional[datetime] = None
    ) -> List[MacroEvent]:
        """
        다음 N시간 내 이벤트 목록을 시간순으로 반환한다.

        Args:
            hours: 조회할 미래 시간 범위(시간 단위).
            symbol: 거래 페어 (affected_pairs 필터). 빈 문자열이면 필터 없음.
            now_utc: 테스트용 현재 시각 주입 (None 이면 실제 UTC now 사용).

        Returns:
            event_time_utc 오름차순 정렬된 MacroEvent 리스트.
        """
        now = now_utc or datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours)
        result = []
        for e in self._events:
            if e.event_time_utc < now or e.event_time_utc > cutoff:
                continue
            if e.affected_pairs and symbol and symbol not in e.affected_pairs:
                continue
            result.append(e)
        return sorted(result, key=lambda e: e.event_time_utc)
