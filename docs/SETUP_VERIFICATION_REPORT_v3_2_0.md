# M11 Setup Verification Report (v3.2.0)

> Generated: 2026-05-26T18:54:21.218586+00:00
> Setup: `1d_tsmom_donchian_long_v1`
> PARAMS_HASH: `ffbacd920f619e7a4b2d04e9734ce066a7b7ce2070f23e086c271ecdb461caab`
> Universe: 18 symbols
> Interval: 1d
> Total trades: 216

## 운영자 권장 7기준 결과

```
=== 7-Criteria Validation Report (mode=trend) ===
Result: FAIL [X]

Metrics:
  n                          = 216 (min 200)
  Net PF                     = 1.260 (min 1.25)
  Expectancy_R               = 0.1178 (min 0.0)
  avg_win/avg_loss           = 1.357 (min 1.5)
  MDD %                      = 204.74% (max 25.0%)
  Single Symbol Max %        = 8.07% (max 25.0%)
  Top-3 Excluded PF          = 1.102 (min 1.0)
  Win Rate                   = 48.1%

Failed criteria (2): min_avg_win_loss_ratio, max_mdd_pct
```

## Status

❌ **FAIL** — 2 fails → DISABLED

청사진 §7.5 Stage 3 옵션 A/B/C 회의 권장
