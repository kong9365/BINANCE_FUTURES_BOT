"""
strategy/ccs_lite.py
=====================================================================
CCS-Lite v1.1 — 8 Binary Kill Gates + 3-State 분류기.

설계 (docs/HANDOFF.md §CCS-Lite v1):
  Leverage Flush(6 조건 base trigger) 이벤트가 발생한 *후* 진입 가부를 결정하는
  *gating 필터*. 점수형 폐기 → 모두 binary AND. 하나라도 활성이면 BLOCK.

8 Kill Gates:
  1) BTC Risk-Off               (strategy.btc_risk_off, 이미 운영)
  2) BTC Trend Kill              (BTC 4h close < BTC 200d EMA)
  3) Symbol Trend Kill           (symbol 1h close < symbol 200d EMA)
  4) Macro Proximity Kill        (FOMC/CPI 24h 이내)
  5) Funding Extreme Kill        (funding |z-30d| > 2 AND 30분 내 0 회귀 없음)
  6) Consecutive Loss Cooldown   (strategy.loss_cooldown)
  7) Sector Exclusion            (strategy.sector_map)
  8) Funding Window Kill         (다음 펀딩 정산 < 20분)

3-State 분류 (LF + 전 gate 통과 시):
  - STRONG_TREND   : BTC trend ↑ AND symbol EMA alignment 상승
  - STRONG_SQUEEZE : funding 음수→0 회귀 AND OI 급감 가속
  - NEUTRAL        : 둘 다 아님

순수 함수. I/O 없음. 호출자가 모든 인풋 사전 계산해 넘김.
=====================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence


# ── 상태 enum ──────────────────────────────────────────────────────
STATE_STRONG_TREND = "STRONG_TREND"
STATE_STRONG_SQUEEZE = "STRONG_SQUEEZE"
STATE_NEUTRAL = "NEUTRAL"
STATE_BLOCKED = "BLOCKED"

# ── 게이트 reason code (Telegram·audit·DB 일관성) ────────────────
GATE_BTC_RISK_OFF = "BTC_RISK_OFF"
GATE_BTC_TREND = "BTC_TREND_KILL"
GATE_SYMBOL_TREND = "SYMBOL_TREND_KILL"
GATE_MACRO = "MACRO_PROXIMITY"
GATE_FUNDING_EXTREME = "FUNDING_EXTREME"
GATE_LOSS_COOLDOWN = "LOSS_COOLDOWN"
GATE_SECTOR = "SECTOR_EXCLUSION"
GATE_FUNDING_WINDOW = "FUNDING_WINDOW"


@dataclass
class CCSLiteConfig:
    """전 임계 사전확정. ±보정 자유도 0(CCS-Lite v1.1 §사전확정 합격 기준)."""

    # Gate 2/3 — EMA200 (200일을 1h 기준 = 4800봉, 너무 많음 → 1d 200봉 사용)
    btc_trend_ema_period: int = 200          # 4h interval → 200×4h ≈ 33일
    symbol_trend_ema_period: int = 200       # 1h interval → 200×1h ≈ 8일(짧음 — 빠른 추세 필터)

    # Gate 4 — Macro
    macro_block_hours: float = 24.0

    # Gate 5 — Funding extreme
    funding_zscore_window_days: int = 30
    funding_zscore_threshold: float = 2.0
    funding_reversion_minutes: float = 30.0  # 30분 내 0 방향 회귀 확인

    # Gate 8 — Funding window
    funding_window_block_minutes: float = 20.0  # 다음 펀딩까지 20분 미만이면 차단

    # 3-State 분류 임계
    state_squeeze_funding_neg_threshold: float = -0.0003   # 직전 펀딩 < -0.03% → 음수
    state_squeeze_funding_now_threshold: float = 0.0       # 현재 펀딩 ≥ 0 → 회귀
    state_squeeze_oi_acceleration_threshold: float = -0.05  # 직전 1h OI < -5% (LF 의 -3% 보다 강한 가속)


@dataclass
class KillGateResult:
    """8 kill gate 평가 결과.

    Attributes:
        blocked: 하나라도 활성이면 True.
        active_gates: 활성화된 gate reason code 리스트.
        details: gate별 디버그 메시지(로그·텔레그램용).
    """

    blocked: bool
    active_gates: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


def _ema(values: Sequence[float], period: int) -> Optional[float]:
    """마지막 EMA 값. SMA(period) 시드 + 표준 평활. 부족 시 None."""
    n = len(values)
    if period <= 0 or n < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def gate_btc_risk_off(btc_risk_off_active: bool) -> bool:
    """Gate 1 — BTC risk-off 활성 시 차단."""
    return bool(btc_risk_off_active)


def gate_btc_trend(btc_closes_4h: Sequence[float], cfg: CCSLiteConfig) -> bool:
    """Gate 2 — BTC 4h close < BTC 200봉 EMA → BLOCK.

    데이터 부족(EMA 계산 불가) 시 *보수적으로 차단*(True 반환).
    """
    if not btc_closes_4h:
        return True
    e = _ema(btc_closes_4h, cfg.btc_trend_ema_period)
    if e is None:
        return True
    return btc_closes_4h[-1] < e


def gate_symbol_trend(symbol_closes_1h: Sequence[float], cfg: CCSLiteConfig) -> bool:
    """Gate 3 — symbol 1h close < symbol 200봉 EMA → BLOCK."""
    if not symbol_closes_1h:
        return True
    e = _ema(symbol_closes_1h, cfg.symbol_trend_ema_period)
    if e is None:
        return True
    return symbol_closes_1h[-1] < e


def gate_macro_proximity(
    now: datetime,
    upcoming_event_times: Sequence[datetime],
    cfg: CCSLiteConfig,
) -> bool:
    """Gate 4 — 거시 이벤트 24h 이내(전·후)에 있으면 차단.

    Args:
        now: 현재 시각(UTC).
        upcoming_event_times: 24h 윈도 내 발생할 거시 이벤트 시각 리스트(UTC).
            호출자가 macro_event_analyzer.get_upcoming(hours=cfg.macro_block_hours) 로 사전 추출.
        cfg: 설정.

    Returns:
        24h 이내 이벤트 1건이라도 있으면 True.
    """
    if not upcoming_event_times:
        return False
    window = timedelta(hours=cfg.macro_block_hours)
    for evt in upcoming_event_times:
        delta = evt - now
        if -window <= delta <= window:
            return True
    return False


def gate_funding_extreme(
    funding_history: Sequence[float],
    current_funding: float,
    cfg: CCSLiteConfig,
) -> bool:
    """Gate 5 — 30d 펀딩 |z-score| > 2 AND 0 방향 회귀 없음 → 차단.

    회귀 정의: 현재 funding 의 절댓값이 직전 funding 의 절댓값보다 작거나 같으면 회귀.
    (3-state 의 squeeze 정의와 약간 다른 *방어적* 회귀 확인.)

    Args:
        funding_history: 직전 30d funding rate(8h 단위, ≈ 90개 표본).
        current_funding: 현재 funding.
        cfg: 설정.

    Returns:
        극단(z>2) + 회귀 없음 → True.
    """
    n = len(funding_history)
    if n < 5:
        return False  # 데이터 부족 시 차단하지 않음(false negative보다 false positive 회피)
    mean = sum(funding_history) / n
    var = sum((x - mean) ** 2 for x in funding_history) / max(n - 1, 1)
    std = var ** 0.5
    if std <= 0:
        # 분산 0(균등 분포 — 보통 funding 안정구간) → z 계산 불가.
        # 폴백: 평균 대비 절대 편차가 0.5%/8h 이상이면 명백한 anomaly → 차단.
        # 회귀 판정도 prev 와 동일하면 의미 없으므로 절대 임계 단독.
        return abs(current_funding - mean) > 0.005
    z = (current_funding - mean) / std
    if abs(z) <= cfg.funding_zscore_threshold:
        return False
    # 회귀: 현재 절댓값이 직전 절댓값 이하면 회귀 중 → 차단 안 함
    if n >= 1:
        prev = funding_history[-1]
        if abs(current_funding) <= abs(prev):
            return False
    return True


def gate_loss_cooldown(cooldown_active: bool) -> bool:
    """Gate 6 — strategy.loss_cooldown.is_cooldown_active 결과 위임."""
    return bool(cooldown_active)


def gate_sector_exclusion(
    candidate_symbol: str,
    held_symbols: Sequence[str],
) -> bool:
    """Gate 7 — 동일 섹터 보유 종목 존재 시 차단.

    sector_map.is_any_held_same_sector 위임.
    """
    from strategy.sector_map import is_any_held_same_sector
    return is_any_held_same_sector(candidate_symbol, held_symbols)


def gate_funding_window(
    now: datetime,
    next_funding_at: Optional[datetime],
    cfg: CCSLiteConfig,
) -> bool:
    """Gate 8 — 다음 펀딩 정산까지 N분 미만이면 차단(펀딩비 부담 회피).

    Args:
        now: 현재 시각(UTC).
        next_funding_at: 다음 펀딩 정산 예정 UTC 시각. None 이면 차단 안 함(데이터 미상).
        cfg: 설정.
    """
    if next_funding_at is None:
        return False
    delta = (next_funding_at - now).total_seconds() / 60.0
    return 0.0 <= delta < cfg.funding_window_block_minutes


def evaluate_kill_gates(
    *,
    btc_risk_off_active: bool,
    btc_closes_4h: Sequence[float],
    symbol_closes_1h: Sequence[float],
    now: datetime,
    upcoming_macro_events: Sequence[datetime],
    funding_history: Sequence[float],
    current_funding: float,
    loss_cooldown_active: bool,
    candidate_symbol: str,
    held_symbols: Sequence[str],
    next_funding_at: Optional[datetime],
    cfg: Optional[CCSLiteConfig] = None,
) -> KillGateResult:
    """8 게이트 동시 평가. 하나라도 활성이면 blocked=True.

    Returns:
        KillGateResult — blocked / active_gates / details.
    """
    cfg = cfg or CCSLiteConfig()
    active: List[str] = []
    details: List[str] = []

    if gate_btc_risk_off(btc_risk_off_active):
        active.append(GATE_BTC_RISK_OFF)
        details.append("BTC risk-off active (1h drop ≤ −1.2%)")
    if gate_btc_trend(btc_closes_4h, cfg):
        active.append(GATE_BTC_TREND)
        details.append("BTC 4h close below 200 EMA")
    if gate_symbol_trend(symbol_closes_1h, cfg):
        active.append(GATE_SYMBOL_TREND)
        details.append(f"{candidate_symbol} 1h close below 200 EMA")
    if gate_macro_proximity(now, upcoming_macro_events, cfg):
        active.append(GATE_MACRO)
        details.append("Macro event within ±24h window")
    if gate_funding_extreme(funding_history, current_funding, cfg):
        active.append(GATE_FUNDING_EXTREME)
        details.append(f"Funding |z30| > {cfg.funding_zscore_threshold} without reversion")
    if gate_loss_cooldown(loss_cooldown_active):
        active.append(GATE_LOSS_COOLDOWN)
        details.append("Consecutive loss cooldown active")
    if gate_sector_exclusion(candidate_symbol, held_symbols):
        active.append(GATE_SECTOR)
        details.append(f"Same-sector position already held for {candidate_symbol}")
    if gate_funding_window(now, next_funding_at, cfg):
        active.append(GATE_FUNDING_WINDOW)
        details.append(
            f"Next funding settlement < {cfg.funding_window_block_minutes:.0f}min away"
        )

    return KillGateResult(
        blocked=len(active) > 0,
        active_gates=active,
        details=details,
    )


def classify_state(
    *,
    btc_closes_4h: Sequence[float],
    symbol_closes_1h: Sequence[float],
    prev_funding: Optional[float],
    current_funding: Optional[float],
    recent_oi_change_pct: Optional[float],
    cfg: Optional[CCSLiteConfig] = None,
) -> str:
    """LF + 전 gate 통과 후 시장 컨텍스트 분류.

    Returns:
        STATE_STRONG_TREND / STATE_STRONG_SQUEEZE / STATE_NEUTRAL.

    노트:
        - STRONG_TREND > STRONG_SQUEEZE 우선순위. 둘 다 매칭되면 TREND.
        - 둘 다 미매칭이면 NEUTRAL (호출자는 NEUTRAL 시 거래 안 함이 일반적).
    """
    cfg = cfg or CCSLiteConfig()

    # ── STRONG_TREND: BTC trend ↑ AND symbol EMA alignment 상승 ──
    btc_up = False
    if btc_closes_4h:
        e_btc = _ema(btc_closes_4h, cfg.btc_trend_ema_period)
        if e_btc is not None and btc_closes_4h[-1] > e_btc:
            btc_up = True
    sym_up = False
    if symbol_closes_1h:
        e_sym = _ema(symbol_closes_1h, cfg.symbol_trend_ema_period)
        if e_sym is not None and symbol_closes_1h[-1] > e_sym:
            sym_up = True
    if btc_up and sym_up:
        return STATE_STRONG_TREND

    # ── STRONG_SQUEEZE: funding 음수→0 회귀 AND OI 급감 가속 ──
    squeeze_funding = False
    if prev_funding is not None and current_funding is not None:
        if (
            prev_funding < cfg.state_squeeze_funding_neg_threshold
            and current_funding >= cfg.state_squeeze_funding_now_threshold
        ):
            squeeze_funding = True
    squeeze_oi = False
    if recent_oi_change_pct is not None:
        if recent_oi_change_pct <= cfg.state_squeeze_oi_acceleration_threshold:
            squeeze_oi = True
    if squeeze_funding and squeeze_oi:
        return STATE_STRONG_SQUEEZE

    return STATE_NEUTRAL
