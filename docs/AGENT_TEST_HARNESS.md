# Agent 운영 흐름 테스트 하네스

## 목적

ChatGPT나 Agent가 실제 운영 DB, 외부 API, Windows 작업 스케줄러를 변경하지 않고 변경 범위에 맞는 운영 계약을 빠르게 검증한다. 개발 중에는 관련 시나리오만 선택하고, 광범위한 변경이나 하네스 전체 점검일 때만 `all`을 사용한다.

현재 하네스는 다음 영역을 다룬다.

- 2단계 군집 순차 호출과 토큰·TPM 계약
- Windows 예약 스케줄러 명령·상태 해석
- 최신 데이터 수집의 짧은 DuckDB 연결, 부분 성공, 수집 후 정리 순서
- 저장 자료 정리의 보존·삭제 기준
- 주제 방향 자동 생성의 요청·검증·저장·재실행 멱등성
- 실제 DB를 변경하지 않는 운영 진단 보고서·CLI·표본 판단 계약
- 버튼·작업·Gemini 전송 운영 로그와 앱 supervisor·웹 업데이트 계약
- 제작 화면 이동, 자동/수동 글쓰기 기반, AI 요청서 → AI 결과 → 편집 → 발행 보조 계약
- Luna High 품질 감사의 1회 검수·1회 최소 수정 계약
- 대표 이미지 생성 계획, 공식 공개 페이지 캡처 실행 계약과 안전한 실패 전환
- AdSense 후보·블로그 추천 표시 계약
- 개발 하네스와 `apply_update` 안전 계약

## 실행

프로젝트 루트에서 필요한 시나리오를 선택한다.

```powershell
.\run_agent_harness.bat clustering
.\run_agent_harness.bat scheduler
.\run_agent_harness.bat latest-data
.\run_agent_harness.bat cleanup
.\run_agent_harness.bat topic-angles
.\run_agent_harness.bat diagnostics
.\run_agent_harness.bat operations
.\run_agent_harness.bat workflow
.\run_agent_harness.bat harness
```

여러 영역을 함께 바꿨다면 필요한 시나리오를 순서대로 지정한다.

```powershell
.\scripts\check_harness.ps1 -Scenario latest-data,cleanup,diagnostics
```

광범위한 운영 계약 변경이나 하네스 전체 정합성 점검에서는 다음을 사용한다.

```powershell
.\run_agent_harness.bat all
```

