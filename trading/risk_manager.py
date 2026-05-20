"""
trading/risk_manager.py
=====================================================================
RiskManager — 다층 리스크 게이트

근거:
  - docs/SPEC_v3.1.md §9 (리스크 룰북 + check_all / get_max_* 시그니처)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-4 (자본 기준 분리 — capital_manager 의존성)

v3.1.1 변경 (부록 E-4):
  - __init__에 CapitalManager 주입 (자본은 capital_manager에서 자동 조회)
  - check_all()은 async — capital_manager.get_snapshot()이 async이므로
  - 일일 손실 한도 → daily_start_wallet_balance 기준
  - 전체 MDD 한도 → initial_wallet_balance 기준
  - 최소 잔고 → available_balance 기준

설계 메모:
  - §9-2는 check_all / get_max_concurrent_positions / get_max_leverage 만
    본문 코드를 제시. 부록 E-4-2는 _check_daily_loss / _check_total_drawdown /
    _check_min_balance 본문을 제시. 나머지 DB 의존 체크
    (_check_consecutive_losses / _check_monthly_drawdown /
    _check_concurrent_positions / _check_cooldown)는 본문 미제공이므로
    RISK_RULES 임계값을 그대로 따르도록 구현했다 (임의 완화 없음).
  - DB(sqlite3)는 동기 라이브러리 → check_all()에서 asyncio.to_thread로 래핑.
  - DB 조회 실패는 "안전 우선" 원칙으로 차단(passed=False) 처리.
  - 7연패(terminal)는 check_all() 평가 순서상 _check_cooldown보다 먼저
    _check_consecutive_losses에서 차단된다.
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from config.settings import RISK_RULES

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """개별 리스크 체크 결과.

    Attributes:
        passed: 통과 여부 (True면 거래 허용).
        reason: 통과/차단 사유 (로그·디버깅용).
    """

    passed: bool
    reason: str


class RiskManager:
    """다층 리스크 게이트.

    매 신규 진입 직전 check_all()을 호출하여 7개 체크를 모두 통과해야만
    거래를 허용한다. 자본 정보는 생성자에 주입된 CapitalManager에서 조회한다
    (부록 E-4-2).

    사용:
        rm = RiskManager(db_path=SYSTEM_CONFIG.db_path,
                         capital_manager=self.capital_manager)
        if not await rm.check_all():
            return  # 진입 차단
    """

    def __init__(self, db_path: str, capital_manager) -> None:
        """RiskManager 초기화.

        Args:
            db_path: sqlite3 DB 파일 경로 (trades 테이블 조회용).
            capital_manager: CapitalManager 인스턴스. get_snapshot()(async),
                get_initial_capital(), get_daily_start_capital()를 제공해야 한다
                (부록 E-4-2).
        """
        if not db_path:
            logger.warning("[Risk] db_path가 비어 있음 — DB 의존 체크가 모두 차단됨")
        if capital_manager is None:
            logger.warning("[Risk] capital_manager가 None — check_all()은 항상 차단됨")

        self.db_path = db_path
        self.capital_manager = capital_manager

    # ── 메인 게이트 ─────────────────────────────────────────────

    async def check_all(self) -> bool:
        """모든 리스크 체크를 실행하고 전체 통과 여부를 반환한다.

        부록 E-4-2: 자본은 capital_manager에서 자동 조회한다. snapshot /
        initial / daily_start 중 하나라도 미준비(None)면 안전 우선 원칙으로
        즉시 차단한다.

        DB 의존 체크(_check_consecutive_losses / _check_monthly_drawdown /
        _check_concurrent_positions / _check_cooldown)는 sqlite3가 동기
        라이브러리이므로 asyncio.to_thread로 래핑한다.

        Returns:
            7개 체크 전부 통과 시 True, 하나라도 차단 시 False.
        """
        if self.capital_manager is None:
            logger.warning("[Risk] capital_manager 미주입 → 차단")
            return False

        try:
            snapshot = await self.capital_manager.get_snapshot()
        except Exception as e:  # noqa: BLE001 — 외부 호출 실패는 안전 차단
            logger.error("[Risk] 자본 스냅샷 조회 실패: %s → 차단", e)
            return False

        initial = self.capital_manager.get_initial_capital()
        daily_start = self.capital_manager.get_daily_start_capital()

        if snapshot is None or initial is None or daily_start is None:
            logger.warning(
                "[Risk] 자본 정보 미준비 → 차단 "
                "(snapshot=%s, initial=%s, daily_start=%s)",
                snapshot is not None, initial, daily_start,
            )
            return False

        # 평가 순서: 7연패 terminal은 _check_consecutive_losses에서 먼저 차단.
        checks: list[CheckResult] = [
            self._check_daily_loss(snapshot.wallet_balance, daily_start),
            self._check_total_drawdown(snapshot.wallet_balance, initial),
            await asyncio.to_thread(self._check_consecutive_losses),
            await asyncio.to_thread(
                self._check_monthly_drawdown, snapshot.wallet_balance
            ),
            await asyncio.to_thread(
                self._check_concurrent_positions, snapshot.wallet_balance
            ),
            self._check_min_balance(snapshot.available_balance),
            await asyncio.to_thread(self._check_cooldown),
        ]

        all_passed = True
        for c in checks:
            if c.passed:
                logger.info("[Risk] PASS — %s", c.reason)
            else:
                logger.warning("[Risk] BLOCK — %s", c.reason)
                all_passed = False

        if all_passed:
            logger.info("[Risk] check_all 통과 — 진입 허용")
        else:
            logger.warning("[Risk] check_all 차단 — 진입 거부")
        return all_passed

    # ── 자본 기준 체크 (부록 E-4-2 본문 1:1) ────────────────────

    def _check_daily_loss(self, current: float, daily_start: float) -> CheckResult:
        """일일 손실 한도 검사 (부록 E-4-2).

        오늘 시작 시점의 wallet_balance(daily_start) 대비 현재 wallet_balance가
        max_daily_loss_pct(1.5%) 이상 하락하면 차단한다.

        Args:
            current: 현재 wallet_balance.
            daily_start: 오늘 00:00 UTC 시점의 wallet_balance.
        """
        if daily_start <= 0:
            # 감사 M2 — 비정상 베이스라인 fail-closed (한도가 조용히 꺼지지 않도록)
            logger.warning("[Risk] daily_start=%s 비정상 — 안전 차단", daily_start)
            return CheckResult(passed=False, reason=f"daily_start 비정상({daily_start}) → 차단")

        pct = (current - daily_start) / daily_start
        limit = RISK_RULES.max_daily_loss_pct
        if pct <= -limit:
            return CheckResult(
                passed=False,
                reason=f"일일 손실 {pct * 100:.2f}% (한도 -{limit * 100:.1f}%)",
            )
        return CheckResult(passed=True, reason=f"일일 손익 {pct * 100:.2f}%")

    def _check_total_drawdown(self, current: float, initial: float) -> CheckResult:
        """전체 MDD 한도 검사 (부록 E-4-2).

        최초 가동 시점의 wallet_balance(initial) 대비 현재 wallet_balance가
        max_total_drawdown_pct(15%) 이상 하락하면 차단한다 — 절대 청산선.

        Args:
            current: 현재 wallet_balance.
            initial: 최초 가동 시점의 wallet_balance.
        """
        if initial <= 0:
            # 감사 M2 — 비정상 베이스라인 fail-closed
            logger.warning("[Risk] initial=%s 비정상 — 안전 차단", initial)
            return CheckResult(passed=False, reason=f"initial 비정상({initial}) → 차단")

        pct = (current - initial) / initial
        limit = RISK_RULES.max_total_drawdown_pct
        if pct <= -limit:
            return CheckResult(
                passed=False,
                reason=f"전체 MDD {pct * 100:.2f}% (한도 -{limit * 100:.1f}%)",
            )
        return CheckResult(passed=True, reason=f"전체 손익 {pct * 100:.2f}%")

    def _check_min_balance(self, available: float) -> CheckResult:
        """최소 잔고 검사 (부록 E-4-2).

        available_balance가 min_balance_for_trade_usdt($100) 미만이면 차단한다.

        Args:
            available: 사용 가능 잔고 (마진 락 제외).
        """
        limit = RISK_RULES.min_balance_for_trade_usdt
        if available < limit:
            return CheckResult(
                passed=False,
                reason=f"available_balance ${available:.2f} < ${limit:.0f}",
            )
        return CheckResult(passed=True, reason=f"잔고 충분 (${available:.2f})")

    # ── DB 의존 체크 (§9-2 시그니처 — RISK_RULES 기준 구현) ──────

    def _check_consecutive_losses(self) -> CheckResult:
        """연속 손실(연패) 검사 — terminal 임계 차단.

        닫힌 거래의 최신순 연속 손실 횟수가
        max_consecutive_losses_terminal(7) 이상이면 차단한다 (전략 재검토 강제,
        7일 중단 + 페이퍼 강제 — §9-1).

        3·5연패의 시간 기반 쿨다운 차단은 _check_cooldown이 담당하므로
        여기서는 경고 로그만 남기고 통과시킨다.

        DB 조회 실패 시 안전 우선 원칙으로 차단(passed=False)한다.
        """
        try:
            streak, _ = self._get_loss_streak()
        except Exception as e:  # noqa: BLE001 — DB 실패는 안전 차단
            logger.error("[Risk] 연패 조회 실패: %s → 안전 차단", e)
            return CheckResult(passed=False, reason="연패 DB 조회 실패 → 안전 차단")

        terminal = RISK_RULES.max_consecutive_losses_terminal
        critical = RISK_RULES.max_consecutive_losses_critical
        warning = RISK_RULES.max_consecutive_losses_warning

        if streak >= terminal:
            return CheckResult(
                passed=False,
                reason=(
                    f"{streak}연패 — terminal (전략 재검토 강제, "
                    f"7일 중단 + 페이퍼 강제)"
                ),
            )
        if streak >= critical:
            logger.warning("[Risk] %d연패 — critical (쿨다운 체크 위임)", streak)
            return CheckResult(passed=True, reason=f"{streak}연패 (critical, 쿨다운 위임)")
        if streak >= warning:
            logger.warning("[Risk] %d연패 — warning (쿨다운 체크 위임)", streak)
            return CheckResult(passed=True, reason=f"{streak}연패 (warning, 쿨다운 위임)")
        return CheckResult(passed=True, reason=f"연패 {streak}회")

    def _check_monthly_drawdown(self, current: float) -> CheckResult:
        """월간 누적 손실 한도 검사.

        당월(현재 UTC 월) 실현 손익 합계로 월초 잔고를 역산하여
        (month_start = current - 당월손익), 월초 대비 하락률이
        monthly_drawdown_terminal_pct(8%) 이상이면 차단한다 (한 달 중단).

        설계 메모: 입금/출금이 없으면 정확하고, 입금 발생 시 더 보수적으로
        (차단이 빨리) 작동하므로 안전 측면에서 허용 가능. 향후 v3.1.2에서
        capital_daily_snapshot 테이블로 정밀화 검토.

        DB 조회 실패 시 안전 우선 원칙으로 차단(passed=False)한다.

        Args:
            current: 현재 wallet_balance.
        """
        try:
            monthly_pnl = self._get_monthly_realized_pnl()
        except Exception as e:  # noqa: BLE001 — DB 실패는 안전 차단
            logger.error("[Risk] 월간 손익 조회 실패: %s → 안전 차단", e)
            return CheckResult(passed=False, reason="월간 손익 DB 조회 실패 → 안전 차단")

        month_start = current - monthly_pnl
        if month_start <= 0:
            # 감사 M2 — 비정상 베이스라인 fail-closed
            logger.warning("[Risk] 월초 잔고 역산값 %s 비정상 — 안전 차단", month_start)
            return CheckResult(passed=False, reason=f"월초 잔고 비정상({month_start:.2f}) → 차단")

        pct = monthly_pnl / month_start
        limit = RISK_RULES.monthly_drawdown_terminal_pct
        if pct <= -limit:
            return CheckResult(
                passed=False,
                reason=f"월간 손실 {pct * 100:.2f}% (한도 -{limit * 100:.1f}%)",
            )
        return CheckResult(passed=True, reason=f"월간 손익 {pct * 100:.2f}%")

    def _check_concurrent_positions(self, capital: float) -> CheckResult:
        """동시 보유 포지션 수 한도 검사.

        현재 열린 포지션(exit_price IS NULL) 수가 자본 규모별 한도
        (get_max_concurrent_positions)에 도달하면 신규 진입을 차단한다.

        DB 조회 실패 시 안전 우선 원칙으로 차단(passed=False)한다.

        Args:
            capital: 현재 wallet_balance (자본 규모별 한도 산정용).
        """
        try:
            open_count = self._get_open_position_count()
        except Exception as e:  # noqa: BLE001 — DB 실패는 안전 차단
            logger.error("[Risk] 열린 포지션 조회 실패: %s → 안전 차단", e)
            return CheckResult(passed=False, reason="포지션 DB 조회 실패 → 안전 차단")

        max_positions = self.get_max_concurrent_positions(capital)
        if open_count >= max_positions:
            return CheckResult(
                passed=False,
                reason=f"동시 포지션 {open_count}/{max_positions} (한도 도달)",
            )
        return CheckResult(
            passed=True, reason=f"동시 포지션 {open_count}/{max_positions}"
        )

    def _check_cooldown(self) -> CheckResult:
        """연패 기반 쿨다운 잔여 시간 검사.

        연속 손실 횟수에 따라 쿨다운 시간을 산정한다 (§9-1):
            - 7연패 이상: 168시간 (7일)
            - 5연패 이상: 24시간
            - 3연패 이상: 4시간
            - 그 외: 쿨다운 없음

        마지막 손실 거래 시각으로부터 쿨다운 시간이 경과하지 않았으면
        차단하고 잔여 시간을 reason에 담는다.

        DB 조회 실패 시 안전 우선 원칙으로 차단(passed=False)한다.
        """
        try:
            streak, last_loss_at = self._get_loss_streak()
        except Exception as e:  # noqa: BLE001 — DB 실패는 안전 차단
            logger.error("[Risk] 쿨다운 조회 실패: %s → 안전 차단", e)
            return CheckResult(passed=False, reason="쿨다운 DB 조회 실패 → 안전 차단")

        if streak >= RISK_RULES.max_consecutive_losses_terminal:
            cooldown_hours: float = 24.0 * 7  # terminal 7일
        elif streak >= RISK_RULES.max_consecutive_losses_critical:
            cooldown_hours = float(RISK_RULES.cooldown_hours_5_losses)
        elif streak >= RISK_RULES.max_consecutive_losses_warning:
            cooldown_hours = float(RISK_RULES.cooldown_hours_3_losses)
        else:
            return CheckResult(passed=True, reason="쿨다운 없음")

        if last_loss_at is None:
            # 연패가 임계 이상인데 손실 시각이 없는 비정상 상태 — 안전 차단.
            logger.warning("[Risk] 연패 %d인데 마지막 손실 시각 없음 → 안전 차단", streak)
            return CheckResult(passed=False, reason=f"{streak}연패 — 손실 시각 미상")

        now = datetime.now(timezone.utc)
        elapsed_hours = (now - last_loss_at).total_seconds() / 3600.0
        remaining = cooldown_hours - elapsed_hours
        if remaining > 0:
            return CheckResult(
                passed=False,
                reason=(
                    f"{streak}연패 쿨다운 — {cooldown_hours:.0f}시간 중 "
                    f"잔여 {remaining:.1f}시간"
                ),
            )
        return CheckResult(
            passed=True,
            reason=f"{streak}연패 쿨다운 종료 (경과 {elapsed_hours:.1f}시간)",
        )

    # ── 자본/레짐 매핑 (§9-2 본문 1:1) ──────────────────────────

    def get_max_concurrent_positions(self, capital: float) -> int:
        """자본 규모별 동시 보유 포지션 한도 (§9-2).

        Args:
            capital: 현재 자본 (wallet_balance).
        Returns:
            $20,000 이상 3, $5,000 이상 2, 그 외 1.
        """
        if capital >= 20000:
            return RISK_RULES.max_concurrent_positions_above_20k
        if capital >= 5000:
            return RISK_RULES.max_concurrent_positions_above_5k
        return RISK_RULES.max_concurrent_positions

    def get_max_leverage(self, tier: int, regime: str) -> int:
        """Tier × 레짐 최대 레버리지 매트릭스 (§9-2).

        Tier별 한도와 레짐별 한도 중 더 작은 값을 반환한다. 미정의 tier/regime은
        0(차단)으로 처리한다.

        Args:
            tier: 페어 Tier (1/2/3, 0=blocked).
            regime: 시장 레짐 (TREND_UP/TREND_DOWN/RANGING/UNCERTAIN/HIGH_VOL).
        Returns:
            허용 최대 레버리지 (정수).
        """
        tier_max = RISK_RULES.max_leverage_by_tier.get(tier, 0)
        regime_max = RISK_RULES.max_leverage_by_regime.get(regime, 0)
        return min(tier_max, regime_max)

    # ── DB 조회 헬퍼 ────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """sqlite3 연결 생성 (읽기 전용 용도)."""
        return sqlite3.connect(self.db_path)

    def _get_loss_streak(self) -> tuple[int, datetime | None]:
        """닫힌 거래의 최신순 연속 손실 횟수와 마지막 손실 시각을 반환한다.

        손익 기준은 COALESCE(pnl_usd_net, pnl_usd) < 0. 닫힌 거래만
        (exit_price IS NOT NULL) 대상으로 하며, id 내림차순으로 순회하다가
        손실이 아닌 거래를 만나면 카운트를 멈춘다.

        Returns:
            (연속 손실 횟수, 가장 최근 손실 거래의 UTC datetime 또는 None).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, COALESCE(pnl_usd_net, pnl_usd) AS net_pnl
                FROM trades
                WHERE exit_price IS NOT NULL
                ORDER BY id DESC
                """
            ).fetchall()
        finally:
            conn.close()

        streak = 0
        last_loss_at: datetime | None = None
        for ts, net_pnl in rows:
            if net_pnl is None or net_pnl >= 0:
                break
            streak += 1
            if last_loss_at is None:
                last_loss_at = self._parse_ts(ts)
        return streak, last_loss_at

    def _get_monthly_realized_pnl(self) -> float:
        """당월(현재 UTC 월) 닫힌 거래의 실현 손익 합계를 반환한다.

        Returns:
            COALESCE(pnl_usd_net, pnl_usd) 합계 (거래 없으면 0.0).
        """
        month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(pnl_usd_net, pnl_usd)), 0.0)
                FROM trades
                WHERE exit_price IS NOT NULL
                  AND substr(timestamp, 1, 7) = ?
                """,
                (month_prefix,),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) if row and row[0] is not None else 0.0

    def _get_open_position_count(self) -> int:
        """열린 포지션(exit_price IS NULL) 수를 반환한다."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE exit_price IS NULL"
            ).fetchone()
        finally:
            conn.close()
        return int(row[0]) if row and row[0] is not None else 0

    @staticmethod
    def _parse_ts(ts: str | None) -> datetime | None:
        """trades.timestamp(TEXT)를 UTC tz-aware datetime으로 파싱한다.

        naive datetime은 UTC로 간주한다. 파싱 실패 시 None.
        """
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            logger.warning("[Risk] timestamp 파싱 실패: %r", ts)
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
