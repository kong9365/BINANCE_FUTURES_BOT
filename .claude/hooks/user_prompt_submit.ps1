# =====================================================================
# .claude/hooks/user_prompt_submit.ps1 — 위험 키워드 감지
#
# 청사진 §3.4.1 #3 — 운영자 입력에 위험 키워드 발견 시 알림.
#
# 입력: stdin JSON {prompt}
# 출력: exit 0 = 허용 (단 경고 메시지는 Claude에 전달)
# =====================================================================

$inputJson = $input | Out-String
if (-not $inputJson) { exit 0 }

try {
    $data = $inputJson | ConvertFrom-Json
} catch {
    exit 0
}

$prompt = $data.prompt
if (-not $prompt) { exit 0 }

# 위험 키워드 패턴
$dangerPatterns = @(
    "withdraw",
    "출금",
    "레버리지\s*1[0-9]+x",       # 10x 이상
    "leverage\s*1[0-9]+",
    "remove\s+protected_symbols",
    "manual_unblock",
    "spot.*api"
)

$matched = @()
foreach ($p in $dangerPatterns) {
    if ($prompt -match $p) {
        $matched += $p
    }
}

if ($matched.Count -gt 0) {
    Write-Host "⚠️ 위험 키워드 감지: $($matched -join ', ')"
    Write-Host "    CLAUDE.md TIER 1 룰 재확인 필요"
    # exit 0 — 허용하되 Claude/운영자에게 경고
}

exit 0
