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
  - C1: 진입 체결 직후 거래소 reduceOnly STOP_MARKET(필수) +
    TAKE_PROFIT_MARKET(옵션)를 생성한다. 손절 주문 실패 시 즉시 시장가 청산.
  - H1: 체결·보호주문이 모두 성공한 뒤에만 trades 행을 INSERT 한다(유령 행 방지).
  - H2: newClientOrderId 로 멱등성 확보.
  - M1: 청산 시 실제 체결가/수수료로 PnL 을 정확히 기록한다(avgPrice 누락 시
    futures_account_trades 로 보정).
  - M3: 수량/가격을 exchangeInfo 필터(LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL)로
    정규화한다. 필터 조회 실패 시 live 주문을 차단한다.

설계 메모:
  - dry_run=True: 어떤 Binance 주문/조회 API 도 호출하지 않는다(페이퍼).
    체결가=entry_price 로 가정하고 정규화·보호주문은 생략, trades INSERT 만 수행.
  - 거래소·DB 동기 호출은 asyncio.to_thread 로 래핑(메인 루프 비차단).
  - 보호 주문은 closePosition=True 로 생성 → 부분 청산 후에도 잔여 포지션을
    자동 전량 보호. SL 이 하드 플로어, ExitPlanController 는 분할 TP/트레일/
    시간 스톱 + SL 갱신을 담당한다.
=====================================================================
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

# enter_trade(decision) 필수 키 (boundary 검증)
_REQUIRED_DECISION_KEYS = ("symbol", "action", "entry_price", "tp", "sl", "size_usdt")

# exchangeInfo 필터 미적용 dry_run 폴백 수량 반올림 자리
_DRYRUN_QTY_PRECISION = 3