지원 시나리오와 실제 선택 테스트를 확인하려면 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe scripts\agent_test_harness.py --list
```

작업 브랜치 적용기가 특정 변경 파일을 어떤 검증으로 분류하는지 확인하려면 다음처럼 `--resolve-targets`를 사용한다.

```powershell
.\.venv\Scripts\python.exe scripts\agent_test_harness.py --resolve-targets src/services/adsense_candidate_service.py src/trend_candidate_blog_recommendation_ui.py
```

`check_harness.ps1`은 프로젝트 `.venv`, `venv`, 실행 가능한 시스템 Python 순으로 선택한다. 프로젝트 가상환경이 있으면 항상 우선한다. 지원 시나리오·별칭·테스트 묶음과 작업 브랜치용 명시적 파일 분류표는 `scripts/agent_test_harness.py`가 단일 기준이며 BAT와 PowerShell 진입점은 같은 목록을 다시 정의하지 않는다.

`apply_update.bat work/<다음 버전>`은 실제 적용 직전 `beforeHead..targetHead` 증분 diff를 `--resolve-targets`에 넘긴다. 결과는 다음 네 모드 중 하나다.

- `no_op`: 적용 대상 커밋 차이가 없을 때 최소 위생 검사
- `doc_only`: 문서·위생 규칙만 바뀐 경우 텍스트 검사와 문서 계약 테스트
- `selective`: 1~2개 기존 시나리오 경계에 명확히 속하면 해당 하네스만 실행
- `fallback_all`: 핵심 파일, 분류되지 않은 파일, 3개 이상 영역, diff 계산 실패 등 안전 경계에서 전체 pytest 실행

라우팅 JSON은 PowerShell 7과 Windows PowerShell 5.1 모두에서 안정적으로 읽히도록 ASCII-safe로 출력하고, PowerShell에서 역직렬화한 뒤 사람이 읽는 한글 이유를 표시한다.

## 시나리오별 역할

### `clustering`

1·2차 군집의 순차 실행, 희소 응답, 토큰·진행 상태, 군집 작업 UI 계약을 임시 데이터와 가짜 응답으로 검증한다.

### `scheduler`

Windows 예약 작업 명령 생성과 상태·쿼터 해석을 검증한다. 실제 `schtasks /Create`·`/Delete`는 실행하지 않는다.

### `latest-data`

다음 순서를 임시 DB와 가짜 수집 흐름으로 검증한다.

```text
출처 수집·저장
→ 자동 정리
→ 군집·통합 순위 준비
```

수집 실패 시 자동 정리가 실행되지 않는 것도 확인한다. Streamlit 경로의 실제 순서는 원본 함수 본문만 보지 않고 `src/__init__.py`가 설치하는 `post_collection_cleanup_runtime` 계약까지 포함해 판단한다.

### `cleanup`

원본 보존기간, 연결된 사용자 자료 보호, 관련 군집·이력 정리 계약을 임시 DB로 검증한다.

### `topic-angles`

주제 방향 요청·검증·부분 성공·저장과 2단계 군집 완료 뒤 보류된 방향 생성 재개 조건을 검증한다. 실제 Gemini API는 호출하지 않는다.

### `diagnostics`

`scripts/report_operation_diagnostics.py`, `scripts/report_source_analysis_limits.py`, `scripts/report_p2_diagnostics.py`와 대응 읽기 전용 진단의 출력·실패 표본·스냅샷 재사용·throttle 우선순위·NAVER·Daum 전체 범위 진단·P2 통합 진입점을 검증한다. 테스트용 임시 DB만 사용하며 생산 설정이나 실제 운영 DB를 변경하지 않는다. 군집 품질이나 deterministic baseline 판단을 수정할 때는 관련 `clustering` 시나리오도 함께 선택한다.

### `operations`

프로그램 로그, 수집 실행 ID 묶음, 숫자·시간 표시, 앱 supervisor, 웹 업데이트 실행 계약을 검증한다. 실제 앱을 장시간 실행하거나 포트를 점유하지 않는다.

### `workflow`

AI 요청서 → AI 결과 가져오기 → 글 편집 → 발행 보조의 Streamlit 상태·이동 계약과 함께 다음 제작 기반을 검증한다.

- 자동/수동 작성 추천과 0원 자동 작성 모델 fallback 계약
- Luna High 검수 JSON, `revision_requests`, `keep_points`, 1회 수정 제한
- 공식 캡처 task/schema와 대표 이미지 생성 계약
- 공개 URL 안전 검사, Fake 캡처 및 public capture runtime의 안전한 실패 전환
- 콘텐츠 workflow UI 회귀
- AdSense 후보 판단, 블로그 추천 서비스와 글감 추천 표시 UI

이 시나리오는 **실제 외부 API 호출이나 실제 브라우저 렌더링을 실행했다는 뜻이 아니다.** 하네스는 `CONTENT_TREND_BROWSER_SMOKE=0`을 강제해 public-capture 실브라우저 테스트를 건너뛴다. 실제 Chrome/Edge 공개 페이지 스모크는 별도 로컬/Agent 검증에서만 명시적으로 실행한다.

### `harness`

`run_agent_harness.bat`, `scripts/check_harness.ps1`, `scripts/agent_test_harness.py`, `apply_update` 작업·최종 모드와 저장소 텍스트 보호 계약을 검증한다. 개발 하네스나 적용 도구를 수정했을 때 가장 먼저 실행한다.

## 검증 선택 원칙

- 먼저 변경 파일과 직접 연결된 테스트 또는 위 시나리오를 실행한다.
- 실패를 수정했다면 영향받은 같은 시나리오를 다시 실행한다.
- 새 테스트나 새 파일이 기존 시나리오 책임에 들어가면 해당 `SCENARIO_TESTS` 묶음과 `classify_file()` 경계를 함께 갱신하고, 별도 시나리오는 만들지 않는다.
- `src/services/content_quality_*`, `tests/test_content_quality_*`, `tests/test_content_pack_*`는 제작 흐름의 직접 계약이므로 `workflow`로 분류한다.
- 여러 테스트가 반복해서 함께 변경되고 별도 확인 경로가 있어야 선택 실수를 줄일 수 있을 때만 새 시나리오를 만든다. 단일 파일·일회성 검증은 직접 pytest로 유지한다.
- 문서만 변경했고 실행 계약이 바뀌지 않았다면 텍스트 검사와 해당 문서 계약 테스트를 우선하며 전체 pytest를 기계적으로 반복하지 않는다.
- 작업 브랜치 적용에서는 `no_op / doc_only / selective / fallback_all` 라우팅을 사용하고, 안전하게 분류할 수 없는 파일을 단지 속도를 위해 억지로 기존 시나리오에 넣지 않는다.
- GitHub CI의 전체 pytest와 릴리스 최종 검증은 통합 게이트로 유지한다. 같은 HEAD에서 이미 유효하게 통과했고 이후 영향을 받지 않은 전체 검증은 로컬에서 이유 없이 반복하지 않는다.

## 안전 경계

하네스는 다음 원칙을 고정한다.

- 실제 `data\content_trend_tracker.duckdb`를 열지 않는다.
- `.env`의 Gemini, NAVER, Kakao 인증값을 사용하지 않는다.
- OpenRouter, Groq, OpenCode, Cloudflare 인증 환경값도 하네스 실행에서 비운다.
- 외부 네트워크 요청 대신 테스트 어댑터와 가짜 AI 응답을 사용한다.
- `CONTENT_TREND_BROWSER_SMOKE=0`을 강제해 실제 Chrome/Edge 외부 페이지 캡처를 실행하지 않는다.
- `schtasks /Create`, `/Delete`를 실제로 실행하지 않는다.
- pytest 임시 DB·캐시·산출물은 시스템 임시 디렉터리에만 만든다.
- 저장소 파일과 Git 상태를 수정하지 않는다.

따라서 하네스 통과만으로 실제 수집 결과, 실제 AI 품질, 실제 브라우저 동작, 실제 Windows 작업 스케줄러 등록 상태까지 통과했다고 해석하지 않는다.

실제 공개 페이지 캡처 스모크가 필요할 때만 로컬/Agent에서 별도로 실행한다.

```powershell
$env:CONTENT_TREND_BROWSER_SMOKE = "1"
python -m pytest -q -p no:cacheprovider tests/test_content_workflow_public_capture_executor.py
Remove-Item Env:CONTENT_TREND_BROWSER_SMOKE -ErrorAction SilentlyContinue
```

## 하네스 확장 절차

1. 변경 영역이 기존 시나리오에 속하는지 먼저 확인한다.
2. 기존 경계에 속하면 `SCENARIO_TESTS`의 해당 묶음과 `classify_file()`의 경계를 함께 확장하고 대표 변경 파일이 `selective`로 분류되는 계약 테스트를 추가한다.
3. 독립 영역의 여러 테스트가 반복해서 함께 변경되고 직접 테스트 선택이 누락되기 시작할 때만 `SCENARIO_TESTS`와 `SCENARIO_ORDER`에 새 시나리오를 추가한다.
4. 시나리오 책임이 달라졌다면 하네스 계약 테스트와 이 문서의 라우팅 설명을 함께 갱신한다.
5. BAT·PowerShell에 시나리오 enum이나 파일 분류표를 복제하지 않는다. 현재의 단순 명시적 분류표보다 복잡한 의존성 그래프 기반 선택기, Git Hook, Hook 기반 자동 실행, 다중 Agent 오케스트레이션은 명확한 반복 비용과 검증 이득이 확인된 뒤 별도 작업으로 검토한다.

## Agent 보고 형식

검증 전용 Agent를 사용하는 경우 다음만 보고한다.

1. 실행한 시나리오와 명령
2. 시나리오별 종료 코드와 통과·실패
3. 실패한 테스트 이름과 핵심 오류
4. 실제 DB·API·스케줄러를 사용하지 않았다는 확인
5. 남은 수동 검증 항목

검증 전용 Agent는 하네스 실행 과정에서 코드 수정, `git add`, commit, push를 수행하지 않는다.
