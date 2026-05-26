"""
governance/kill_switch.py
=====================================================================
KillSwitch — file-based 긴급 중단 + btc_risk_off 어댑터 통합

근거:
  - SYSTEM_DESIGN_BLUEPRINT.md §3.4.3 (Kill Switch 명세 — 파일 기반, 6조건 자동 활성화)
  - docs/REFACTOR_PLAN_v2_BLUEPRINT.md M1 (어댑터 패턴: file OR btc_is_halted)
  - 운영자 결정 #v2-4 (2026-05-26): 청사진 기준 + btc_risk_off 통합

설계 메모:
  - 어댑터 패턴: KillSwitch.is_active() 는 *3가지 소스* 중 하나라도 활성이면 True.
    1. KILLSWITCH 파일 존재 (수동/자동 모두)
    2. strategy.btc_risk_off.is_halted() (BTC -1.2%/6h, 메모리 상태머신)
    3. 향후 M4 의 governance.auto_trigger 조건
  - btc_risk_off 본문 0줄 수정 (CLAUDE.md TIER 1 #5 보호 파일 룰 정합)
  - KILLSWITCH 파일 경로는 env (`KILLSWITCH_FILE`) 우선, 기본값 `data/KILLSWITCH`
    (멀티 PC 호환 — 외장 SSD `E:/bot_data/state/` 운영자가 env 로 override).
  - activate/deactivate 는 *동기* — KillSwitch 활성화는 *즉시* 효과 발휘 필요.
  - audit_log 기록은 *선택* (AuditLogger 주입). 미주입 시 stderr 로그만.
=====================================================================
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 기본 KILLSWITCH 파일 경로 (env override 가능)
_DEFAULT_KILLSWITCH_PATH = "data/KILLSWITCH"


@dataclass(frozen=True)
class KillSwitchReason:
    """KillSwitch 활성화 사유 (audit_log 기록용)."""
    reason: str
    source: str           # manual/ops_agent/system/btc_risk_off/stage3/daily_loss_hard/...
    activated_at: datetime


def _resolve_killswitch_path() -> Path:
    """KILLSWITCH 파일 경로를 환경변수 우선으로 결정."""
    raw = os.environ.get("KILLSWITCH_FILE", _DEFAULT_KILLSWITCH_PATH)
    return Path(raw)


class KillSwitch:
    """청사진 §3.4.3 의 file-based KillSwitch + btc_risk_off 어댑터.

    사용:
        # 시작 시 매 거래 결정 전:
        if KillSwitch.is_active(btc_state=self.btc_state, now=now_utc):
            logger.critical("KillSwitch active — abort iteration")
            return

        # 수동 활성화:
        KillSwitch.activate(reason="manual halt", source="manual")

        # 운영자 수동 해제:
        KillSwitch.deactivate(by="operator")
    """

    @classmethod
    def path(cls) -> Path:
        """KILLSWITCH 파일 경로 (env override 반영)."""
        return _resolve_killswitch_path()

    @classmethod
    def is_active(
        cls,
        btc_state: Optional[object] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """KillSwitch 활성 여부 — 3가지 소스 중 하나라도 True 면 True.

        Args:
            btc_state: strategy.btc_risk_off.BTCRiskOffState 인스턴스 (선택).
                None 이면 btc_risk_off 체크 생략.
            now: 현재 UTC tz-aware datetime (btc_is_halted 평가용). None 이면
                datetime.now(timezone.utc).

        Returns:
            파일 존재 OR btc_is_halted 면 True.
        """
        # 1. 파일 기반 (수동/자동 활성화)
        if cls.path().exists():
            return True

        # 2. btc_risk_off 어댑터 (메모리 상태머신, 기존 코드 0줄 수정)
        if btc_state is not None:
            try:
                from strategy.btc_risk_off import is_halted as btc_is_halted

                eval_now = now if now is not None else datetime.now(timezone.utc)
                if eval_now.tzinfo is None:
                    eval_now = eval_now.replace(tzinfo=timezone.utc)
                if btc_is_halted(btc_state, eval_now):
                    return True
            except Exception as e:  # noqa: BLE001 — 안전 우선
                logger.warning("[KillSwitch] btc_risk_off 체크 실패: %s", e)

        # 3. (M4 신규) governance.auto_trigger 의 6조건 — 향후 통합

        return False

    @classmethod
    def activate(cls, reason: str, source: str) -> KillSwitchReason:
        """KILLSWITCH 파일 생성 + audit_log 기록 (선택).

        Args:
            reason: 활성화 사유 (예: "daily_loss -1.5% 도달").
            source: 활성화 소스 (manual / ops_agent / system / btc_risk_off /
                stage3 / daily_loss_hard / etc).

        Returns:
            KillSwitchReason 인스턴스 (활성화 시각 포함).
        """
        if not reason:
            raise ValueError("reason must not be empty")
        if not source:
            raise ValueError("source must not be empty")

        activated_at = datetime.now(timezone.utc)
        ks_path = cls.path()
        ks_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "activated_at": activated_at.isoformat(),
            "reason": reason,
            "source": source,
        }
        ks_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.critical(
            "[KillSwitch] ACTIVATED — reason=%s source=%s file=%s",
            reason, source, ks_path,
        )
        return KillSwitchReason(
            reason=reason, source=source, activated_at=activated_at,
        )

    @classmethod
    def deactivate(cls, by: str = "operator") -> None:
        """KILLSWITCH 파일 삭제 + audit_log 기록.

        Args:
            by: 해제 주체 (기본 "operator"). 청사진 §3.4.3: 운영자만 수동 해제.

        Raises:
            PermissionError: by != "operator" 일 때.
            FileNotFoundError: 파일이 이미 없는 경우 (이미 해제됨).
        """
        if by != "operator":
            raise PermissionError(
                f"Only operator can deactivate kill switch (got by={by!r})"
            )
        ks_path = cls.path()
        if not ks_path.exists():
            logger.warning("[KillSwitch] deactivate 요청 — 파일이 이미 없음")
            return
        ks_path.unlink()
        logger.info("[KillSwitch] DEACTIVATED by=%s file=%s", by, ks_path)

    @classmethod
    def get_status(cls) -> Optional[dict]:
        """현재 활성화 상태 정보 dict (없으면 None)."""
        ks_path = cls.path()
        if not ks_path.exists():
            return None
        try:
            return json.loads(ks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("[KillSwitch] 상태 파일 파싱 실패: %s", e)
            return {"reason": "unknown (file corrupted)", "source": "unknown"}
