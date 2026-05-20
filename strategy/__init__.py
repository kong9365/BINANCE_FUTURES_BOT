"""
strategy/
=====================================================================
신호 필터·전략 게이트 패키지.

모듈:
  - regime_detector.py  RegimeDetector — 5종 시장 레짐 감지 (§8-1)
  - cost_guard.py       CostGuard — 비용 차감 기대값 게이트 (§8-2)
  - pair_whitelist.py   PairWhitelist — 페어 화이트리스트 + 보호 종목 차단 (§8-7 + 부록 E-3)
  - oi_filter.py        OIFilter — OI 변화 + 레짐 컨텍스트 신호 등급 (§8-8 Layer 3)
  - quality_gate.py     QualityGate — 레짐별 임계 품질 점수 게이트 (§8-8 Layer 3)
=====================================================================
"""