# 수수료 추정용 기본 taker 율(실제 체결 수수료를 못 얻을 때만 사용 — CostGuard 기본값과 동일)
_DEFAULT_TAKER_FEE = 0.00045

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
    ) -> None:
        """실행기 초기화.

        Args:
            binance_client: python-binance Client. dry_run=True 면 None 허용.
            db_path: sqlite3 DB 파일 경로 (trades 테이블).
            dry_run: True 면 거래소 호출을 스킵하고 DB 기록만 수행 (페이퍼).
            fill_timeout_s: 진입 주문 FILLED 대기 한도(초). 초과 시 cancel.
            fill_poll_interval_s: futures_get_order 폴링 주기(초).
            exchange_info_ttl_s: 심볼 필터 캐시 TTL(초).
            place_take_profit: True 면 reduceOnly TAKE_PROFIT_MARKET 도 생성.
        """
        if not db_path:
            logger.warning("[Executor] db_path 가 비어 있음 — DB 기록이 실패합니다")
        if binance_client is None and not dry_run:
            logger.warning(
                "[Executor] binance_client=None 인데 dry_run=False — "
                "enter_trade 가 항상 실패합니다"
            )
        self.binance = binance_client
        self.db_path = db_path
        self.dry_run = dry_run
        self.fill_timeout_s = fill_timeout_s
        self.fill_poll_interval_s = fill_poll_interval_s
        self.exchange_info_ttl_s = exchange_info_ttl_s
        self.place_take_profit = place_take_profit

        # exchangeInfo 심볼 필터 캐시 (M3)
        self._filters: dict[str, dict] = {}
        self._filters_loaded_at: float = 0.0

    # ── 진입 ────────────────────────────────────────────────────

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

        # ── live: exchangeInfo 필터 정규화 (M3) ──
        norm_qty, norm_price, reason = await self._normalize(
            symbol, size_usdt / entry_price, entry_price
        )
        if reason is not None:
            logger.error("[Executor] %s 정규화 차단: %s", symbol, reason)
            return self._fail(symbol, decision, reason)

        # ── live: 진입 + FILLED 확인 (C2/H2) ──
        client_order_id = self._gen_client_order_id(symbol)
        confirm = await self._place_entry_and_confirm(
            symbol, action, norm_qty, norm_price, leverage, client_order_id
        )
        if not confirm["filled"]:
            return self._fail(symbol, decision, confirm["reason"])

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
            logger.critical(
                "[Executor] %s trades 기록 실패 → 보호주문 cancel + 포지션 청산", symbol
            )
            await self._cancel_order_safe(symbol, sl_order_id)
            await self._cancel_order_safe(symbol, tp_order_id)
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
            return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                    "order_id": None, "reason": f"진입 주문 전송 실패: {e}"}

        order_id = order.get("orderId") if isinstance(order, dict) else None
        logger.info(
            "[Executor] %s GTX %s qty=%s @ %s (lev=%dx) 전송 — FILLED 대기",
            symbol, side, quantity, entry_price, leverage,
        )
        return await self._await_fill(symbol, order_id, order)

    async def _await_fill(self, symbol: str, order_id, last_order: dict) -> dict:
        """진입 주문이 FILLED 될 때까지 폴링한다. 타임아웃 시 cancel.

        부분체결 단순화: 타임아웃 시 잔량은 cancel 하되 executedQty>0 이면
        체결분으로 진행한다(감사 H1 — 미체결 추적/유령 행 방지).
        """
        deadline = time.monotonic() + self.fill_timeout_s
        while True:
            try:
                cur = await asyncio.to_thread(
                    self.binance.futures_get_order, symbol=symbol, orderId=order_id
                )
            except Exception as e:
                logger.error("[Executor] %s 주문 상태 조회 실패: %s", symbol, e)
                cur = last_order
            status = (cur or {}).get("status", "")
            if status == "FILLED":
                eq, avg = self._extract_fill(cur)
                logger.info("[Executor] %s 진입 FILLED qty=%s avg=%s", symbol, eq, avg)
                return {"filled": True, "filled_qty": eq, "avg_price": avg,
                        "order_id": order_id, "reason": "filled"}
            if status in _TERMINAL_FAIL_STATES:
                logger.warning("[Executor] %s 진입 주문 %s — 진입 스킵", symbol, status)
                return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                        "order_id": order_id, "reason": f"주문 {status}"}
            if time.monotonic() >= deadline:
                # 타임아웃 — 잔여 주문 cancel 후 체결분 판정
                await self._cancel_order_safe(symbol, order_id)
                eq, avg = self._extract_fill(cur)
                if eq > 0:
                    logger.warning(
                        "[Executor] %s 타임아웃 — 부분체결 qty=%s 로 진행 (잔량 cancel)",
                        symbol, eq,
                    )
                    return {"filled": True, "filled_qty": eq, "avg_price": avg,
                            "order_id": order_id, "reason": "partial_fill_timeout"}
                logger.warning(
                    "[Executor] %s 타임아웃 미체결 → cancel + 진입 스킵", symbol
                )
                return {"filled": False, "filled_qty": 0.0, "avg_price": 0.0,
                        "order_id": order_id, "reason": "fill_timeout"}
            await asyncio.sleep(self.fill_poll_interval_s)

    async def _place_protective_orders(
        self, symbol: str, action: str, filled_qty: float, sl: float, tp: float,
    ) -> tuple[str | None, str | None]:
        """진입 체결 직후 reduceOnly STOP_MARKET(+TAKE_PROFIT_MARKET)을 생성한다.

        closePosition=True 로 생성하여 부분 청산 후에도 잔여 전량을 보호한다.

        Returns:
            (sl_order_id, tp_order_id). STOP 실패 시 sl_order_id=None
            (호출부가 강제청산). TP 실패는 비치명적(SL 이 하드 플로어)이라
            tp_order_id=None 로 두고 진행한다.
        """
        close_side = "SELL" if action == "LONG" else "BUY"

        try:
            stop = await asyncio.to_thread(
                self.binance.futures_create_order,
                symbol=symbol,
                side=close_side,
                type="STOP_MARKET",
                stopPrice=sl,
                closePosition=True,
            )
            sl_order_id = str(stop.get("orderId")) if isinstance(stop, dict) else None
            logger.info("[Executor] %s 거래소 STOP_MARKET 등록 (stop=%s, id=%s)",
                        symbol, sl, sl_order_id)
        except Exception as e:
            logger.error("[Executor] %s STOP_MARKET 생성 실패: %s", symbol, e)
            return None, None

        tp_order_id: str | None = None
        if self.place_take_profit:
            try:
                tp_o = await asyncio.to_thread(
                    self.binance.futures_create_order,
                    symbol=symbol,
                    side=close_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=tp,
                    closePosition=True,
                )
                tp_order_id = str(tp_o.get("orderId")) if isinstance(tp_o, dict) else None
                logger.info("[Executor] %s 거래소 TAKE_PROFIT_MARKET 등록 (tp=%s, id=%s)",
                            symbol, tp, tp_order_id)
            except Exception as e:
                # TP 실패는 비치명적 — SL 이 이미 포지션을 보호한다
                logger.warning("[Executor] %s TAKE_PROFIT_MARKET 생성 실패(비치명적): %s",
                               symbol, e)
        return sl_order_id, tp_order_id

    async def replace_stop_order(
        self, symbol: str, old_sl_order_id: str | None, action: str, new_sl: float
    ) -> str | None:
        """기존 거래소 STOP 을 취소하고 새 stopPrice 로 재생성한다(BE/트레일).

        dry_run 은 no-op 으로 None 을 반환한다.

        Returns:
            새 STOP 주문 id. 실패 시 None(호출부가 경고 후 재시도, 소프트웨어
            SL 은 여전히 동작하므로 무방비 아님).
        """
        if self.dry_run:
            return None
        await self._cancel_order_safe(symbol, old_sl_order_id)
        close_side = "SELL" if action == "LONG" else "BUY"
        try:
            stop = await asyncio.to_thread(
                self.binance.futures_create_order,
                symbol=symbol,
                side=close_side,
                type="STOP_MARKET",
                stopPrice=new_sl,
                closePosition=True,
            )
            new_id = str(stop.get("orderId")) if isinstance(stop, dict) else None
            logger.info("[Executor] %s STOP 갱신 → stop=%s (id=%s)", symbol, new_sl, new_id)
            await asyncio.to_thread(self._update_sl_order_id, symbol, new_id)
            return new_id
        except Exception as e:
            logger.warning("[Executor] %s STOP 갱신 실패: %s — 다음 루프 재시도", symbol, e)
            return None

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
                    entry_order_id, sl_order_id, tp_order_id, trade_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?)
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
                    logger.info("[Executor] %s 열린 포지션 없음 — 청산 스킵", symbol)
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

    async def _cancel_trade_protective_orders(self, symbol: str) -> None:
        """미청산 trades 행에 기록된 sl/tp 주문을 취소한다(best-effort)."""
        try:
            row = await asyncio.to_thread(self._get_open_trade_order_ids, symbol)
        except Exception as e:
            logger.warning("[Executor] %s 보호주문 id 조회 실패: %s", symbol, e)
            return
        if not row:
            return
        sl_id, tp_id = row
        await self._cancel_order_safe(symbol, sl_id)
        await self._cancel_order_safe(symbol, tp_id)

    async def _cancel_order_safe(self, symbol: str, order_id) -> None:
        """주문을 취소한다. 이미 없거나 실패해도 예외를 삼킨다."""
        if not order_id:
            return
        try:
            await asyncio.to_thread(
                self.binance.futures_cancel_order, symbol=symbol, orderId=int(order_id)
            )
            logger.info("[Executor] %s 주문 취소 (id=%s)", symbol, order_id)
        except Exception as e:
            logger.warning("[Executor] %s 주문 취소 실패(무시): id=%s %s", symbol, order_id, e)

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
                "SELECT id, timestamp, action, entry_price, quantity "
                "FROM trades WHERE symbol = ? AND exit_price IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row is None:
                logger.info("[Executor] %s 마감할 미청산 행 없음", symbol)
                return
            tid, ts, action, entry, qty = row
            entry = float(entry or 0.0)
            qty = float(qty or 0.0)
            if exit_price is None or exit_price <= 0:
                logger.warning(
                    "[Executor] %s 청산 체결가 미상 → 진입가 중립 처리(PnL 0 근사)", symbol
                )
                exit_price = entry

            direction = 1.0 if action == "LONG" else -1.0
            pnl_usd = direction * (exit_price - entry) * qty
            entry_notional = entry * qty
            exit_notional = exit_price * qty
            if exit_commission is not None:
                # 청산 수수료는 실측, 진입 수수료는 taker 추정
                fees_usd = entry_notional * _DEFAULT_TAKER_FEE + exit_commission
            else:
                fees_usd = (entry_notional + exit_notional) * _DEFAULT_TAKER_FEE
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
