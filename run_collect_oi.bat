@echo off
cd /d "C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT"
echo [%date% %time%] start >> logs\oi_collect.log
"C:\Python314\python.exe" -m backtesting.collect_oi >> logs\oi_collect.log 2>&1
echo [%date% %time%] exit %ERRORLEVEL% >> logs\oi_collect.log
