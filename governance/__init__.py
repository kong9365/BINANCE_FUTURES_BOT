"""Governance Layer (v3.2.0, 청사진 §3.4 + §7.5).

본 패키지는 청사진의 4번째 거버넌스 레이어를 구현:
  - kill_switch: file-based 긴급 중단 + btc_risk_off 어댑터 통합 (M1)
  - preflight: 시스템 시작 시 1회 검증 (M1)
  - auto_trigger: 6가지 자동 활성화 조건 (M4)
  - stage3_rule: 90d -10% / MDD -25% 백스톱 (M4)
  - risk_hierarchy: 2-tier (warn -1.0% / hard -1.5%) (M4)
  - slack_command_handler: Slack `/bot halt` 명령 (M5)

M1 시점: kill_switch.py + preflight.py 만 구현.
"""

from governance.kill_switch import KillSwitch, KillSwitchReason

__all__ = ["KillSwitch", "KillSwitchReason"]
