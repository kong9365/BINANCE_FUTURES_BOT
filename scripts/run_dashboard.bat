@echo off
REM =====================================================================
REM Binance Futures Bot v3.2.0 M10 — Streamlit 대시보드 실행 (Windows)
REM
REM 사용:
REM   scripts\run_dashboard.bat
REM
REM 브라우저: http://localhost:8501
REM 종료: Ctrl+C
REM =====================================================================

cd /d "%~dp0\.."

echo [Dashboard] Streamlit 시작 — http://localhost:8501
streamlit run dashboard/app.py --server.address localhost --server.port 8501 --browser.gatherUsageStats false

pause
