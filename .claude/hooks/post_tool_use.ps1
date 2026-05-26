# =====================================================================
# .claude/hooks/post_tool_use.ps1 — 자동 py_compile + 관련 pytest
#
# 청사진 §3.4.1 #2 + Polyphemus 패턴.
#
# 입력: stdin JSON {tool_name, tool_input, tool_response}
# 출력: exit 0 = OK, exit 1 = 컴파일 또는 테스트 실패 (Claude에 피드백)
# =====================================================================

$inputJson = $input | Out-String
if (-not $inputJson) { exit 0 }

try {
    $data = $inputJson | ConvertFrom-Json
} catch {
    exit 0
}

$toolName = $data.tool_name
$filePath = $data.tool_input.file_path

if ($toolName -notin @("Edit", "Write", "MultiEdit")) {
    exit 0
}

if (-not $filePath) { exit 0 }
if ($filePath -notlike "*.py") { exit 0 }

# 1. py_compile (구문 검증)
$compileResult = & python -m py_compile $filePath 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "🛑 py_compile 실패: $filePath"
    Write-Host $compileResult
    exit 1
}

# 2. 관련 테스트 자동 실행 (있으면)
$baseName = [System.IO.Path]::GetFileNameWithoutExtension($filePath)
$testFile = "tests/test_$baseName.py"
if (Test-Path $testFile) {
    Write-Host "▶ 관련 테스트 실행: $testFile"
    & python -m pytest $testFile -q --tb=line
    if ($LASTEXITCODE -ne 0) {
        Write-Host "🛑 관련 테스트 실패: $testFile"
        exit 1
    }
}

exit 0
