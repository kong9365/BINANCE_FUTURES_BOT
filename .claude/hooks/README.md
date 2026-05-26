# Claude Code Hooks (v3.2.0 M6)

> 청사진 §3.4.1 + CLAUDE.md TIER 1 #5 보호 파일 + Polyphemus 자동 게이트 패턴

## Hooks 개요

본 디렉토리는 Claude Code의 *자동 게이트*를 정의한다. 매 도구 호출 전/후 + 운영자 입력 시 자동 검증.

### 3개 Hooks (Windows PowerShell)

| Hook | 트리거 | 역할 |
|---|---|---|
| `pre_tool_use.ps1` | Edit/Write/MultiEdit 호출 *전* | 보호 파일 차단 (CLAUDE.md #5) + 6 보호 자산 무결성 |
| `post_tool_use.ps1` | Edit/Write/MultiEdit 호출 *후* | `py_compile` + 관련 `pytest test_<name>.py` 자동 |
| `user_prompt_submit.ps1` | 운영자 prompt 전송 *전* | 위험 키워드 감지 (withdraw / 레버리지 10x+ / spot api 등) |

## 설치

`.claude/settings.local.json` 에 hooks 섹션 자동 등록 (M6 commit 에 포함). 별도 작업 X.

## 동작 확인

### pre_tool_use 검증
```powershell
# trading/executor.py 수정 시도 (차단되어야 함)
# Claude Code 세션에서 Edit/Write 호출 → 🛑 BLOCKED 메시지
```

### post_tool_use 검증
```powershell
# 신규 .py 파일 생성 시
# → 자동 py_compile + 관련 tests/test_*.py 실행
```

### user_prompt_submit 검증
```powershell
# 운영자가 "withdraw" 키워드 포함 prompt 전송
# → ⚠️ 위험 키워드 감지 경고 메시지
```

## Linux/Mac 사용자

본 hooks 는 *PowerShell* 스크립트. Linux/Mac 에서는 `.sh` 등가 스크립트 추가 작성 필요. 청사진 §3.4.1 의 bash 스크립트 참조.

## Polyphemus 패턴 참조

청사진 §3.4.1 의 Polyphemus 36,000줄 봇 hooks:
- 파일 작성마다 Gate 1 자동 실행 (본 `post_tool_use.ps1` 와 동일)
- 점진적 컨텍스트 로딩 (44% 비용 절감)
- 체크포인팅 (안전한 아키텍처 실험)

본 봇은 Phase 1.5 이후 점진 확장 권장.

## 트러블슈팅

### pre_tool_use 가 무시되는 경우
- `.claude/settings.local.json` 의 hooks 섹션 확인
- PowerShell ExecutionPolicy 확인: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### post_tool_use 가 너무 느린 경우
- 큰 테스트 파일 (test_main_integration 등) 은 자동 실행 안 함 (basename 매칭만)
- 운영자가 manual `pytest tests/ -q` 진행 권장

## 운영자 결정 (M6)

- pre_tool_use: 차단 (TIER 1 보호)
- post_tool_use: 검증 (실패 시 Claude에 피드백)
- user_prompt_submit: 경고만 (허용, exit 0)
