"""
trading/executor.py
=====================================================================
TradeExecutor — 진입 주문 실행(체결 확인) + 거래소 보호 주문 + 거래 기록

근거:
  - docs/SPEC_v3.1.md §8-8 (Layer 5), §8-8-2 (Post-Only Limit)
  - docs/SPEC_v3.1_APPENDIX_E.md §E-5-2 / §E-7-1 (자본 상태 컬럼 INSERT)

v3.1.2 감사 후속 (실거래 중단급 수정):
  - C2: 진입은 "주문 접수"가 아니라 **FILLED 확인** 후에만 성공 처리한다.
    newClientOrderId 멱등 + futures_get_order 폴링. 타임아웃 시 cancel.
  - C1: 진입 체결 직후 거래소 closePosition STOP_MARKET(필수) +
    TAKE_PROFIT_MARKET(옵션)를 생성한다. 손절 주문 실패 시 즉시 시장가 청산.
  - H1: 체결·보호주문이 모두 성공한 뒤에만 trades 행을 INSERT 한다(유령 행 방지).
  - H2: newClientOrderId 로 멱등성 확보.
  - M1: 청산 시 실제 체결가/수수료로 PnL 을 정확히 기록한다(avgPrice 누락 시
    futures_account_trades 로 보정).
  - M3: 수량/가격을 exchangeInfo 필터(LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL)로
    정규화한다. 필터 조회 실패 시 live 주문을 차단한다.

v3.1.2 추가 (2025-12-09 Binance Algo Service 전환):
  - 보호 주문(STOP_MARKET / TAKE_PROFIT_MARKET / TRAILING_STOP_MARKET)은
    2025-12-09 부터 POST /fapi/v1/algoOrder 로만 받는다(-4120 STOP_ORDER_SWITCH_ALGO).
    → 진입 후 SL/TP 는 futures_create_algo_order(algoType="CONDITIONAL", ...) 로
       생성하고, 취소는 futures_cancel_algo_order(algoId|clientAlgoId), 조회는
       futures_get_algo_order 를 사용한다.
  - 진입 LIMIT(GTX)은 기존 futures_create_order / futures_cancel_order 그대로 유지.
  - algoOrder 페이로드 가드:
      closePosition="true" 사용 → 반드시 quantity 와 reduceOnly 미전송
      (요구 [2]: 두 파라미터 동시 전송 시 Binance 가 거부).
  - DB(sl_order_id / tp_order_id / entry_order_id)는 **문자열**로 저장.
    algoId(정수)와 clientAlgoId(문자열) 모두 수용. cancel 시 isdigit() 로 분기.

설계 메모:
  - dry_run=True: 어떤 Binance 주문/조회 API 도 호출하지 않는다(페이퍼).
    체결가=entry_price 로 가정하고 정규화·보호주문은 생략, trades INSERT 만 수행.
  - 거래소·DB 동기 호출은 asyncio.to_thread 로 래핑(메인 루프 비차단).
  - 보호 주문은 closePosition="true" 로 생성 → 부분 청산 후에도 잔여 포지션을
    자동 전량 보호. SL 이 하드 플로어, ExitPlanController 는 분할 TP/트레일/
    시간 스톱 + SL 갱신을 담당한다.
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from governance.kill_switch import KillSwitch

logger = logging.getLogger(__name__)

# enter_trade(decision) 필수 키 (boundary 검증)
_REQUIRED_DECISION_KEYS = ("symbol", "action", "entry_price", "tp", "sl", "size_usdt")

# exchangeInfo 필터 미적용 dry_run 폴백 수량 반올림 자리
_DRYRUN_QTY_PRECISION = 3

# 수수료 추정용 기본 taker 율(실제 체결 수수료를 못 얻을 때만 사용 — CostGuard 기본값과 동일)
_DEFAULT_TAKER_FEE = 0.00045
# A4 [2.4]: 진입은 GTX(post-only)=maker 이므로 진입 수수료 추정엔 maker 율을 쓴다.
_DEFAULT_MAKER_FEE = 0.00018
# A4 [2.5]: 청산 체결가를 못 얻어 STOP가/진입가로 합성 처리한 행 표시(통계 제외용).
_STOP_FALLBACK_FLAG = "_stop_fallback"

# 진입 주문 미체결로 간주하지 않는 상태
_TERMINAL_FAIL_STATES = {"CANCELED", "EXPIRED", "REJECTED"}


@dataclass
class Position:
    """열린 포지션 1건.

    Attributes:
        symbol: 거래 페어.
        position_amt: 포지션 수량 (LONG 양수 / SHORT 음수).
        entry_price: 평균 진입가.
        unrealized_pnl: 미실현 손익 (USDT). dry_run 은 0.0.
        side: "LONG" / "SHORT".
    """

    symbol: str
    position_amt: float
    entry_price: float
    unrealized_pnl: float
    side: str


class TradeExecutor:
    """진입(체결 확인) + 거래소 보호 주문 + 거래 기록기.

    사용:
        executor = TradeExecutor(binance_client, db_path="data/bot.db")
        result = await executor.enter_trade(decision)
        if result["success"]:
            track(result["trade_id"], result["sl_order_id"], result["tp_order_id"])
        elif result["critical"]:
            alert_operator(result["reason"])   # 보호주문 실패 → 강제청산됨
    """

    def __init__(
        self,
        binance_client,
        db_path: str,
        dry_run: bool = False,
        fill_timeout_s: float = 15.0,
        fill_poll_interval_s: float = 1.0,
        exchange_info_ttl_s: float = 3600.0,
        place_take_profit: bool = True,
        working_type: str = "MARK_PRICE",
        price_protect: bool = True,
        block_hedge_mode: bool = True,
        protected_symbols: list[str] | None = None,
    ) -> None:
        """실행기 초기화.

        Args:
            binance_client: python-binance Client. dry_run=True 면 None 허용.
            db_path: sqlite3 DB 파일 경로 (trades 테이블).
            dry_run: True 면 거래소 호출을 스킵하고 DB 기록만 수행 (페이퍼).
            fill_timeout_s: 진입 주문 FILLED 대기 한도(초). 초과 시 cancel.
            fill_poll_interval_s: futures_get_order 폴링 주기(초).
            exchange_info_ttl_s: 심볼 필터 캐시 TTL(초).
            place_take_profit: True 면 closePosition TAKE_PROFIT_MARKET 도 생성.
            working_type: 보호 주문 트리거 기준가 ("MARK_PRICE" / "CONTRACT_PRICE").
                MARK_PRICE 권장 — wick stop hunt 방지 (운영 결정 사항).
            price_protect: True 면 algoOrder 의 priceProtect="true" 로 전송한다
                (Binance 공식 명세: STRING "true"/"false" 소문자).
            block_hedge_mode: True 면 계정이 Hedge Mode(dualSidePosition=True)일 때
                live 신규 진입을 차단한다(A-4). 이 봇은 One-way Mode 전제로 설계됐고
                Hedge Mode 를 지원하지 않으므로 기본 True. position mode 조회는 첫
                live 진입 시 1회 수행 후 캐시한다.
            protected_symbols: 보호종목 목록(Protected Existing Position Coexist Mode).
                close_position / reconcile 가 이 종목들의 기존 보유분을 절대 청산·
                취소하지 않도록 guard 한다. 봇은 이 종목을 신규 진입도 하지 않는다
                (진입 차단은 PairWhitelist 책임). None 이면 빈 집합(가드 비활성).
        """
        if not db_path:
            logger.warning("[Executor] db_path 가 비어 있음 — DB 기록이 실패합니다")
        if binance_client is None and not dry_run:
            logger.warning(
                "[Executor] binance_client=None 인데 dry_run=False — "
                "enter_trade 가 항상 실패합니다"
            )
        if working_type not in ("MARK_PRICE", "CONTRACT_PRICE"):
            logger.warning(
                "[Executor] working_type=%r 비표준 — Binance 기본(CONTRACT_PRICE) "
                "으로 대체될 수 있음",
                working_type,
            )
        self.binance = binance_client
        self.db_path = db_path
        self.dry_run = dry_run
        self.fill_timeout_s = fill_timeout_s
        self.fill_poll_interval_s = fill_poll_interval_s
        self.exchange_info_ttl_s = exchange_info_ttl_s
        self.place_take_profit = place_take_profit
        self.working_type = working_type
        self.price_protect = price_protect
        self.block_hedge_mode = block_hedge_mode
        # Protected Existing Position Coexist Mode — 기존 보호종목 보유분 보존
        self._protected_symbols: set[str] = set(protected_symbols or [])

        # exchangeInfo 심볼 필터 캐시 (M3)
        self._filters: dict[str, dict] = {}
        self._filters_loaded_at: float = 0.0

        # position mode 캐시 (A-4) — 첫 live 진입 시 1회 확인 후 재사용
        self._position_mode_checked: bool = False
        self._is_hedge_mode: bool | None = None

    # ── 진입 ────────────────────────────────────────────────────

    @staticmethod
    def _live_trading_authorized() -> bool:
        """A5 [2.10] defense-in-depth: 실거래(live) 진입 인가 여부 (env 직접 확인).

        testnet(USE_TESTNET=true) 은 안전하므로 허용. mainnet 은 명시적
        LIVE_TRADING_ENABLED=true 일 때만 허용. main 게이트 우회 시의 최후 보루.
        """
        if os.environ.get("USE_TESTNET", "").strip().lower() in ("1", "true", "yes"):
            return True
        return os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower() in (
            "1", "true", "yes",
        )

    async def enter_trade(self, decision: dict) -> dict:
        """진입 주문을 실행하고, 체결 + 보호주문 성공 시에만 기록한다.

        live 흐름 (감사 C1/C2/H1/H2/M3):
            입력검증 → 필터 정규화(실패 차단) → GTX 진입 + FILLED 확인(미체결
            차단) → 거래소 STOP/TP 생성(STOP 실패 시 즉시 강제청산) →
            trades INSERT(실패 시 보호주문 cancel + 포지션 청산).

        Returns:
            {"success", "trade_id", "symbol", "quantity", "entry_price_actual",
             "sl_order_id", "tp_order_id", "critical", "decision", "reason"}.
            critical=True 는 보호주문 실패로 방금 연 포지션을 강제청산했음을 의미
            (운영자 즉시 확인 필요).
        """
        symbol = decision.get("symbol", "?")

        missing = [k for k in _REQUIRED_DECISION_KEYS if k not in decision]
        if missing:
            logger.error("[Executor] %s decision 필수 키 누락: %s", symbol, missing)
            return self._fail(symbol, decision, f"필수 키 누락: {missing}")

        action = decision["action"]
        entry_price = float(decision["entry_price"])
        size_usdt = float(decision["size_usdt"])
        sl = float(decision["sl"])
        tp = float(decision["tp"])
        leverage = int(decision.get("leverage", 1))

        if action not in ("LONG", "SHORT"):
            logger.error("[Executor] %s 잘못된 action=%r", symbol, action)
            return self._fail(symbol, decision, f"잘못된 action: {action}")
        if entry_price <= 0 or size_usdt <= 0:
            logger.error(
                "[Executor] %s 비정상 입력 entry_price=%s size_usdt=%s",
                symbol, entry_price, size_usdt,
            )
            return self._fail(symbol, decision, "entry_price/size_usdt <= 0")

        # ── dry_run: 거래소 호출 없이 페이퍼 기록만 ──
        if self.dry_run:
            quantity = round(size_usdt / entry_price, _DRYRUN_QTY_PRECISION)
            if quantity <= 0:
                return self._fail(symbol, decision, "계산된 수량이 0")
            logger.info(
                "[Executor] [DRY-RUN] %s %s qty=%s @ %s (lev=%dx) — 주문/보호주문 스킵",
                symbol, action, quantity, entry_price, leverage,
            )
            return await self._record_and_return(
                decision, quantity, leverage, entry_price,
                entry_order_id=None, sl_order_id=None, tp_order_id=None,
            )

        # ── live: A5 [2.10] defense-in-depth — 미인가 실거래 진입 차단 ──
        # main 의 안전 게이트(_resolve_effective_dry_run)가 우회되어 dry_run=False
        # 로 실거래 경로에 진입한 경우를 대비한 독립 검사(env 직접 확인). testnet
        # 또는 LIVE_TRADING_ENABLED=true 가 아니면 신규 진입 실주문을 거부한다.
        if not self._live_trading_authorized():
            logger.critical(
                "[Executor] %s live 진입 미인가 → 차단 "
                "(USE_TESTNET≠true & LIVE_TRADING_ENABLED≠true, A5)", symbol,
            )
            return self._fail(
                symbol, decision, "live_trading_not_authorized", critical=True,
            )

        # ── live: 끄는 선 (S1) — KillSwitch 방어심층(파일 기반) ──
        # 메인루프 _iter 가 이미 신규진입 전 차단하나, 우회·버그 대비 *돈 나가는
        # 지점 자체*가 거부한다. fail-closed: 상태 조회 실패 시에도 차단(안전 우선).
        # btc_state 는 메인루프가 평가하므로 여기선 파일 기반(수동/daily_loss 자동)만 확인.
        try:
            ks_active = KillSwitch.is_active()
        except Exception as e:  # noqa: BLE001 — 조회 실패 = 차단(fail-closed)
            logger.critical(
                "[Executor] %s KillSwitch 조회 실패 → fail-closed 차단: %s", symbol, e,
            )
            return self._fail(
                symbol, decision, "killswitch_check_failed", critical=True,
            )
        if ks_active:
            logger.critical("[Executor] %s KillSwitch 활성 → 진입 차단 (S1 방어심층)", symbol)
            return self._fail(symbol, decision, "killswitch_active", critical=True)

        # ── live: Probe 하드실링 (방어심층) — 메인루프 우회/버그에도 소액 보장 ──
        # decision.budget_cap 존재(probe 모드) & size_usdt 가 예산 초과면 차단(게이트만).
        _budget_cap = decision.get("budget_cap")
        if _budget_cap is not None and size_usdt > float(_budget_cap) * 1.001:
            logger.critical(
                "[Executor] %s size $%.2f > probe budget $%.2f → 차단(하드실링)",
                symbol, size_usdt, float(_budget_cap),
            )
            return self._fail(symbol, decision, "size_exceeds_probe_budget", critical=True)

        # ── live: Hedge Mode 차단 (A-4) ──
        # One-way Mode 전제. Hedge Mode 면 positionSide 누락/오매칭 위험이 있어
        # 신규 진입을 막는다. 조회 실패도 live 에서는 fail-closed.
        ok, hedge_reason = await self._ensure_one_way_mode()
        if not ok:
            logger.critical("[Executor] %s 진입 차단: %s", symbol, hedge_reason)
            return self._fail(symbol, decision, hedge_reason, critical=True)

        # ── live: exchangeInfo 필터 정규화 (M3) ──
        norm_qty, norm_price, reason = await self._normalize(
            symbol, size_usdt / entry_price, entry_price
        )
        if reason is not None:
            logger.error("[Executor] %s 정규화 차단: %s", symbol, reason)
            return self._fail(symbol, decision, reason)

        # ── live: 진입 + FILLED 확인 (C2/H2) ──
        # B-3 계측(fail-soft): 주문 전송 시각(근사 — leverage 설정 직전) + 요청 limit가(norm_price)를
        # decision 에 기록(체결→trades / 미체결→unfilled_signals 공통 기준). 거래 흐름 무영향.
        decision["entry_limit_price"] = norm_price
        _order_send_ts = datetime.now(timezone.utc).isoformat()
        decision["entry_order_send_ts"] = _order_send_ts
        decision["order_send_ts"] = _order_send_ts
        client_order_id = self._gen_client_order_id(symbol)
        confirm = await self._place_entry_and_confirm(
            symbol, action, norm_qty, norm_price, leverage, client_order_id
        )
        if not confirm["filled"]:
            # B7 [4-4]: 미체결/스킵 신호를 폐기하지 말고 영속화(forward-return 분석용).
            # 계측은 거래 경로와 격리 — 실패해도 진입-스킵 흐름에 영향 0 (로그만).
            self._record_unfilled_signal(decision, confirm.get("reason", "unknown"))
            # A-5: 조회 실패/타임아웃이 고아 포지션을 만들었을 수 있다 → critical 전파.
            # _await_fill 이 이미 강제청산했거나(orphan), 포지션 조회 실패로 fail-closed
            # 한 경우 critical=True 로 main 에서 즉시 Telegram 경고가 나가도록 한다.
            return self._fail(
                symbol, decision, confirm["reason"],
                critical=confirm.get("critical", False),
            )

        filled_qty = confirm["filled_qty"]
        avg_price = confirm["avg_price"] or norm_price

        # ── live: 거래소 보호 주문 (C1) ──
        sl_order_id, tp_order_id = await self._place_protective_orders(
            symbol, action, filled_qty, sl, tp
        )
        if sl_order_id is None:
            # 손절 주문 실패 → 무방비 포지션 → 즉시 강제 청산 (요구 7)
            logger.critical(
                "[Executor] %s 손절 주문 생성 실패 → 포지션 즉시 강제청산", symbol
            )
            await self._force_close(symbol, action, filled_qty)
            return self._fail(
                symbol, decision,
                "손절 주문 생성 실패 → 포지션 강제청산됨", critical=True,
            )

        # ── live: 체결 + 보호주문 성공 후에만 기록 (H1) ──
        result = await self._record_and_return(
            decision, filled_qty, leverage, avg_price,
            entry_order_id=client_order_id,
            sl_order_id=sl_order_id, tp_order_id=tp_order_id,
        )
        if not result["success"]:
            # DB 기록 실패 → 보호주문 cancel + 포지션 청산(고아 방지) (요구 8)
            # 2025-12-09 전환: 보호주문은 algo 엔드포인트로 생성됐으므로 cancel 도 algo.
            logger.critical(
                "[Executor] %s trades 기록 실패 → 보호주문 cancel + 포지션 청산", symbol
            )
            await self._cancel_algo_order_safe(symbol, sl_order_id)
            await self._cancel_algo_order_safe(symbol, tp_order_id)
            await self._force_close(symbol, action, filled_qty)
            result["critical"] = True
        return result

    async def _record_and_return(
        self, decision: dict, quantity: float, leverage: int, entry_price: float,
        entry_order_id: str | None, sl_order_id: str | None, tp_order_id: str | None,
    ) -> dict:
        """trades 행을 INSERT 하고 표준 결과 dict 를 반환한다."""
        symbol = decision["symbol"]
        try:
            trade_id = await asyncio.to_thread(
                self._insert_trade, decision, quantity, leverage, entry_price,
                entry_order_id, sl_order_id, tp_order_id,
            )
        except Exception as e:
            logger.error("[Executor] %s trades INSERT 실패: %s", symbol, e)
            return {
                "success": False, "trade_id": None, "symbol": symbol,
                "quantity": quantity, "entry_price_actual": entry_price,
                "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
                "critical": False, "decision": decision, "reason": f"DB 기록 실패: {e}",
            }
        logger.info(
            "[Executor] %s %s 진입 기록 완료 (trade_id=%d, qty=%s, entry=%s, %s)",
            symbol, decision["action"], trade_id, quantity, entry_price,
            "DRY-RUN" if self.dry_run else "LIVE",
        )
        return {
            "success": True, "trade_id": trade_id, "symbol": symbol,
            "quantity": quantity, "entry_price_actual": entry_price,
            "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
            "critical": False, "decision": decision, "reason": "ok",
        }

    async def _place_entry_and_confirm(
        self, symbol: str, action: str, quantity: float,
        entry_price: float, leverage: int, client_order_id: str,
    ) -> dict:
        """레버리지 설정 → GTX 진입 전송 → FILLED 확인 (감사 C2/H2).

        Returns:
            {"filled": bool, "filled_qty": float, "avg_price": float,
             "order_id": int|None, "reason": str}.
            미체결/거부/타임아웃은 filled=False. 타임아웃 시 잔여 주문은 cancel.
        """
        side = "BUY" if action == "LONG" else "SELL"
        try:
            await asyncio.to_thread(
                self.binance.futures_change_leverage,
                symbol=symbol, leverage=leverage,
            )
            order = await asyncio.to_thread(
                self.binance.futures_create_order,
                symbol=symbol,
                side=side,
                type="LIMIT",
                timeInForce="GTX",                  # Post-Only (Good Till Crossing)
                quantity=quantity,
                price=entry_price,
                newClientOrderId=client_order_id,    # 멱등 (H2)
            )
        except Exception as e:
            logger.error("[Executor] %s 진입 주문 전송 실패: %s", symbol, e)
            # 주문 전송 자체가 실패 → 포지션 생성 가능성 없음 → critical 아님.
            return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                    "order_id": None, "critical": False,
                    "reason": f"진입 주문 전송 실패: {e}"}

        order_id = order.get("orderId") if isinstance(order, dict) else None
        logger.info(
            "[Executor] %s GTX %s qty=%s @ %s (lev=%dx) 전송 — FILLED 대기",
            symbol, side, quantity, entry_price, leverage,
        )
        return await self._await_fill(symbol, order_id, order, action)

    async def _await_fill(
        self, symbol: str, order_id, last_order: dict, action: str
    ) -> dict:
        """진입 주문이 FILLED 될 때까지 폴링한다. 타임아웃/조회 실패 시 안전 해소.

        모든 반환 dict 는 "critical" 키를 포함한다(A-5).
        부분체결 단순화: 타임아웃 시 잔량은 cancel 하되 executedQty>0 이면
        체결분으로 진행한다(감사 H1 — 미체결 추적/유령 행 방지).
        조회 실패로 체결을 확인할 수 없을 때는 _resolve_fill_timeout 이
        실제 포지션을 확인해 고아 포지션을 방지한다(A-5).
        """
        deadline = time.monotonic() + self.fill_timeout_s
        while True:
            cur, _query_ok = await self._safe_get_order(symbol, order_id)
            if cur is None:
                cur = last_order
            status = (cur or {}).get("status", "")
            if status == "FILLED":
                eq, avg = self._extract_fill(cur)
                logger.info("[Executor] %s 진입 FILLED qty=%s avg=%s", symbol, eq, avg)
                return {"filled": True, "filled_qty": eq, "avg_price": avg,
                        "order_id": order_id, "critical": False, "reason": "filled"}
            if status in _TERMINAL_FAIL_STATES:
                logger.warning("[Executor] %s 진입 주문 %s — 진입 스킵", symbol, status)
                return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                        "order_id": order_id, "critical": False,
                        "reason": f"주문 {status}"}
            if time.monotonic() >= deadline:
                return await self._resolve_fill_timeout(
                    symbol, order_id, action, cur
                )
            await asyncio.sleep(self.fill_poll_interval_s)

    async def _ensure_one_way_mode(self) -> tuple[bool, str | None]:
        """계정이 One-way Mode 인지 확인한다(A-4 — Hedge Mode 신규 진입 차단).

        - dry_run: 거래소 호출 금지 → 항상 통과(True, None).
        - block_hedge_mode=False: 검사 비활성 → 통과.
        - 이미 확인됨: 캐시 사용(Hedge 면 차단, One-way 면 통과).
        - 미확인: futures_get_position_mode 1회 호출.
            dualSidePosition=True → 차단("hedge_mode_blocked").
            조회 실패 → live fail-closed("position_mode_check_failed").
              (실패는 캐시하지 않아 다음 진입에서 재시도)

        Returns:
            (ok, reason). ok=False 면 reason 이 차단 사유. 봇은 Hedge Mode 를
            지원하지 않으며 이 메서드는 차단만 수행한다.
        """
        if self.dry_run or not self.block_hedge_mode:
            return True, None
        if self._position_mode_checked:
            if self._is_hedge_mode:
                return False, "hedge_mode_blocked"
            return True, None
        try:
            mode = await asyncio.to_thread(self.binance.futures_get_position_mode)
        except Exception as e:
            # live fail-closed — 캐시하지 않고 다음 진입에서 재시도
            logger.error(
                "[Executor] position mode 조회 실패: %s — live 진입 차단(fail-closed)", e
            )
            return False, "position_mode_check_failed"
        dual = bool(mode.get("dualSidePosition")) if isinstance(mode, dict) else False
        self._is_hedge_mode = dual
        self._position_mode_checked = True
        if dual:
            logger.critical(
                "[Executor] 계정이 Hedge Mode(dualSidePosition=True) — "
                "이 봇은 One-way 전용이므로 신규 진입을 차단합니다"
            )
            return False, "hedge_mode_blocked"
        logger.info("[Executor] position mode = One-way (정상)")
        return True, None

    async def _safe_get_order(self, symbol: str, order_id) -> tuple[dict | None, bool]:
        """futures_get_order 를 안전하게 호출한다.

        Returns:
            (order_dict, ok). 조회 성공이면 (dict, True), 실패/비dict 면 (None, False).
        """
        try:
            o = await asyncio.to_thread(
                self.binance.futures_get_order, symbol=symbol, orderId=order_id
            )
        except Exception as e:
            logger.warning("[Executor] %s 주문 상태 조회 실패: %s", symbol, e)
            return None, False
        return (o, True) if isinstance(o, dict) else (None, False)

    async def _resolve_fill_timeout(
        self, symbol: str, order_id, action: str, last_cur: dict | None
    ) -> dict:
        """타임아웃 시점의 체결 여부를 해소한다(A-5 — 고아 포지션 방지).

        흐름:
            1) 최종 재조회 — FILLED 면 정상 진입으로 복구.
            2) 잔여 주문 cancel(best-effort).
            3) cancel 후 재조회 — 그 사이 FILLED 됐으면 복구.
            4) 조회가 신뢰 가능(성공)하고 executedQty>0 → 기존 부분체결 정책 유지.
            5) 조회가 신뢰 가능하고 executedQty==0 → 깔끔한 미체결(포지션 확인 불필요).
            6) 조회가 끝까지 불신뢰(실패) → futures_position_information 로 실제 포지션
               확인:
                 - 조회 실패 → critical=True (fail-closed).
                 - 포지션 존재 → _force_close + critical=True (고아 방지).
                 - 포지션 없음 → 미체결.
        """
        # 1) 최종 재조회 (다른 시점 — 일시적 네트워크/레이트리밋 회복 가능)
        final, final_ok = await self._safe_get_order(symbol, order_id)
        if final_ok and final.get("status") == "FILLED":
            eq, avg = self._extract_fill(final)
            logger.warning(
                "[Executor] %s 최종 재조회에서 FILLED 확인 — 정상 진입(qty=%s)", symbol, eq
            )
            return {"filled": True, "filled_qty": eq, "avg_price": avg,
                    "order_id": order_id, "critical": False,
                    "reason": "filled_on_final_requery"}

        # 2) 잔여 주문 cancel (best-effort — 이미 체결됐다면 실패할 수 있음)
        await self._cancel_order_safe(symbol, order_id)

        # 3) cancel 후 재조회 (cancel 실패/이미 체결 케이스 포착)
        post, post_ok = await self._safe_get_order(symbol, order_id)
        if post_ok and post.get("status") == "FILLED":
            eq, avg = self._extract_fill(post)
            logger.warning(
                "[Executor] %s cancel 후 재조회에서 FILLED 확인 — 정상 진입(qty=%s)",
                symbol, eq,
            )
            return {"filled": True, "filled_qty": eq, "avg_price": avg,
                    "order_id": order_id, "critical": False,
                    "reason": "filled_after_cancel_attempt"}

        # 신뢰 가능한 최신 스냅샷 선택
        reliable = post_ok or final_ok
        snap = post if post_ok else (final if final_ok else last_cur)
        eq, avg = self._extract_fill(snap if isinstance(snap, dict) else {})

        # 4) 신뢰 가능 + 부분체결 → 기존 정책 유지(체결분 진행)
        if reliable and eq > 0:
            logger.warning(
                "[Executor] %s 타임아웃 — 부분체결 qty=%s 로 진행 (잔량 cancel)", symbol, eq
            )
            return {"filled": True, "filled_qty": eq, "avg_price": avg,
                    "order_id": order_id, "critical": False,
                    "reason": "partial_fill_timeout"}

        # 5) 신뢰 가능 + 체결 0 → 깔끔한 미체결 (실포지션 없음 — 확인 불필요)
        if reliable and eq <= 0:
            logger.warning("[Executor] %s 타임아웃 미체결 → cancel + 진입 스킵", symbol)
            return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                    "order_id": order_id, "critical": False, "reason": "fill_timeout"}

        # 6) 조회 불신뢰 → 실제 포지션 확인으로 고아 포지션 방지 (A-5 핵심)
        has_pos, pos_amt, check_ok = await self._position_state(symbol)
        if not check_ok:
            logger.critical(
                "[Executor] %s 체결 불명 + 포지션 조회 실패 → critical (fail-closed)", symbol
            )
            return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                    "order_id": order_id, "critical": True,
                    "reason": "fill_unknown_position_check_failed"}
        if has_pos:
            # 봇은 미체결로 판단했으나 실제 포지션이 존재 → DB/STOP 추적 없는 고아.
            # 보수적으로 즉시 강제청산 + critical (DB 복구보다 위험 축소 우선).
            # A-4 요구 11: 실제 position_amt 방향과 진입 action 불일치 여부를 로그로
            # 명확히 남긴다(Hedge Mode 는 A-4 에서 차단되므로 실질 위험은 낮으나,
            # _force_close 는 action 기준 close_side 라 방향 점검을 기록한다).
            expected_long = (action == "LONG")
            actual_long = (pos_amt > 0)
            if expected_long != actual_long:
                logger.critical(
                    "[Executor] %s 포지션 방향 불일치 — action=%s 인데 position_amt=%s "
                    "(_force_close 는 action 기준 청산; 운영자 거래소 확인 필요)",
                    symbol, action, pos_amt,
                )
            logger.critical(
                "[Executor] %s 미체결 판단했으나 실제 포지션 존재(amt=%s, action=%s) → 강제청산",
                symbol, pos_amt, action,
            )
            await self._force_close(symbol, action, abs(pos_amt))
            return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                    "order_id": order_id, "critical": True,
                    "reason": "orphan_position_force_closed"}
        logger.warning(
            "[Executor] %s 조회 불명이나 실포지션 없음 → 미체결 처리", symbol
        )
        return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                "order_id": order_id, "critical": False,
                "reason": "fill_timeout_no_position"}

    async def _position_state(self, symbol: str) -> tuple[bool, float, bool]:
        """심볼의 실제 포지션 존재 여부를 확인한다(A-5).

        get_open_positions 는 조회 실패 시 [](빈 목록)을 반환해 "포지션 없음"과
        "조회 실패"를 구분할 수 없으므로, 여기서는 futures_position_information 을
        직접 호출해 실패를 명시적으로 구분한다(fail-closed 판단용).

        Returns:
            (has_position, position_amt, check_ok).
            check_ok=False 면 조회 자체가 실패한 것(호출부가 fail-closed 처리).
        """
        try:
            raw = await asyncio.to_thread(
                self.binance.futures_position_information, symbol=symbol
            )
        except Exception as e:
            logger.error("[Executor] %s 포지션 확인 실패: %s", symbol, e)
            return False, 0.0, False
        if raw is None:
            return False, 0.0, False
        for p in raw:
            try:
                if p.get("symbol") == symbol:
                    amt = float(p.get("positionAmt", 0) or 0)
                    if amt != 0:
                        return True, amt, True
            except (ValueError, TypeError, AttributeError):
                continue
        return False, 0.0, True

    async def _place_protective_orders(
        self, symbol: str, action: str, filled_qty: float, sl: float, tp: float,
    ) -> tuple[str | None, str | None]:
        """진입 체결 직후 closePosition STOP_MARKET(+TAKE_PROFIT_MARKET)을 algoOrder
        엔드포인트로 생성한다 (2025-12-09 Binance Algo Service 전환 대응).

        closePosition="true" 로 생성하여 부분 청산 후에도 잔여 전량을 보호한다.
        (가드: quantity 와 reduceOnly 는 절대 전송 금지 — _build_algo_payload 강제)

        Returns:
            (sl_order_id, tp_order_id) 모두 문자열. STOP 실패 시 sl_order_id=None
            (호출부가 강제청산). TP 실패는 비치명적(SL 이 하드 플로어)이라
            tp_order_id=None 로 두고 진행한다.
        """
        close_side = "SELL" if action == "LONG" else "BUY"

        sl_client_id = self._gen_client_algo_id(symbol, "sl")
        sl_payload = self._build_algo_payload(
            symbol=symbol, side=close_side, order_type="STOP_MARKET",
            trigger_price=sl, client_algo_id=sl_client_id,
        )
        try:
            stop = await asyncio.to_thread(
                self.binance.futures_create_algo_order, **sl_payload
            )
            sl_order_id = self._extract_algo_id(stop) or sl_client_id
            logger.info(
                "[Executor] %s algo STOP_MARKET 등록 (trigger=%s, id=%s, workingType=%s)",
                symbol, sl, sl_order_id, self.working_type,
            )
        except Exception as e:
            logger.error("[Executor] %s algo STOP_MARKET 생성 실패: %s", symbol, e)
            return None, None

        tp_order_id: str | None = None
        if self.place_take_profit:
            tp_client_id = self._gen_client_algo_id(symbol, "tp")
            tp_payload = self._build_algo_payload(
                symbol=symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
                trigger_price=tp, client_algo_id=tp_client_id,
            )
            try:
                tp_o = await asyncio.to_thread(
                    self.binance.futures_create_algo_order, **tp_payload
                )
                tp_order_id = self._extract_algo_id(tp_o) or tp_client_id
                logger.info(
                    "[Executor] %s algo TAKE_PROFIT_MARKET 등록 (trigger=%s, id=%s)",
                    symbol, tp, tp_order_id,
                )
            except Exception as e:
                # TP 실패는 비치명적 — SL 이 이미 포지션을 보호한다
                logger.warning(
                    "[Executor] %s algo TAKE_PROFIT_MARKET 생성 실패(비치명적): %s",
                    symbol, e,
                )
        return sl_order_id, tp_order_id

    def _build_algo_payload(
        self, *, symbol: str, side: str, order_type: str,
        trigger_price: float, client_algo_id: str,
    ) -> dict:
        """algoOrder payload 빌더 — closePosition 경로의 필수 가드를 강제한다.

        Binance Algo Order 명세(2025-12-09 적용):
            algoType="CONDITIONAL", type=STOP_MARKET/TAKE_PROFIT_MARKET/...
            closePosition="true" (소문자) → **quantity 와 reduceOnly 동시 전송 금지**.
            triggerPrice (구 stopPrice 와 다른 키).
            workingType=MARK_PRICE / CONTRACT_PRICE — config 노출.
            priceProtect="true"/"false" (소문자 — 공식 문서 기준; 일부 클라이언트
                docstring 이 "TRUE"/"FALSE" 로 잘못 적혀 있음).
            clientAlgoId — 멱등 + cancel 시 사용.

        Args:
            symbol, side ("BUY"/"SELL"), order_type (STOP_MARKET/...).
            trigger_price: 트리거 가격 (구 stopPrice 가 아닌 triggerPrice 키).
            client_algo_id: 멱등용 clientAlgoId (호출부에서 미리 생성).

        Returns:
            algoOrder POST 페이로드 dict. quantity / reduceOnly 키는 절대 포함되지 않음.
        """
        payload: dict = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": trigger_price,
            "closePosition": "true",
            "workingType": self.working_type,
            "priceProtect": "true" if self.price_protect else "false",
            "clientAlgoId": client_algo_id,
        }
        # 안전 가드 — closePosition="true" 경로에서 절대 보내면 안 되는 키 (요구 [2])
        assert "quantity" not in payload, "algoOrder closePosition=true 경로에 quantity 금지"
        assert "reduceOnly" not in payload, "algoOrder closePosition=true 경로에 reduceOnly 금지"
        return payload

    @staticmethod
    def _extract_algo_id(resp) -> str | None:
        """algoOrder 응답에서 식별자를 문자열로 반환한다(algoId 우선, clientAlgoId 폴백)."""
        if not isinstance(resp, dict):
            return None
        algo_id = resp.get("algoId")
        if algo_id is None:
            algo_id = resp.get("clientAlgoId")
        return str(algo_id) if algo_id is not None else None

    @staticmethod
    def _gen_client_algo_id(symbol: str, role: str) -> str:
        """algo 보호 주문용 clientAlgoId 생성 (멱등 + 역할 식별).

        role: "sl" (STOP_MARKET) / "tp" (TAKE_PROFIT_MARKET) 등.
        Binance 최대 길이(보수적으로 36자) 내로 자른다.
        """
        return f"bot-{role}-{symbol[:8]}-{uuid.uuid4().hex[:10]}"[:36]

    async def replace_stop_order(
        self, symbol: str, old_sl_order_id: str | None, action: str, new_sl: float
    ) -> str | None:
        """기존 algo STOP 을 새 triggerPrice 로 교체한다(BE/트레일).

        A-3 (create-first): 무방비 구간을 만들지 않기 위해 순서를 뒤집는다.
            1) 새 STOP 을 먼저 생성한다.
            2) 새 STOP 생성 성공 시 DB sl_order_id 를 새 ID 로 먼저 갱신한다.
               (ExitPlanController 의 plan.sl_order_id 갱신은 호출부가 반환값으로 처리)
            3) 그 다음 기존 STOP 을 cancel 한다.
            4) 기존 STOP cancel 실패는 warning 만 — 새 STOP 은 그대로 유지.
            5) 새 STOP 생성 실패 시 기존 STOP 은 **절대 cancel 하지 않는다** (요구 7).
               기존 STOP 은 거래소에 그대로 남아 하드 손절을 계속 보장.

        중간에 거래소 STOP 가 잠시 2개 존재할 수 있지만, 둘 다 closePosition=true
        로 등록되어 어느 한쪽이 트리거되면 포지션이 전량 청산되고 다른 한쪽은
        만료/잔여 상태가 된다. reconcile / _cancel_trade_protective_orders 가 잔여
        STOP 을 정리한다.

        dry_run 은 no-op 으로 None 을 반환한다(요구 8).

        Returns:
            새 STOP 주문 id(문자열 — algoId 또는 clientAlgoId). 실패 시 None
            (기존 STOP 유지, 호출부가 다음 루프 재시도).
        """
        if self.dry_run:
            return None

        # ── 1) 새 STOP 을 먼저 생성 ──
        close_side = "SELL" if action == "LONG" else "BUY"
        client_algo_id = self._gen_client_algo_id(symbol, "sl")
        payload = self._build_algo_payload(
            symbol=symbol, side=close_side, order_type="STOP_MARKET",
            trigger_price=new_sl, client_algo_id=client_algo_id,
        )
        try:
            stop = await asyncio.to_thread(
                self.binance.futures_create_algo_order, **payload
            )
            new_id = self._extract_algo_id(stop) or client_algo_id
        except Exception as e:
            # 새 STOP 생성 실패 — 기존 STOP 은 절대 cancel 하지 않는다 (요구 7)
            logger.warning(
                "[Executor] %s algo STOP 갱신 실패(기존 유지, 무방비 구간 없음): %s — "
                "다음 루프 재시도", symbol, e,
            )
            return None

        logger.info(
            "[Executor] %s algo STOP 갱신 → trigger=%s (id=%s) — 기존(%s) cancel 진행",
            symbol, new_sl, new_id, old_sl_order_id,
        )

        # ── 2) DB 의 sl_order_id 먼저 새 ID 로 갱신 (실패해도 새 STOP 은 살아있음) ──
        try:
            await asyncio.to_thread(self._update_sl_order_id, symbol, new_id)
        except Exception as e:
            # DB 갱신 실패는 비치명적 — 다음 reconcile 에서 정합
            logger.warning(
                "[Executor] %s sl_order_id DB 갱신 실패(무시): %s", symbol, e,
            )

        # ── 3) 기존 STOP 을 그 다음 cancel (요구 5/6 — 실패는 warning) ──
        #     _cancel_algo_order_safe 자체가 예외를 삼키지만, 명시적 가드를 위해
        #     None/같은 ID 인 경우 호출 자체를 생략한다.
        if old_sl_order_id and str(old_sl_order_id) != new_id:
            await self._cancel_algo_order_safe(symbol, old_sl_order_id)

        return new_id

    async def _force_close(self, symbol: str, action: str, quantity: float) -> None:
        """보호주문 실패 등 비상 시 reduceOnly 시장가로 즉시 청산한다(요구 7)."""
        close_side = "SELL" if action == "LONG" else "BUY"
        try:
            await asyncio.to_thread(
                self.binance.futures_create_order,
                symbol=symbol, side=close_side, type="MARKET",
                quantity=quantity, reduceOnly=True,
            )
            logger.critical("[Executor] %s 비상 강제청산 전송 완료", symbol)
        except Exception as e:
            logger.critical("[Executor] %s 비상 강제청산 실패: %s — 운영자 수동 개입 필요",
                            symbol, e)

    # ── exchangeInfo 정규화 (감사 M3) ──────────────────────────

    async def _normalize(
        self, symbol: str, quantity: float, price: float
    ) -> tuple[float | None, float | None, str | None]:
        """수량/가격을 심볼 필터로 정규화한다. 위반/조회실패 시 차단 사유 반환."""
        f = await self._get_symbol_filters(symbol)
        if f is None:
            return None, None, "심볼 필터 조회 실패 — 주문 차단"

        qty_step = f.get("qty_step")
        price_tick = f.get("price_tick")
        norm_qty = self._floor_to_step(quantity, qty_step) if qty_step else quantity
        norm_price = self._round_to_tick(price, price_tick) if price_tick else price

        if norm_qty <= 0 or norm_qty < f.get("min_qty", 0.0):
            return None, None, (
                f"수량 {norm_qty} < minQty {f.get('min_qty')} (step={qty_step})"
            )
        notional = norm_qty * norm_price
        if notional < f.get("min_notional", 0.0):
            return None, None, (
                f"명목가치 {notional:.4f} < minNotional {f.get('min_notional')}"
            )
        return norm_qty, norm_price, None

    async def _get_symbol_filters(self, symbol: str) -> dict | None:
        """exchangeInfo 필터를 캐시 조회한다(TTL). 조회 실패 시 None."""
        now = time.monotonic()
        if not self._filters or (now - self._filters_loaded_at) > self.exchange_info_ttl_s:
            try:
                info = await asyncio.to_thread(self.binance.futures_exchange_info)
            except Exception as e:
                logger.error("[Executor] exchangeInfo 조회 실패: %s", e)
                return None
            if not isinstance(info, dict):
                logger.error("[Executor] exchangeInfo 형식 오류")
                return None
            parsed: dict[str, dict] = {}
            for s in info.get("symbols", []):
                try:
                    parsed[s["symbol"]] = self._parse_filters(s)
                except (KeyError, TypeError, ValueError):
                    continue
            if not parsed:
                return None
            self._filters = parsed
            self._filters_loaded_at = now
        return self._filters.get(symbol)

    @staticmethod
    def _parse_filters(sym_info: dict) -> dict:
        """단일 심볼 exchangeInfo 항목에서 필요한 필터를 추출한다."""
        by_type = {x.get("filterType"): x for x in sym_info.get("filters", [])}
        lot = by_type.get("LOT_SIZE") or by_type.get("MARKET_LOT_SIZE")
        pf = by_type.get("PRICE_FILTER")
        mn = by_type.get("MIN_NOTIONAL")
        return {
            "qty_step": float(lot["stepSize"]) if lot and lot.get("stepSize") else None,
            "min_qty": float(lot["minQty"]) if lot and lot.get("minQty") else 0.0,
            "price_tick": float(pf["tickSize"]) if pf and pf.get("tickSize") else None,
            "min_notional": (
                float(mn.get("notional") or mn.get("minNotional") or 0.0) if mn else 0.0
            ),
        }

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        """value 를 step 의 배수로 내림(절대 초과 사이즈 방지)."""
        d = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
        return float(d * Decimal(str(step)))

    @staticmethod
    def _round_to_tick(value: float, tick: float) -> float:
        """value 를 tick 단위로 반올림."""
        d = (Decimal(str(value)) / Decimal(str(tick))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return float(d * Decimal(str(tick)))

    @staticmethod
    def _extract_fill(order) -> tuple[float, float]:
        """주문 응답에서 (executedQty, avg_price) 를 추출한다."""
        if not isinstance(order, dict):
            return 0.0, 0.0
        try:
            eq = float(order.get("executedQty") or 0.0)
        except (ValueError, TypeError):
            eq = 0.0
        avg = order.get("avgPrice")
        try:
            avg = float(avg) if avg not in (None, "") else 0.0
        except (ValueError, TypeError):
            avg = 0.0
        if avg <= 0 and eq > 0:
            cq = order.get("cumQuote") or order.get("cummulativeQuoteQty")
            try:
                if cq:
                    avg = float(cq) / eq
            except (ValueError, TypeError, ZeroDivisionError):
                avg = 0.0
        return eq, avg

    @staticmethod
    def _gen_client_order_id(symbol: str) -> str:
        """멱등용 newClientOrderId 생성 (Binance 36자 제한 내)."""
        return f"bot-{symbol[:8]}-{uuid.uuid4().hex[:16]}"[:36]

    # ── 거래 기록 ────────────────────────────────────────────────

    def _insert_trade(
        self, decision: dict, quantity: float, leverage: int, entry_price: float,
        entry_order_id: str | None, sl_order_id: str | None, tp_order_id: str | None,
    ) -> int:
        """trades 테이블에 진입 행을 INSERT 하고 trade_id 를 반환한다.

        v3.1.1: wallet/available/locked_margin_at_entry (부록 E-7-1).
        v3.1.2: entry_order_id / sl_order_id / tp_order_id / trade_status='OPEN'.
        entry_price 는 실제 체결가(live) 또는 entry_price(dry_run)이다.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    timestamp, symbol, action, entry_price, quantity, leverage,
                    take_profit, stop_loss, setup_tag,
                    regime, regime_confidence, cost_guard_ev, pair_tier,
                    manual_intervention, sizing_kelly_raw, sizing_pct,
                    wallet_balance_at_entry, available_at_entry,
                    locked_margin_at_entry,
                    entry_order_id, sl_order_id, tp_order_id, trade_status,
                    entry_limit_price, entry_order_send_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    decision["symbol"],
                    decision["action"],
                    float(entry_price),
                    quantity,
                    leverage,
                    float(decision["tp"]),
                    float(decision["sl"]),
                    decision.get("setup_tag"),
                    decision.get("regime"),
                    decision.get("regime_confidence"),
                    decision.get("cost_guard_ev"),
                    decision.get("pair_tier"),
                    0,                                  # manual_intervention
                    decision.get("sizing_kelly_raw"),
                    decision.get("sizing_pct"),
                    decision.get("wallet_balance_at_entry"),
                    decision.get("available_at_entry"),
                    decision.get("locked_margin_at_entry"),
                    entry_order_id,
                    sl_order_id,
                    tp_order_id,
                    "OPEN",
                    decision.get("entry_limit_price"),       # B-3: 요청 limit가
                    decision.get("entry_order_send_ts"),     # B-3: 주문 전송 시각
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _update_sl_order_id(self, symbol: str, new_sl_order_id: str | None) -> None:
        """미청산 trades 행의 sl_order_id 를 갱신한다(STOP 재배치 시)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE trades SET sl_order_id = ? "
                "WHERE symbol = ? AND exit_price IS NULL",
                (new_sl_order_id, symbol),
            )
            conn.commit()
        finally:
            conn.close()

    # ── 포지션 조회 ─────────────────────────────────────────────

    async def get_open_positions(self) -> list[Position]:
        """현재 열린 포지션 목록을 반환한다.

        live: Binance futures_position_information (positionAmt != 0 필터).
        dry_run: trades 테이블의 미청산 행 (exit_price IS NULL).
        """
        if self.dry_run:
            return await asyncio.to_thread(self._db_open_positions)
        try:
            raw = await asyncio.to_thread(self.binance.futures_position_information)
        except Exception as e:
            logger.error("[Executor] 포지션 조회 실패: %s", e)
            return []

        positions: list[Position] = []
        for p in raw or []:
            try:
                amt = float(p["positionAmt"])
                if amt == 0:
                    continue
                positions.append(Position(
                    symbol=p["symbol"],
                    position_amt=amt,
                    entry_price=float(p["entryPrice"]),
                    unrealized_pnl=float(p.get("unRealizedProfit", 0.0)),
                    side="LONG" if amt > 0 else "SHORT",
                ))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("[Executor] 포지션 항목 파싱 실패: %s", e)
        return positions

    def _db_open_positions(self) -> list[Position]:
        """dry_run 용 — trades 테이블의 미청산 행을 Position 으로 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT symbol, action, entry_price, quantity "
                "FROM trades WHERE exit_price IS NULL"
            ).fetchall()
        finally:
            conn.close()
        positions: list[Position] = []
        for symbol, action, entry_price, quantity in rows:
            qty = float(quantity or 0.0)
            amt = qty if action == "LONG" else -qty
            positions.append(Position(
                symbol=symbol,
                position_amt=amt,
                entry_price=float(entry_price or 0.0),
                unrealized_pnl=0.0,
                side=action,
            ))
        return positions

    # ── 청산 ────────────────────────────────────────────────────

    async def close_position(
        self, symbol: str, reason: str = "manual", portion: float = 1.0
    ) -> dict:
        """열린 포지션을 전량 또는 부분 청산한다.

        전량 청산(portion>=1.0): 잔존 보호주문 cancel → reduceOnly 시장가 청산
            → 실제 체결가/수수료로 PnL 정밀 기록 (감사 M1/[4]).
        부분 청산(portion<1.0): 해당 비율 reduceOnly 시장가 + quantity 감소
            (행은 열린 상태 유지). closePosition STOP 은 잔여를 계속 보호한다.

        Returns:
            {"success", "symbol", "reason", "portion"}.
        """
        # Protected Existing Position Coexist Mode — 보호종목 기존 보유분은 절대
        # 청산/취소하지 않는다. 거래소 API(주문/취소) 호출 자체를 하지 않는다.
        if symbol in self._protected_symbols:
            logger.warning(
                "[Executor] %s 보호종목 — 청산/취소 차단(기존 보유분 보존)", symbol
            )
            return {"success": False, "symbol": symbol,
                    "reason": "protected_symbol_close_blocked", "portion": portion}

        if not 0.0 < portion <= 1.0:
            logger.error("[Executor] %s 잘못된 portion=%s", symbol, portion)
            return {"success": False, "symbol": symbol,
                    "reason": f"잘못된 portion: {portion}", "portion": portion}

        is_full = portion >= 1.0
        exit_price: float | None = None
        exit_commission: float | None = None

        if not self.dry_run:
            try:
                positions = await self.get_open_positions()
                target = next((p for p in positions if p.symbol == symbol), None)
                if target is None:
                    # A3 [2.7]: 거래소 STOP/TP 가 폴링 사이에 선체결되어 포지션이
                    # 사라진 경우에도 미청산 trades 행을 CLOSED 로 마감한다(OPEN 고아
                    # 방지). reconcile 과 동일 패턴 — 최근 체결로 청산가 추정 후 finalize.
                    # (미청산 행이 없으면 _finalize_trade_exit 가 no-op.) 마감 실패는
                    # 삼켜 success 반환을 보존한다(거래소 추가 주문 0 — 기존 동작 불변).
                    logger.info(
                        "[Executor] %s 열린 포지션 없음 — DB 마감 시도(고아 방지, A3)", symbol
                    )
                    try:
                        np_exit, np_comm = await self._resolve_recent_fill(symbol)
                        await asyncio.to_thread(
                            self._finalize_trade_exit, symbol, "no_position",
                            np_exit, np_comm,
                        )
                    except Exception as e:  # noqa: BLE001 — 마감 실패는 success 보존
                        logger.error("[Executor] %s no_position 마감 실패: %s", symbol, e)
                    return {"success": True, "symbol": symbol,
                            "reason": "no_position", "portion": portion}

                # 전량 청산 시 잔존 거래소 보호주문 먼저 취소(중복 체결 방지)
                if is_full:
                    await self._cancel_trade_protective_orders(symbol)

                close_side = "SELL" if target.position_amt > 0 else "BUY"
                total_amt = abs(target.position_amt)
                close_qty = (
                    total_amt if is_full
                    else self._floor_partial_qty(total_amt, portion)
                )
                if close_qty <= 0:
                    logger.error("[Executor] %s 청산 수량 0 (portion=%s)", symbol, portion)
                    return {"success": False, "symbol": symbol,
                            "reason": "청산 수량 0", "portion": portion}
                order = await asyncio.to_thread(
                    self.binance.futures_create_order,
                    symbol=symbol,
                    side=close_side,
                    type="MARKET",
                    quantity=close_qty,
                    reduceOnly=True,
                )
                exit_price, exit_commission = await self._resolve_exit_fill(symbol, order)
                logger.info(
                    "[Executor] %s 시장가 %s 청산 전송 (%s, portion=%.2f, exit=%s)",
                    symbol, "전량" if is_full else "부분", reason, portion, exit_price,
                )
            except Exception as e:
                logger.error("[Executor] %s 청산 주문 실패: %s", symbol, e)
                return {"success": False, "symbol": symbol,
                        "reason": str(e), "portion": portion}

        try:
            if is_full:
                await asyncio.to_thread(
                    self._finalize_trade_exit, symbol, reason, exit_price, exit_commission
                )
            else:
                await asyncio.to_thread(self._reduce_trade_quantity, symbol, portion)
        except Exception as e:
            logger.error("[Executor] %s 청산 DB 기록 실패: %s", symbol, e)
            return {"success": False, "symbol": symbol,
                    "reason": f"DB: {e}", "portion": portion}

        return {"success": True, "symbol": symbol, "reason": reason,
                "portion": portion}

    async def reconcile_closed_positions(self, tracked_symbols: list[str]) -> list[dict]:
        """추적 중인데 거래소 포지션이 사라진 심볼을 DB 에 마감 처리한다.

        거래소 STOP/TP 가 메인 루프 폴링 사이에 체결된 경우를 포착한다(요구 7).
        dry_run 은 외부 체결이 없으므로 빈 리스트.

        Args:
            tracked_symbols: ExitPlanController 가 추적 중인 심볼 목록.

        Returns:
            [{"symbol", "exit_price", "reason"}] — 마감 처리된 심볼 목록.
        """
        if self.dry_run or not tracked_symbols:
            return []
        try:
            positions = await self.get_open_positions()
        except Exception as e:
            logger.error("[Executor] reconcile 포지션 조회 실패: %s", e)
            return []
        live_symbols = {p.symbol for p in positions}

        closed: list[dict] = []
        for symbol in tracked_symbols:
            # 방어적 — 보호종목은 reconcile 정리 대상에서 제외(기존 보유분 보존).
            # 정상적으로는 보호종목이 tracked 에 들어오지 않으나, 이중 안전망.
            if symbol in self._protected_symbols:
                logger.warning("[Executor] %s 보호종목 — reconcile skip", symbol)
                continue
            if symbol in live_symbols:
                continue
            # 추적 중인데 거래소 포지션 없음 → STOP/TP 체결(or 외부 청산)
            await self._cancel_trade_protective_orders(symbol)
            exit_price, exit_commission = await self._resolve_recent_fill(symbol)
            try:
                await asyncio.to_thread(
                    self._finalize_trade_exit, symbol, "exchange_stop_or_tp",
                    exit_price, exit_commission,
                )
            except Exception as e:
                logger.error("[Executor] %s reconcile 마감 실패: %s", symbol, e)
                continue
            logger.warning(
                "[Executor] %s 거래소 보호주문 체결 감지 → DB 마감 (exit=%s)",
                symbol, exit_price,
            )
            closed.append({"symbol": symbol, "exit_price": exit_price,
                           "reason": "exchange_stop_or_tp"})
        return closed

    # ── live preflight (Protected Existing Position Coexist Mode) ──────
    # 아래 3개는 main 의 _live_account_preflight 전용. 조회 실패 시 예외를 그대로
    # 전파하여 호출부가 fail-closed(시작 차단) 하도록 한다. get_open_positions 처럼
    # 실패를 [] 로 삼키지 않는다(빈 결과를 '없음'으로 오인 → fail-open 방지).

    async def preflight_open_position_symbols(self) -> list[str]:
        """기존 열린 포지션의 심볼 목록(positionAmt != 0). 조회 실패 시 예외."""
        raw = await asyncio.to_thread(self.binance.futures_position_information)
        return [p["symbol"] for p in (raw or [])
                if float(p.get("positionAmt", 0) or 0) != 0]

    async def preflight_open_order_symbols(self) -> list[str]:
        """기존 일반 Open Orders 의 심볼 목록. 조회 실패 시 예외."""
        raw = await asyncio.to_thread(self.binance.futures_get_open_orders)
        return [o["symbol"] for o in (raw or [])]

    async def preflight_open_algo_symbols(self) -> list[str]:
        """기존 Open Algo Orders 의 심볼 목록. 조회 실패 시 예외."""
        ao = await asyncio.to_thread(self.binance.futures_get_open_algo_orders)
        orders = ao.get("orders") if isinstance(ao, dict) else (ao or [])
        return [o["symbol"] for o in orders]

    async def _cancel_trade_protective_orders(self, symbol: str) -> None:
        """미청산 trades 행에 기록된 sl/tp algo 주문을 취소한다(best-effort).

        보호 주문은 algo 엔드포인트로 생성됐으므로 cancel 도 algo 엔드포인트 사용.
        진입 LIMIT 의 _cancel_order_safe 와 분리 (요구 [10] — int 캐스팅 금지).
        """
        try:
            row = await asyncio.to_thread(self._get_open_trade_order_ids, symbol)
        except Exception as e:
            logger.warning("[Executor] %s 보호주문 id 조회 실패: %s", symbol, e)
            return
        if not row:
            return
        sl_id, tp_id = row
        await self._cancel_algo_order_safe(symbol, sl_id)
        await self._cancel_algo_order_safe(symbol, tp_id)

    async def _cancel_order_safe(self, symbol: str, order_id) -> None:
        """진입 LIMIT 주문(orderId=int)을 취소한다. 이미 없거나 실패해도 예외 삼킴.

        보호(algo) 주문 cancel 은 _cancel_algo_order_safe 사용 — int 캐스팅 금지.
        """
        if not order_id:
            return
        try:
            await asyncio.to_thread(
                self.binance.futures_cancel_order, symbol=symbol, orderId=int(order_id)
            )
            logger.info("[Executor] %s 주문 취소 (id=%s)", symbol, order_id)
        except Exception as e:
            logger.warning("[Executor] %s 주문 취소 실패(무시): id=%s %s", symbol, order_id, e)

    async def _cancel_algo_order_safe(
        self, symbol: str, algo_id: str | int | None
    ) -> None:
        """algo 보호 주문을 취소한다. algoId(int) / clientAlgoId(str) 자동 분기.

        DB 의 sl_order_id / tp_order_id 는 TEXT 컬럼 — 봇이 응답에서 받은 algoId
        를 문자열로 저장하거나, 응답에 id 가 없을 때는 clientAlgoId 를 저장한다.
        cancel 시 그 문자열이 숫자로만 구성됐으면 algoId(int)로, 그렇지 않으면
        clientAlgoId(str)로 라우팅한다. 어느 쪽 실패도 삼킴(best-effort).
        """
        if not algo_id:
            return
        algo_id_str = str(algo_id)
        cancel_kwargs: dict = {"symbol": symbol}
        if algo_id_str.isdigit():
            cancel_kwargs["algoId"] = int(algo_id_str)
        else:
            cancel_kwargs["clientAlgoId"] = algo_id_str
        try:
            await asyncio.to_thread(
                self.binance.futures_cancel_algo_order, **cancel_kwargs
            )
            logger.info(
                "[Executor] %s algo 주문 취소 (id=%s)", symbol, algo_id_str
            )
        except Exception as e:
            logger.warning(
                "[Executor] %s algo 주문 취소 실패(무시): id=%s %s",
                symbol, algo_id_str, e,
            )

    async def _resolve_exit_fill(
        self, symbol: str, order: dict
    ) -> tuple[float | None, float | None]:
        """청산 주문의 실제 체결가/수수료를 확정한다(avgPrice 누락 시 보정 — M1)."""
        eq, avg = self._extract_fill(order)
        if avg > 0:
            return avg, None
        order_id = order.get("orderId") if isinstance(order, dict) else None
        return await self._fills_by_order(symbol, order_id)

    async def _resolve_recent_fill(
        self, symbol: str
    ) -> tuple[float | None, float | None]:
        """reconcile 용 — 최근 체결에서 청산가/수수료를 추정한다."""
        return await self._fills_by_order(symbol, order_id=None)

    async def _fills_by_order(
        self, symbol: str, order_id
    ) -> tuple[float | None, float | None]:
        """futures_account_trades 로 (체결 VWAP, 수수료 합)을 계산한다.

        order_id 가 주어지면 해당 주문 체결만, 없으면 가장 최근 체결 묶음을 사용.
        실패/없음 시 (None, None) — 호출부가 중립 처리.
        """
        try:
            trades = await asyncio.to_thread(
                self.binance.futures_account_trades, symbol=symbol
            )
        except Exception as e:
            logger.warning("[Executor] %s 체결 내역 조회 실패: %s", symbol, e)
            return None, None
        if not trades:
            return None, None
        if order_id is not None:
            fills = [t for t in trades if str(t.get("orderId")) == str(order_id)]
        else:
            # 가장 최근 주문의 체결만 묶는다
            last_oid = trades[-1].get("orderId")
            fills = [t for t in trades if t.get("orderId") == last_oid]
        if not fills:
            return None, None
        try:
            qty = sum(float(t["qty"]) for t in fills)
            if qty <= 0:
                return None, None
            vwap = sum(float(t["price"]) * float(t["qty"]) for t in fills) / qty
            commission = sum(float(t.get("commission", 0.0)) for t in fills)
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning("[Executor] %s 체결 내역 파싱 실패: %s", symbol, e)
            return None, None
        return vwap, commission

    @staticmethod
    def _floor_partial_qty(total_amt: float, portion: float) -> float:
        """부분 청산 수량을 보수적으로 내림(3자리). 거래소 필터는 reduceOnly 라 관대."""
        return round(total_amt * portion, _DRYRUN_QTY_PRECISION)

    def _get_open_trade_order_ids(self, symbol: str) -> tuple | None:
        """미청산 trades 행의 (sl_order_id, tp_order_id) 반환 (없으면 None)."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT sl_order_id, tp_order_id FROM trades "
                "WHERE symbol = ? AND exit_price IS NULL ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else None

    def _finalize_trade_exit(
        self, symbol: str, reason: str,
        exit_price: float | None, exit_commission: float | None,
    ) -> None:
        """미청산 trades 행에 정밀 청산 정보를 기록한다(감사 M1/[4]).

        exit_price/pnl_usd/pnl_pct/fees_usd/pnl_usd_net/duration_seconds/
        exit_reason/trade_status='CLOSED' 를 계산해 UPDATE 한다.
        실제 체결가를 못 얻으면(None) 진입가를 중립값으로 사용한다(최후 수단).
        """
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT id, timestamp, action, entry_price, quantity, stop_loss "
                "FROM trades WHERE symbol = ? AND exit_price IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row is None:
                logger.info("[Executor] %s 마감할 미청산 행 없음", symbol)
                return
            tid, ts, action, entry, qty, stop = row
            entry = float(entry or 0.0)
            qty = float(qty or 0.0)
            stop = float(stop) if stop is not None else None
            if exit_price is None or exit_price <= 0:
                # A4 [2.5]: 청산 체결가 미상 시 진입가 중립(PnL 0=breakeven)은 손실을
                # 숨긴다. STOP가가 있으면 그걸로 보수적 손실 처리하고, 합성 청산임을
                # exit_reason 에 표시(A4b: expectancy 집계에선 제외, 연패 게이트엔 보수적
                # 포함 — 운영자 결정 2026-05-30).
                if stop is not None and stop > 0:
                    logger.warning(
                        "[Executor] %s 청산 체결가 미상 → STOP가 %.6g 로 보수적 손실 처리(A4)",
                        symbol, stop,
                    )
                    exit_price = stop
                else:
                    logger.warning(
                        "[Executor] %s 청산 체결가·STOP 모두 미상 → 진입가 중립(최후수단)", symbol
                    )
                    exit_price = entry
                if _STOP_FALLBACK_FLAG not in reason:
                    reason = f"{reason}{_STOP_FALLBACK_FLAG}"

            direction = 1.0 if action == "LONG" else -1.0
            pnl_usd = direction * (exit_price - entry) * qty
            entry_notional = entry * qty
            exit_notional = exit_price * qty
            # A4 [2.4]: 진입은 GTX(post-only)=maker. 청산은 reduceOnly MARKET=taker.
            if exit_commission is not None:
                # 청산 수수료는 실측, 진입 수수료는 maker 율 추정.
                fees_usd = entry_notional * _DEFAULT_MAKER_FEE + exit_commission
            else:
                fees_usd = (
                    entry_notional * _DEFAULT_MAKER_FEE
                    + exit_notional * _DEFAULT_TAKER_FEE
                )
            pnl_usd_net = pnl_usd - fees_usd
            pnl_pct = (direction * (exit_price - entry) / entry * 100.0) if entry > 0 else 0.0
            duration = self._duration_seconds(ts)

            conn.execute(
                """
                UPDATE trades
                SET exit_price = ?, pnl_usd = ?, pnl_pct = ?, fees_usd = ?,
                    pnl_usd_net = ?, duration_seconds = ?, exit_reason = ?,
                    trade_status = 'CLOSED'
                WHERE id = ?
                """,
                (exit_price, pnl_usd, pnl_pct, fees_usd, pnl_usd_net,
                 duration, reason, tid),
            )
            conn.commit()
        finally:
            conn.close()

    def _record_unfilled_signal(self, decision: dict, reason: str) -> None:
        """B7 [4-4]: 진입 미체결/스킵 신호 메타를 unfilled_signals 에 적재한다.

        post-only(GTX) 미체결·타임아웃으로 폐기되던 신호를 보존해, 이후 +1/+3/+6
        bar forward-return 을 채워 체결 역선택(B-3)을 검증할 수 있게 한다. 분석
        리포트는 범위 외 — 적재 훅만 제공한다.

        **거래 경로와 완전 격리**: 어떤 실패도 삼켜(로그만) 진입-스킵 흐름에 절대
        영향을 주지 않는다(계측이 매매를 깨면 안 된다 — CLAUDE.md §4).
        """
        try:
            ts = datetime.now(timezone.utc).isoformat()
            entry_price = decision.get("entry_price")
            signal_price = float(entry_price) if entry_price is not None else None
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO unfilled_signals "
                    "(ts, symbol, action, setup_tag, signal_price, reason, order_send_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ts, decision.get("symbol"), decision.get("action"),
                     decision.get("setup_tag"), signal_price, reason,
                     decision.get("order_send_ts")),
                )
                conn.commit()
            finally:
                conn.close()
            logger.info(
                "[Executor] %s 미체결 신호 기록 (reason=%s, B7)",
                decision.get("symbol"), reason,
            )
        except Exception as e:  # noqa: BLE001 — 계측 실패는 절대 매매 흐름에 영향 X
            logger.warning("[Executor] 미체결 신호 기록 실패: %s (거래 영향 X)", e)

    def _reduce_trade_quantity(self, symbol: str, portion: float) -> None:
        """부분 청산 — 미청산 trades 행의 quantity 를 잔여 수량으로 감소시킨다.

        exit_price 는 NULL 로 유지되어 trades 행은 열린 상태로 남는다.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE trades
                SET quantity = quantity * (1.0 - ?)
                WHERE symbol = ? AND exit_price IS NULL
                """,
                (portion, symbol),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _duration_seconds(entry_ts: str | None) -> int | None:
        """진입 timestamp(ISO)부터 현재까지 경과 초. 파싱 실패 시 None."""
        if not entry_ts:
            return None
        try:
            dt = datetime.fromisoformat(entry_ts)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - dt).total_seconds())

    # ── 내부 ────────────────────────────────────────────────────

    @staticmethod
    def _fail(symbol: str, decision: dict, reason: str, critical: bool = False) -> dict:
        """진입 실패 결과 dict 생성."""
        return {
            "success": False,
            "trade_id": None,
            "symbol": symbol,
            "quantity": 0.0,
            "entry_price_actual": None,
            "sl_order_id": None,
            "tp_order_id": None,
            "critical": critical,
            "decision": decision,
            "reason": reason,
        }
