"""
trading/exit_plan.py
=====================================================================
ExitPlanController — 진입 후 포지션 청산 계획 자동 관리

근거:
  - docs/SPEC_v3.1.md §8-8-2 ("ExitPlanController (분할 TP, ATR 트레일, BE, 시간 스톱)")
  - docs/SPEC_v3.1.md §8-8-3 ("기존 포지션은 ExitPlanController가 자동 관리")
  - docs/SPEC_v3.1.md D-2 (Layer 5: TradeExecutor.enter_trade() → ExitPlanController.start_tracking())

책임:
  - 진입한 포지션을 추적 등록 (start_tracking)
  - 매 루프 현재가(+ATR)로 청산 조건 평가 (update / update_all):
      ① 시간 스톱   — max_hold_minutes 경과 시 전량 청산
      ② SL 히트     — current_sl 도달 시 전량 청산
      ③ TP 히트     — tp 도달 시 전량 청산
      ④ 분할 TP     — entry→tp 경로의 일정 지점 도달 시 partial_tp_ratio 만큼 부분 청산
      ⑤ BE 이동     — 분할 TP 체결 후 SL 을 진입가(break-even)로 1회 상향
      ⑥ ATR 트레일  — 분할 TP 체결 후 peak 기준 ATR 배수만큼 SL 추적

설계 메모:
  - 명세서는 이 모듈을 "v3.0 유지"로만 표기하므로 §8-8 / D-2 의 호출 계약
    기준으로 신규 작성한다.
  - 실제 청산 주문은 TradeExecutor.close_position(symbol, reason, portion) 에
    위임한다 (전량 portion=1.0 / 분할 portion=partial_tp_ratio).
  - close_position 실패 시 해당 plan 을 제거하지 않아 다음 루프에서 재시도된다
    (안전 우선 — 추적을 잃지 않음).
  - current_sl 은 BE/트레일로만 더 유리한 방향으로 이동하며, 불리한 방향으로는
    절대 되돌리지 않는다 (LONG 은 상향만, SHORT 은 하향만).

v3.1.2 감사 후속:
  - 거래소 reduceOnly STOP_MARKET 가 하드 플로어다. ExitPlanController 는
    분할 TP / ATR 트레일 / 시간 스톱 + SL 갱신(거래소 STOP cancel+replace)을
    담당한다. current_sl 이 BE/트레일로 이동하면 _sync_exchange_stop 으로
    거래소 STOP 도 동기화하여 무방비 구간을 만들지 않는다.
  - 진입 시 executor 가 반환한 sl_order_id/tp_order_id 를 start_tracking 으로
    받아 보관한다.
=====================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ExitPlan:
    """단일 포지션의 청산 계획 추적 상태.

    Attributes:
        trade_id: trades 테이블의 거래 id.
        symbol: 거래 페어.
        action: "LONG" / "SHORT".
        entry_price: 진입가.
        original_sl: 최초 손절가 (불변, 참조용).
        tp: 최종 익절 목표가.
        quantity: 진입 수량.
        opened_at: 진입 시각 (UTC tz-aware).
        max_hold_minutes: 시간 스톱 한도 (분).
        current_sl: 현재 유효 손절가 (BE/트레일로 이동, 가변).
        partial_tp_price: 분할 TP 트리거 가격.
        partial_tp_done: 분할 TP 체결 완료 여부.
        be_moved: SL 을 break-even 으로 이동 완료했는지.
        peak_price: 진입 후 도달한 최고(LONG)/최저(SHORT) 가 — 트레일 기준.
        sl_order_id: 거래소 reduceOnly STOP_MARKET 주문 id (BE/트레일 시 교체 대상).
        tp_order_id: 거래소 reduceOnly TAKE_PROFIT_MARKET 주문 id.
    """

    trade_id: int
    symbol: str
    action: str
    entry_price: float
    original_sl: float
    tp: float
    quantity: float
    opened_at: datetime
    max_hold_minutes: int
    current_sl: float
    partial_tp_price: float
    partial_tp_done: bool = False
    be_moved: bool = False
    peak_price: float = field(default=0.0)
    sl_order_id: str | None = None
    tp_order_id: str | None = None


class ExitPlanController:
    """진입 후 포지션 청산 계획 자동 관리기.

    사용:
        controller = ExitPlanController(executor)
        controller.start_tracking(trade_id, decision, quantity)
        # 매 루프:
        await controller.update_all(price_map, atr_map)
    """

    def __init__(
        self,
        executor,
        partial_tp_ratio: float = 0.5,
        partial_tp_trigger_pct: float = 0.5,
        atr_trail_multiplier: float = 1.5,
        default_max_hold_minutes: int = 120,
    ) -> None:
        """컨트롤러 초기화.

        Args:
            executor: TradeExecutor 인스턴스. close_position(symbol, reason,
                portion) 을 제공해야 한다.
            partial_tp_ratio: 분할 TP 시 청산할 포지션 비율 (0~1). 기본 0.5.
            partial_tp_trigger_pct: 분할 TP 트리거 지점 (entry→tp 경로 비율).
                기본 0.5 = 목표가 절반 지점.
            atr_trail_multiplier: 트레일링 SL 거리 = ATR × 이 배수. 기본 1.5.
            default_max_hold_minutes: max_hold_minutes 미지정 시 기본 시간 스톱.
        """
        if executor is None:
            logger.warning("[ExitPlan] executor 가 None — 청산 주문이 불가합니다")
        if not 0.0 < partial_tp_ratio < 1.0:
            raise ValueError(
                f"partial_tp_ratio must be in (0, 1), got {partial_tp_ratio}"
            )
        if not 0.0 < partial_tp_trigger_pct < 1.0:
            raise ValueError(
                f"partial_tp_trigger_pct must be in (0, 1), got {partial_tp_trigger_pct}"
            )
        self.executor = executor
        self.partial_tp_ratio = partial_tp_ratio
        self.partial_tp_trigger_pct = partial_tp_trigger_pct
        self.atr_trail_multiplier = atr_trail_multiplier
        self.default_max_hold_minutes = default_max_hold_minutes

        self._plans: dict[str, ExitPlan] = {}

    # ── 추적 등록/해제 ──────────────────────────────────────────

    def start_tracking(
        self,
        trade_id: int,
        decision: dict,
        quantity: float,
        opened_at: datetime | None = None,
        max_hold_minutes: int | None = None,
        sl_order_id: str | None = None,
        tp_order_id: str | None = None,
    ) -> ExitPlan:
        """진입한 포지션을 청산 추적 대상으로 등록한다.

        Args:
            trade_id: TradeExecutor.enter_trade() 가 반환한 거래 id.
            decision: 진입 결정 dict (symbol / action / entry_price / tp / sl).
            quantity: 진입 수량 (TradeExecutor.enter_trade() 결과의 quantity).
            opened_at: 진입 시각 (UTC). None 이면 현재 시각.
            max_hold_minutes: 시간 스톱 한도. None 이면 default_max_hold_minutes.
            sl_order_id: 거래소 STOP_MARKET 주문 id (BE/트레일 시 교체). live 전용.
            tp_order_id: 거래소 TAKE_PROFIT_MARKET 주문 id.

        Returns:
            등록된 ExitPlan. 같은 symbol 이 이미 추적 중이면 덮어쓴다.
        """
        symbol = decision["symbol"]
        action = decision["action"]
        entry = float(decision["entry_price"])
        tp = float(decision["tp"])
        sl = float(decision["sl"])
        opened = opened_at or datetime.now(timezone.utc)
        max_hold = max_hold_minutes or self.default_max_hold_minutes

        # 분할 TP 트리거 = entry→tp 경로의 trigger_pct 지점 (LONG/SHORT 모두 정합)
        partial_tp_price = entry + (tp - entry) * self.partial_tp_trigger_pct

        plan = ExitPlan(
            trade_id=trade_id,
            symbol=symbol,
            action=action,
            entry_price=entry,
            original_sl=sl,
            tp=tp,
            quantity=quantity,
            opened_at=opened,
            max_hold_minutes=max_hold,
            current_sl=sl,
            partial_tp_price=partial_tp_price,
            peak_price=entry,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )
        if symbol in self._plans:
            logger.warning("[ExitPlan] %s 기존 추적 덮어쓰기", symbol)
        self._plans[symbol] = plan
        logger.info(
            "[ExitPlan] %s 추적 시작 (trade_id=%d, %s entry=%s tp=%s sl=%s "
            "partial_tp=%s max_hold=%dm)",
            symbol, trade_id, action, entry, tp, sl, partial_tp_price, max_hold,
        )
        return plan

    def stop_tracking(self, symbol: str) -> None:
        """해당 symbol 의 청산 추적을 중단한다 (없으면 no-op)."""
        if self._plans.pop(symbol, None) is not None:
            logger.info("[ExitPlan] %s 추적 중단", symbol)

    def get_tracked(self) -> list[str]:
        """현재 추적 중인 symbol 목록."""
        return list(self._plans.keys())

    def get_plan(self, symbol: str) -> ExitPlan | None:
        """해당 symbol 의 ExitPlan 반환 (없으면 None)."""
        return self._plans.get(symbol)

    # ── 청산 조건 평가 ──────────────────────────────────────────

    async def update(
        self,
        symbol: str,
        current_price: float,
        current_atr: float | None = None,
        now: datetime | None = None,
    ) -> dict:
        """단일 추적 포지션의 청산 조건을 평가하고 필요한 청산을 실행한다.

        평가 우선순위: 시간 스톱 → SL 히트 → TP 히트 → 분할 TP → BE 이동 →
        ATR 트레일. 앞선 조건이 전량 청산을 유발하면 이후 단계는 건너뛴다.

        Args:
            symbol: 평가 대상 페어.
            current_price: 현재가.
            current_atr: 현재 ATR (절대값). None 이면 트레일링 스킵.
            now: 평가 기준 시각 (UTC). None 이면 현재 시각 (테스트 주입용).

        Returns:
            {"symbol", "tracked", "closed", "actions", "current_sl"}.
            tracked=False 면 추적 대상이 아님. closed=True 면 전량 청산되어
            추적이 해제됨.
        """
        plan = self._plans.get(symbol)
        if plan is None:
            return {"symbol": symbol, "tracked": False, "closed": False,
                    "actions": [], "current_sl": None}

        now = now or datetime.now(timezone.utc)
        actions: list[str] = []

        # ── ① 시간 스톱 ──
        held_minutes = (now - plan.opened_at).total_seconds() / 60.0
        if held_minutes >= plan.max_hold_minutes:
            return await self._full_close(plan, "time_stop", actions)

        # ── ② SL 히트 (current_sl 기준) ──
        if self._sl_hit(plan, current_price):
            reason = "trailing_stop" if plan.be_moved else "stop_loss"
            return await self._full_close(plan, reason, actions)

        # ── ③ TP 히트 (전량) ──
        if self._tp_hit(plan, current_price):
            return await self._full_close(plan, "take_profit", actions)

        # ── ④ 분할 TP ──
        if not plan.partial_tp_done and self._partial_tp_hit(plan, current_price):
            result = await self.executor.close_position(
                symbol, reason="partial_tp", portion=self.partial_tp_ratio
            )
            if result.get("success"):
                plan.partial_tp_done = True
                actions.append("partial_tp")
                logger.info("[ExitPlan] %s 분할 TP 체결 (portion=%.2f)",
                            symbol, self.partial_tp_ratio)
            else:
                logger.warning("[ExitPlan] %s 분할 TP 청산 실패: %s",
                               symbol, result.get("reason"))
                actions.append("partial_tp_failed")

        # ── ⑤ BE 이동 (분할 TP 후 1회) ──
        if plan.partial_tp_done and not plan.be_moved:
            plan.current_sl = plan.entry_price
            plan.be_moved = True
            actions.append("breakeven")
            logger.info("[ExitPlan] %s SL → break-even (%.8f)",
                        symbol, plan.entry_price)
            # 거래소 STOP 도 BE 로 교체 (소프트웨어 SL 과 동기 — 무방비 구간 없음)
            await self._sync_exchange_stop(plan)

        # ── ⑥ ATR 트레일 (분할 TP 후) ──
        if plan.partial_tp_done and current_atr is not None and current_atr > 0:
            if self._apply_trailing(plan, current_price, current_atr):
                actions.append("trail")
                # 트레일로 SL 이 이동했으면 거래소 STOP 도 cancel+replace
                await self._sync_exchange_stop(plan)

        return {"symbol": symbol, "tracked": True, "closed": False,
                "actions": actions, "current_sl": plan.current_sl}

    async def update_all(
        self,
        price_map: dict[str, float],
        atr_map: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> list[dict]:
        """추적 중인 모든 포지션의 청산 조건을 평가한다.

        Args:
            price_map: {symbol: 현재가}.
            atr_map: {symbol: 현재 ATR}. None 이면 트레일링 스킵.
            now: 평가 기준 시각 (UTC).

        Returns:
            각 symbol 의 update() 결과 리스트. price_map 에 가격이 없는
            symbol 은 스킵한다.
        """
        results: list[dict] = []
        for symbol in list(self._plans.keys()):
            price = price_map.get(symbol)
            if price is None:
                logger.warning("[ExitPlan] %s 현재가 없음 — 평가 스킵", symbol)
                continue
            atr = atr_map.get(symbol) if atr_map else None
            results.append(await self.update(symbol, price, atr, now))
        return results

    # ── 내부 헬퍼 ───────────────────────────────────────────────

    @staticmethod
    def _sl_hit(plan: ExitPlan, price: float) -> bool:
        """현재가가 current_sl 을 침범했는지."""
        if plan.action == "LONG":
            return price <= plan.current_sl
        return price >= plan.current_sl

    @staticmethod
    def _tp_hit(plan: ExitPlan, price: float) -> bool:
        """현재가가 최종 tp 에 도달했는지."""
        if plan.action == "LONG":
            return price >= plan.tp
        return price <= plan.tp

    @staticmethod
    def _partial_tp_hit(plan: ExitPlan, price: float) -> bool:
        """현재가가 분할 TP 트리거 가격에 도달했는지."""
        if plan.action == "LONG":
            return price >= plan.partial_tp_price
        return price <= plan.partial_tp_price

    def _apply_trailing(
        self, plan: ExitPlan, price: float, atr: float
    ) -> bool:
        """ATR 트레일링 — peak 갱신 후 SL 을 유리한 방향으로만 이동한다.

        Returns:
            current_sl 이 실제로 이동했으면 True.
        """
        distance = atr * self.atr_trail_multiplier
        if plan.action == "LONG":
            plan.peak_price = max(plan.peak_price, price)
            trail_sl = plan.peak_price - distance
            if trail_sl > plan.current_sl:
                plan.current_sl = trail_sl
                logger.info("[ExitPlan] %s 트레일 SL ↑ %.8f", plan.symbol, trail_sl)
                return True
        else:  # SHORT
            plan.peak_price = min(plan.peak_price, price)
            trail_sl = plan.peak_price + distance
            if trail_sl < plan.current_sl:
                plan.current_sl = trail_sl
                logger.info("[ExitPlan] %s 트레일 SL ↓ %.8f", plan.symbol, trail_sl)
                return True
        return False

    async def _sync_exchange_stop(self, plan: ExitPlan) -> None:
        """current_sl 변경 시 거래소 STOP 을 cancel+replace 한다(BE/트레일).

        거래소 STOP 가 하드 플로어이므로 소프트웨어 SL 이동과 동기화한다.
        실패해도 소프트웨어 SL 평가는 계속되므로 무방비가 아니며, 다음 루프에
        재시도된다. dry_run 은 executor.replace_stop_order 가 no-op.
        """
        if self.executor is None or not hasattr(self.executor, "replace_stop_order"):
            return
        try:
            new_id = await self.executor.replace_stop_order(
                plan.symbol, plan.sl_order_id, plan.action, plan.current_sl
            )
            if new_id is not None:
                plan.sl_order_id = new_id
        except Exception as e:  # noqa: BLE001 — STOP 갱신 실패는 비치명적
            logger.warning("[ExitPlan] %s 거래소 STOP 동기화 실패: %s", plan.symbol, e)

    async def _full_close(
        self, plan: ExitPlan, reason: str, actions: list[str]
    ) -> dict:
        """전량 청산을 실행한다. 성공 시 추적 해제, 실패 시 추적 유지(재시도).

        Returns:
            update() 와 동일한 형식의 결과 dict.
        """
        result = await self.executor.close_position(plan.symbol, reason=reason)
        if result.get("success"):
            self._plans.pop(plan.symbol, None)
            actions.append(reason)
            logger.info("[ExitPlan] %s 전량 청산 (%s) — 추적 해제", plan.symbol, reason)
            return {"symbol": plan.symbol, "tracked": True, "closed": True,
                    "actions": actions, "current_sl": plan.current_sl}
        # 청산 실패 — 추적 유지하여 다음 루프 재시도
        logger.warning(
            "[ExitPlan] %s 전량 청산 실패 (%s): %s — 추적 유지, 재시도",
            plan.symbol, reason, result.get("reason"),
        )
        actions.append(f"{reason}_failed")
        return {"symbol": plan.symbol, "tracked": True, "closed": False,
                "actions": actions, "current_sl": plan.current_sl}
