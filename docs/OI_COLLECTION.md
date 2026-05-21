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
> 수집 전용 실행 위치는 `C:\bots\BINANCE_FUTURES_BOT`(OneDrive 밖, GitHub fresh clone +
> `.env` 복사). 래퍼는 `run_collect_oi.bat`(start/exit 로그 남김). **배터리 설정 필수**
> — 아래 `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`(노트북 기본값이면 미실행, ⚠️ 아래 참조).
```powershell
$action   = New-ScheduledTaskAction -Execute "C:\bots\BINANCE_FUTURES_BOT\run_collect_oi.bat" -WorkingDirectory "C:\bots\BINANCE_FUTURES_BOT"
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "BinanceOICollect" -Action $action -Trigger $trigger -Settings $settings -Description "Hourly OI/OHLCV accumulation (C:\bots) for OI-surge backtest validation" -Force
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

## ⚠️ 진짜 원인은 OneDrive가 아니라 "배터리 전원 시 시작 안 함" 설정 (2026-05-21 정정)
초기엔 OneDrive 가상화 경로가 스케줄 실행을 막는다고 추정했으나, **검증 결과 오진**이었다.
Windows 작업의 기본 설정 `DisallowStartIfOnBatteries=True` + `StopIfGoingOnBatteries=True`
때문에, **노트북이 배터리 전원이면 작업이 시작조차 안 되면서도 `schtasks /Run`은
"성공(0)"으로 보고**한다(로그 미생성·캐시 미갱신). 이게 OneDrive에서도, C:\bots에서도
동일하게 나타난 진짜 원인이다(직접/수동 실행은 어디서든 정상이라 OneDrive를 의심했던 것).

**해결(검증됨):**
1. 작업 설정에 `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries` 적용(위 등록
   명령에 포함). 누락 실행 대비 `-StartWhenAvailable`도 권장.
   - 기존 작업만 고치려면:
     ```powershell
     $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
     Set-ScheduledTask -TaskName "BinanceOICollect" -Settings $s
     ```
2. **검증**: `schtasks /Run /TN BinanceOICollect` 후 `logs/oi_collect.log`에 새 `exit 0`
   줄이 추가되고 `backtests/cache/*.csv` mtime이 갱신되는지 확인.

> C:\bots(OneDrive 밖) 이전은 했으나 — **배터리 설정이 진짜 원인이므로 위치 자체는 무관**.
> 직접 실행(`python -m backtesting.collect_oi`)은 어느 경로에서도 정상이라, 단기 누적은
> 수동 실행으로도 가능하다.

## 주의
- 수집기는 데이터만 모은다. **실거래 GO와 무관(HOLD 유지).**
- ~30일 미만 데이터로의 임계 튜닝/수익성 판단은 **노이즈** — 절대 자본 투입 근거로 쓰지 말 것.
- 1시간 주기면 하루 24회 × 500포인트(1h)로 충분히 겹쳐 누락 없이 누적된다.
