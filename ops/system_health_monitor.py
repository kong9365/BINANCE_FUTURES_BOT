"""
ops/system_health_monitor.py
=====================================================================
SystemHealthMonitor — 시스템 건강 체크

체크 항목:
  1. WebSocket 연결 (kline / userData)
  2. REST API 응답 시간
  3. REST 5xx/4xx 에러율
  4. 로컬 시간 vs Binance 서버 시간 차이 (1초 이상 = 위험)
  5. Binance 점검 공지

이상 시:
  - 경고(warning): 신규 진입 차단
  - 위험(critical): 모든 포지션 안전 청산

명세서: docs/SPEC_v3.1.md §8-6
의존성: binance_client (REST 호출용)
호출자: main_7590.py 메인 루프 시작점 (§8-8 / 부록 E-5)
부수 효과 없음 — 내부 상태 추적만 수행.

v3.1.2 정정 4건 (세션 7, 사용자 승인):
  ① _check_time_sync: python-binance get_server_time() 은 dict 반환.
     명세서는 raw int 가정 → 그대로 두면 실전에서 TypeError →
     시간 동기 체크 영구 실패. dict 면 ["serverTime"] 추출하도록 정정.
  ② rest_history deque 기본 maxlen 100 → 300 (최근 60초 윈도우 확보).
  ③ _rest_stats: 60초 윈도우 하드코딩 → window_seconds 파라미터화.
  ④ HealthReport.checked_at: naive datetime.now() → UTC tz-aware
     (외부 노출 필드 — HealthReport/DB/Telegram, CLAUDE.md TIER 2 시간 규칙).
=====================================================================
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    """시스템 건강 체크 1회 결과 컨테이너.

    Attributes:
        healthy: 이상 항목(경고·위험)이 하나도 없으면 True.
        critical: 즉시 모든 포지션 청산이 필요한 위험 상태면 True.
        issues: 발견된 경고/위험 메시지 목록 (비어 있으면 healthy=True).
        ws_kline_age_seconds: 마지막 kline 수신 후 경과 초 (이력 없으면 None).
        ws_user_age_seconds: 마지막 userData 수신 후 경과 초 (이력 없으면 None).
        rest_avg_latency_ms: 최근 윈도우 REST 평균 응답 시간 (ms, 호출 없으면 None).
        rest_error_rate: 최근 윈도우 REST 에러율 (0.0~1.0, 호출 없으면 None).
        time_diff_seconds: Binance 서버 시간 - 로컬 시간 차이 (초, 체크 실패 시 None).
        checked_at: 체크 수행 시각 (UTC tz-aware).
    """

    healthy: bool
    critical: bool                          # 모든 포지션 청산 필요
    issues: List[str] = field(default_factory=list)
    ws_kline_age_seconds: Optional[float] = None
    ws_user_age_seconds: Optional[float] = None
    rest_avg_latency_ms: Optional[float] = None
    rest_error_rate: Optional[float] = None
    time_diff_seconds: Optional[float] = None
    # v3.1.2 정정 ④: 외부 노출(HealthReport/DB/Telegram) 필드이므로
    # naive datetime.now() → UTC tz-aware 로 정정 (CLAUDE.md TIER 2 시간 규칙).
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class SystemHealthMonitor:
    """
    시스템 건강 체크.

    WebSocket 수신 지연, REST 응답 시간/에러율, 로컬-서버 시간차를 추적하여
    정상(healthy) / 경고(warning) / 위험(critical) 3단계로 판정한다.

    사용:
        monitor = SystemHealthMonitor(binance_client)
        monitor.record_ws_kline_received()
        monitor.record_rest_call(latency_ms=120, success=True)
        report = monitor.check()
        if not report.healthy:
            handle_unhealthy(report)
    """

    def __init__(
        self,
        binance_client,                                # binance REST 호출용
        ws_kline_max_age_s: float = 60.0,              # 1분 이상 캔들 미수신 = 경고
        ws_kline_critical_age_s: float = 180.0,        # 3분 이상 = 위험
        ws_user_max_age_s: float = 300.0,              # 5분 이상 = 경고
        rest_latency_warning_ms: float = 500.0,
        rest_latency_critical_ms: float = 2000.0,
        rest_error_rate_warning: float = 0.10,         # 10% 이상 = 경고
        rest_error_rate_critical: float = 0.30,        # 30% 이상 = 위험
        time_diff_warning_s: float = 1.0,              # 1초 이상 = 경고
        time_diff_critical_s: float = 5.0,             # 5초 이상 = 위험
        rest_history_size: int = 300,                  # v3.1.2 정정 ②: 100 → 300
        time_check_interval_s: int = 300,              # 시간 동기 5분마다 체크
    ):
        """모니터 초기화.

        Args:
            binance_client: get_server_time() 을 제공하는 Binance REST 클라이언트.
                None 이면 시간 동기 체크가 비활성화된다 (경고 로깅).
            ws_kline_max_age_s: kline 미수신 경고 임계 (초).
            ws_kline_critical_age_s: kline 미수신 위험 임계 (초).
            ws_user_max_age_s: userData 미수신 경고 임계 (초).
            rest_latency_warning_ms: REST 평균 응답 경고 임계 (ms).
            rest_latency_critical_ms: REST 평균 응답 위험 임계 (ms).
            rest_error_rate_warning: REST 에러율 경고 임계 (0.0~1.0).
            rest_error_rate_critical: REST 에러율 위험 임계 (0.0~1.0).
            time_diff_warning_s: 로컬-서버 시간차 경고 임계 (초).
            time_diff_critical_s: 로컬-서버 시간차 위험 임계 (초).
            rest_history_size: REST 호출 이력 deque 최대 길이.
            time_check_interval_s: 시간 동기 체크 캐시 주기 (초, 기본 5분).
        """
        # ── 입력 검증 ──
        if binance_client is None:
            logger.warning(
                "[Health] binance_client=None — 시간 동기 체크가 비활성화됩니다."
            )

        self.binance = binance_client

        self.ws_kline_max_age = ws_kline_max_age_s
        self.ws_kline_critical_age = ws_kline_critical_age_s
        self.ws_user_max_age = ws_user_max_age_s
        self.rest_latency_warning = rest_latency_warning_ms
        self.rest_latency_critical = rest_latency_critical_ms
        self.rest_error_rate_warning = rest_error_rate_warning
        self.rest_error_rate_critical = rest_error_rate_critical
        self.time_diff_warning = time_diff_warning_s
        self.time_diff_critical = time_diff_critical_s

        self._last_ws_kline_ts: Optional[float] = None
        self._last_ws_user_ts: Optional[float] = None

        # REST 호출 이력: (timestamp, latency_ms, success)
        # v3.1.2 정정 ②: maxlen 100 → 300 (최근 60초 윈도우를 넉넉히 담기 위함)
        self._rest_history: Deque[tuple] = deque(maxlen=rest_history_size)

        self._last_time_check: Optional[float] = None
        self._last_time_diff: float = 0.0
        self.time_check_interval = time_check_interval_s

    # ── 외부에서 호출하는 기록 메서드 ──
    def record_ws_kline_received(self) -> None:
        """WS kline(캔들) 메시지 수신 시각을 현재 시각으로 기록한다."""
        self._last_ws_kline_ts = time.time()
        logger.debug("[Health] WS kline 수신 기록")

    def record_ws_user_received(self) -> None:
        """WS userData(체결/잔고) 메시지 수신 시각을 현재 시각으로 기록한다."""
        self._last_ws_user_ts = time.time()
        logger.debug("[Health] WS userData 수신 기록")

    def record_rest_call(self, latency_ms: float, success: bool) -> None:
        """REST 호출 1건의 응답 시간과 성공 여부를 이력 deque 에 기록한다.

        Args:
            latency_ms: 응답 소요 시간 (ms). 음수면 경고 후 0.0 으로 보정.
            success: 2xx 응답이면 True, 4xx/5xx/예외면 False.
        """
        if latency_ms < 0:
            logger.warning(
                "[Health] record_rest_call: latency_ms 음수(%.1f) — 0.0 으로 보정",
                latency_ms,
            )
            latency_ms = 0.0
        self._rest_history.append((time.time(), latency_ms, bool(success)))
        logger.debug(
            "[Health] REST 호출 기록: latency=%.1fms success=%s", latency_ms, success
        )

    # ── 건강 체크 ──
    def check(self) -> HealthReport:
        """5개 항목을 점검하고 HealthReport 를 반환한다.

        점검 항목: WS kline / WS userData / REST latency / REST 에러율 /
        로컬-서버 시간차. 위험(critical) 조건이 하나라도 걸리면 해당 메시지를
        logger.error 로 기록하고 report.critical=True 로 반환한다.

        Returns:
            HealthReport — issues 가 비어 있으면 healthy=True.
        """
        issues: List[str] = []
        critical = False

        # 1. WS kline
        ws_kline_age = self._age(self._last_ws_kline_ts)
        if ws_kline_age is None:
            issues.append("WS kline 수신 이력 없음")
        elif ws_kline_age > self.ws_kline_critical_age:
            msg = f"WS kline {ws_kline_age:.0f}초 미수신 (critical)"
            issues.append(msg)
            critical = True
            logger.error("[Health] %s", msg)
        elif ws_kline_age > self.ws_kline_max_age:
            issues.append(f"WS kline {ws_kline_age:.0f}초 미수신 (warning)")

        # 2. WS user
        ws_user_age = self._age(self._last_ws_user_ts)
        if ws_user_age is not None and ws_user_age > self.ws_user_max_age:
            issues.append(f"WS userData {ws_user_age:.0f}초 미수신")

        # 3. REST 통계 (최근 60초 윈도우)
        rest_latency, rest_err_rate = self._rest_stats()
        if rest_latency is not None:
            if rest_latency > self.rest_latency_critical:
                msg = f"REST latency {rest_latency:.0f}ms (critical)"
                issues.append(msg)
                critical = True
                logger.error("[Health] %s", msg)
            elif rest_latency > self.rest_latency_warning:
                issues.append(f"REST latency {rest_latency:.0f}ms (warning)")

        if rest_err_rate is not None:
            if rest_err_rate > self.rest_error_rate_critical:
                msg = f"REST 에러율 {rest_err_rate*100:.1f}% (critical)"
                issues.append(msg)
                critical = True
                logger.error("[Health] %s", msg)
            elif rest_err_rate > self.rest_error_rate_warning:
                issues.append(f"REST 에러율 {rest_err_rate*100:.1f}% (warning)")

        # 4. 시간 동기 (주기적으로만 — 5분 캐시)
        time_diff = self._check_time_sync()
        if time_diff is not None:
            if abs(time_diff) > self.time_diff_critical:
                msg = f"시간 차이 {time_diff:.2f}초 (critical)"
                issues.append(msg)
                critical = True
                logger.error("[Health] %s", msg)
            elif abs(time_diff) > self.time_diff_warning:
                issues.append(f"시간 차이 {time_diff:.2f}초 (warning)")

        # 5. Binance 점검 공지 (옵션, exchange info 의존)
        # → 별도 메서드로 분리하거나 메인 루프에서 직접 처리

        healthy = len(issues) == 0
        return HealthReport(
            healthy=healthy,
            critical=critical,
            issues=issues,
            ws_kline_age_seconds=ws_kline_age,
            ws_user_age_seconds=ws_user_age,
            rest_avg_latency_ms=rest_latency,
            rest_error_rate=rest_err_rate,
            time_diff_seconds=time_diff,
        )

    def _age(self, ts: Optional[float]) -> Optional[float]:
        """타임스탬프(epoch 초) 기준 현재까지 경과 시간(초). ts 가 None 이면 None."""
        return None if ts is None else (time.time() - ts)

    def _rest_stats(
        self, window_seconds: float = 60.0
    ) -> Tuple[Optional[float], Optional[float]]:
        """최근 window_seconds 윈도우의 REST 평균 latency 와 에러율을 계산한다.

        v3.1.2 정정 ③: 명세서 §8-6-2 는 60초 윈도우를 하드코딩했으나
        window_seconds 파라미터로 분리한다 (기본값 60 으로 기존 동작 동일).

        Args:
            window_seconds: 통계 집계 윈도우 길이 (초).

        Returns:
            (avg_latency_ms, error_rate). 이력이 없거나 윈도우 내 호출이
            하나도 없으면 (None, None).
        """
        if not self._rest_history:
            return None, None
        # 최근 window_seconds 윈도우
        cutoff = time.time() - window_seconds
        recent = [(t, l, s) for t, l, s in self._rest_history if t >= cutoff]
        if not recent:
            return None, None
        latencies = [l for _, l, _ in recent]
        successes = [s for _, _, s in recent]
        avg_latency = sum(latencies) / len(latencies)
        error_rate = 1.0 - (sum(successes) / len(successes))
        return avg_latency, error_rate

    def _check_time_sync(self) -> Optional[float]:
        """Binance 서버 시간과 로컬 시간의 차이(초)를 반환한다. 5분 캐시.

        성공 시 결과를 캐시하고 time_check_interval 동안 재호출하지 않는다.
        실패(예외) 시에는 _last_time_check 를 갱신하지 않으므로 다음 check()
        에서 즉시 재시도한다.

        v3.1.2 정정 ①: 명세서 §8-6-2 는 get_server_time() 반환을 raw int 로
        가정했으나 python-binance 는 dict({"serverTime": <ms>}) 를 반환한다.
        그대로 두면 dict / 1000.0 에서 TypeError → 시간 동기 체크 영구 실패
        (세션 5 RegimeDetector 와 동일한 '명세서 가정 vs 실제 라이브러리' 버그).
        dict 면 ["serverTime"] 를 추출하고, int/float 면 그대로 사용한다.

        Returns:
            서버 시간 - 로컬 시간 차이 (초). 캐시 유효 시 캐시값,
            체크 실패 시 None.
        """
        now = time.time()
        if (self._last_time_check is not None
                and (now - self._last_time_check) < self.time_check_interval):
            return self._last_time_diff

        try:
            t0 = time.time()
            raw = self.binance.get_server_time()  # python-binance: {"serverTime": ms}
            t1 = time.time()
            # v3.1.2 정정 ①: dict 반환 대응 (raw int 가정 시 TypeError)
            if isinstance(raw, dict):
                server_time_ms = raw["serverTime"]
            else:
                server_time_ms = raw
            # 왕복 지연의 절반을 보정
            local_at_server = (t0 + t1) / 2
            server_local = server_time_ms / 1000.0
            diff = server_local - local_at_server
            self._last_time_diff = diff
            self._last_time_check = now  # 성공 시에만 캐시 갱신 (실패 시 즉시 재시도)
            return diff
        except Exception as e:
            logger.warning(f"[Health] 시간 동기 체크 실패: {e}")
            return None
