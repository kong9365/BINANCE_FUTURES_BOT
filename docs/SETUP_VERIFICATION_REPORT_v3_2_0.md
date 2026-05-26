# M11 Setup Verification Report (v3.2.0)

> Generated: 2026-05-26T16:21:23.431859+00:00
> Setup: `1d_tsmom_donchian_long_v1`
> PARAMS_HASH: `ffbacd920f619e7a4b2d04e9734ce066a7b7ce2070f23e086c271ecdb461caab`
> Universe: 3 symbols
> Interval: 1d
> Total trades: 4

## 운영자 권장 7기준 결과

```
=== 7-Criteria Validation Report (mode=trend) ===
Result: FAIL [X]

Metrics:
  n                          = 4 (min 200)
  Net PF                     = 0.401 (min 1.25)
  Expectancy_R               = -0.2366 (min 0.0)
  avg_win/avg_loss           = 1.204 (min 1.5)
  MDD %                      = 27.10% (max 25.0%)
  Single Symbol Max %        = 0.56% (max 25.0%)
  Top-3 Excluded PF          = 0.000 (min 1.0)
  Win Rate                   = 25.0%

Failed criteria (6): min_n, min_pf, min_expectancy_r, min_avg_win_loss_ratio, max_mdd_pct, min_top3_excluded_pf

Notes:
  - Top-3 symbols excluded PF 0.00 < 1.0 — 집중 의심 (ZEC/LAB 단일 패턴)
```

## Status

❌ **FAIL** — 6 fails → DISABLED

청사진 §7.5 Stage 3 옵션 A/B/C 회의 권장
