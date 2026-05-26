"""Streamlit 봇 운영 + 시장 차트 통합 대시보드 (v3.2.0 M10).

운영자 결정 2026-05-27: M9 봇 가동 확인 후 "실시간 차트 반영" 요청에 따라
*읽기 전용 localhost UI* 도입.

설계 원칙:
  - DB 쿼리만 (거래 결정 영향 X, CLAUDE.md TIER 1 #10 정합)
  - localhost 만 (보안 — `--server.address localhost`)
  - 봇 (main_7590) 과 *별도 프로세스* — 영향 0
  - 기존 자산 활용: audit.chain.verify_chain, governance.kill_switch, registry.setup_registry, data.aggregator_1m

사용:
    scripts/run_dashboard.bat    # Windows
    또는
    streamlit run dashboard/app.py --server.address localhost --server.port 8501
"""
