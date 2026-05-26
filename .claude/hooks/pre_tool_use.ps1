# =====================================================================
# .claude/hooks/pre_tool_use.ps1 — 보호 파일 차단 (Windows PowerShell)
#
# 청사진 §3.4.1 #1 + CLAUDE.md TIER 1 #5 보호 파일 룰.
#
# 입력: stdin JSON {tool_name, tool_input}
# 출력: exit 0 = 허용, exit 1 = 차단
# =====================================================================

# stdin 에서 JSON 입력 읽기
$inputJson = $input | Out-String
if (-not $inputJson) { exit 0 }

try {
    $data = $inputJson | ConvertFrom-Json
} catch {
    exit 0  # JSON 파싱 실패 — 허용 (안전)
}

$toolName = $data.tool_name
$filePath = $data.tool_input.file_path

# Edit/Write/MultiEdit 외에는 무시
if ($toolName -notin @("Edit", "Write", "MultiEdit", "NotebookEdit")) {
    exit 0
}

if (-not $filePath) { exit 0 }

# CLAUDE.md TIER 1 #5 보호 파일 목록 (운영자 명시 변경 시만 허용)
$protected = @(
    "trading/executor.py",
    "trading/risk_manager.py",
    "data/capital_manager.py",
    "config/settings.py",
    "main_7590.py"
)

# 정규화 (Windows 경로 → Unix-like)
$normalized = $filePath -replace "\\", "/"

foreach ($p in $protected) {
    if ($normalized -like "*$p") {
        Write-Host "🛑 BLOCKED: $filePath 는 TIER 1 보호 파일 (CLAUDE.md #5)."
        Write-Host "    운영자 명시 승인 + 명세서 §섹션 view 필수."
        exit 1
    }
}

# 6 보호 자산 하드코딩 확인
if ($normalized -like "*config/settings.py") {
    $content = Get-Content $filePath -Raw -ErrorAction SilentlyContinue
    if ($content) {
        $required = @("BTCUSDT", "ETHUSDT", "HOLOUSDT", "CFXUSDT", "LYNUSDT", "INJUSDT")
        $missing = @()
        foreach ($s in $required) {
            if ($content -notmatch $s) { $missing += $s }
        }
        if ($missing.Count -gt 0) {
            Write-Host "🛑 BLOCKED: config/settings.py 에서 보호 자산 누락: $($missing -join ', ')"
            exit 1
        }
    }
}

exit 0
