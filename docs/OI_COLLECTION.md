# OI 데이터 누적 수집 (OI-급증 전략 검증용)

## 왜
Binance OI 이력(`futures_open_interest_hist`)은 **~30일치만** 제공한다. OI-급증
전략을 walk-forward로 제대로 검증(명세 §10-4: 거래 ≥200건, 다년 OOS)하려면 OI
시계열을 **지금부터 수개월 누적**해야 한다. 본 수집기를 주기 실행해 누적한다.

## 수집기 (read-only, 주문 없음)
```bash
python -m backtesting.collect_oi [--interval 1h] [--oi-period 1h] [--limit 500]
```
- 종목: `OI_COLLECT_SYMBOLS`(콤마구분) 우선, 없으면 기본 비보호 유동 12종목.
- 저장: `backtests/cache/<SYMBOL>_<interval>.csv` (gitignore). 재실행 시
  `merge_into`로 timestamp union 누적(멱등).
- 데이터: 공개 market data(klines + OI 이력). 실거래 주문/취소 없음.

## 스케줄 등록 (OS 레벨 — 무인 장기 누적 권장)

### Windows (현재 환경) — PowerShell 1회 등록
```powershell
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c ""C:\Python314\python.exe" -m backtesting.collect_oi >> logs\oi_collect.log 2>&1"' -WorkingDirectory "C:\Users\jaeho\OneDrive\Desktop\Cursor\BINANCE_FUTURES_BOT"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "BinanceOICollect" -Action $action -Trigger $trigger -Description "Hourly OI/OHLCV accumulation for OI-surge backtest validation" -Force
```
확인 / 즉시 실행 / 제거:
```powershell
schtasks /Query /TN BinanceOICollect /V /FO LIST
schtasks /Run   /TN BinanceOICollect
schtasks /Delete /TN BinanceOICollect /F
```

### Linux/macOS — cron
```cron
0 * * * * cd /path/to/repo && /usr/bin/python -m backtesting.collect_oi >> logs/oi_collect.log 2>&1
```

## 누적 후 검증 (수개월 뒤)
충분히 모이면(거래 ≥200건 분량) OI-급증 전략을 백테스트:
```python
from backtesting import data_loader as dl
from backtesting.backtest_engine import BacktestConfig, BacktestEngine
data = dl.load_universe([...], interval="1h")
cfg  = BacktestConfig(pairs=list(data), strategy="oi_surge",
                      oi_change_threshold_pct=1.5, price_change_threshold_pct=0.7)
result = BacktestEngine(cfg).run(data)   # total_trades / win_rate / expectancy_R / profit_factor
```

## ⚠️ 환경 주의 — OneDrive 경로에서는 스케줄 실행 실패
현재 저장소가 OneDrive 폴더(`C:\Users\<user>\OneDrive\...`) 아래에 있으면, Windows
**작업 스케줄러 비대화 컨텍스트가 OneDrive 가상화 경로로 cd/실행을 못 해** 작업이
"성공(0)"으로 보고되면서도 **실제로는 수집하지 않는다**(검증됨: 수동/직접 실행은
정상, 스케줄 실행은 로그 미생성·캐시 미갱신).

**해결(권장 순서):**
1. **저장소를 OneDrive 밖으로 이동** (예: `C:\bots\BINANCE_FUTURES_BOT`). 그 후 위
   등록 명령의 경로만 바꿔 재등록하면 스케줄 실행이 정상 작동한다. (가장 확실)
2. 또는 작업을 "사용자 로그온 시에만 실행"으로 두고 OneDrive 가 해당 세션에
   마운트·오프라인 사용 가능 상태인지 보장.
3. 검증: 스케줄 트리거 후 `logs/oi_collect.log` 에 새 줄이 추가되고
   `backtests/cache/*.csv` mtime 이 갱신되는지 확인.

> 직접 실행(`python -m backtesting.collect_oi`)은 OneDrive 경로에서도 정상이므로,
> 단기 누적은 수동/직접 실행으로도 가능하다. 무인 장기 누적은 위 해결책 필요.

## 주의
- 수집기는 데이터만 모은다. **실거래 GO와 무관(HOLD 유지).**
- ~30일 미만 데이터로의 임계 튜닝/수익성 판단은 **노이즈** — 절대 자본 투입 근거로 쓰지 말 것.
- 1시간 주기면 하루 24회 × 500포인트(1h)로 충분히 겹쳐 누락 없이 누적된다.
