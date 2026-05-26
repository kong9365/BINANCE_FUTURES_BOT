"""5-Agent Subagent 검토단 (v3.2.0 M3, 청사진 §3.2.2).

본 패키지는 청사진의 5-Agent 다관점 검증:
  - StrategyAgent — 알파/다중검정/학계 근거 + 운영자 7기준
  - RiskAgent — RiskManager wrap (7→5 매핑)
  - ExecutionAgent — fill_probability + slippage + 4-case
  - DataAgent — staleness + lookahead + 상장일
  - OpsAgent — SystemHealthMonitor wrap + ALCOA+ outbox

설계 원칙:
  - 모든 Agent 는 *읽기 위주* (청사진 §3.2.2)
  - 코드 작성은 *메인 세션* (Agent 가 X)
  - 기존 모듈 wrapping — 새 비즈니스 로직 0줄
  - 출력은 AgentReview (audit/agent_review.py)
  - 병렬+순차 워크플로우: orchestrator
"""

from agents.base import Agent
from agents.strategy_agent import StrategyAgent
from agents.risk_agent import RiskAgent
from agents.execution_agent import ExecutionAgent
from agents.data_agent import DataAgent
from agents.ops_agent import OpsAgent
from agents.orchestrator import AgentOrchestrator, OrchestratorResult

__all__ = [
    "Agent",
    "StrategyAgent", "RiskAgent", "ExecutionAgent", "DataAgent", "OpsAgent",
    "AgentOrchestrator", "OrchestratorResult",
]
