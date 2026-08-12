# AGENTS.md

## 프로젝트 기준

- 저장소: `honggee486-dot/content-trend-tracker`, 기본 브랜치: `main`
- 기술: Python, Streamlit, DuckDB, pandas, pytest
- 앱 진입점: `app.py`
- 수집·분석 진입점: `scripts/refresh_trends.py`
- DB 초기화: `scripts/init_database.py`
- 최신 `origin/main`과 실제 파일을 기준으로 판단하고, 작업 중이면 현재 `work/*` 브랜치와의 관계를 먼저 확인한다.
- 과거 대화·커밋·테스트 결과는 현재 사실로 재사용하지 않는다.

## 탐색 원칙

- 저장소 전체나 과거 이력을 먼저 읽지 않는다. 이번 요청에 필요한 코드, 직접 호출부, 설정, 관련 테스트와 기준 문서만 확인한다.
- `README.md`, `AI_CONTEXT.md`, `docs/NEXT_WORK.md`도 작업과 직접 관련된 부분만 읽는다. 릴리스·운영 계약을 바꾸지 않는 수정에서 모든 문서를 반복 확인하지 않는다.
- Streamlit 실행 흐름이나 함수 호출 순서를 판단할 때는 직접 보이는 호출만으로 결론내리지 않는다. `src/__init__.py`가 설치하는 `src/services/*_runtime.py` 래퍼와 관련 회귀 테스트를 먼저 확인한다. 실제 런타임 순서가 원본 함수 본문과 다를 수 있다.
- 새 구조를 만들기 전에 이미 있는 서비스·런타임 계약·테스트 하네스를 재사용할 수 있는지 확인한다.

## 데이터·운영 안전

- 기존 DuckDB 데이터와 글감·자료팩·AI 기록·초안·리비전·사실 확인·발행 기록을 보존한다.
- DB 변경은 추가형 호환 마이그레이션을 우선하고 기존 테이블·컬럼·상태 의미를 삭제하거나 바꾸지 않는다.
- 외부 API 대기 중 DuckDB 연결을 장시간 유지하지 않으며 한 출처나 Gemini 실패가 다른 수집 성공을 취소하지 않게 한다.
- YouTube 연동은 외부 저장소가 만든 Parquet를 읽는 단방향 계약을 유지한다.
- 자동 로그인, 쿠키 저장, CAPTCHA 우회와 무인 자동 게시를 추가하지 않는다.
- `.env`, 비밀정보, 실제 DB·WAL·잠금, Parquet, 로그, 캐시, 가상환경, 빌드·임시 산출물을 수정·복사·커밋하지 않는다. `.gitignore`와 `make_safe_upload_zip.bat`의 보호 대상도 함께 따른다.
- 실제 DB, 외부 API, 브라우저 인증 상태, Windows 작업 스케줄러 쓰기는 명시적으로 필요한 운영 검증이 아니면 자동 실행하지 않는다.
- Public 전환 판단은 현재 파일만으로 끝내지 않는다. `docs/PUBLIC_READINESS.md`와 `scripts/check_public_readiness.py`를 기준으로 원격 heads/tags, PR audit refs, reachable history, Secret scanner 결과와 GitHub 공개 메타데이터를 별도로 확인하며 Secret 실제 값은 출력하지 않는다.

## 작업 종류별 확인 경로

- **수집·원문·정리·잠금**: `scripts/refresh_trends.py`, `src/adapters/`, `src/services/trend_discovery_service.py`, `data_maintenance_service.py`, `collection_history_service.py`, `trend_refresh_lock_service.py`, `src/__init__.py`와 관련 `*_runtime.py`; 필요 시 `docs/OPERATION_LOGS.md`.
- **1·2차 군집**: `trend_discovery_service.py`, `src/services/trend_cluster_*`, `trend_clustering_*`, `scripts/process_cluster_backlog.py`, `docs/TREND_CLUSTERING_RUNTIME.md`.
- **Gemini 주제 방향**: `src/config.py`, `gemini_*`, `topic_angle_*`, 관련 생성·저장 서비스와 테스트. 운영 표본 판단까지 바꾸는 경우에만 읽기 전용 운영 진단 경로를 추가 확인한다.
- **읽기 전용 운영 진단**: `scripts/report_operation_diagnostics.py`, `src/services/operation_diagnostic_report_service.py`, 관련 `*_diagnostic_*` 서비스와 `docs/P2_OPERATION_DIAGNOSTIC.md`. 군집 품질·baseline 진단이 포함되면 `src/__init__.py`가 설치하는 관련 `trend_clustering_*_runtime.py`도 함께 확인한다.
- **앱 실행·웹 업데이트·운영 로그**: `app.py`, `run_app.bat`, `stop_app.bat`, `scripts/app_supervisor.ps1`, `web_update_*`, `program_log_*`, 관련 `*_runtime.py`, `docs/APP_RUNTIME.md`, `docs/WEB_UPDATE.md`, `docs/OPERATION_LOGS.md`.
- **제작 흐름·발행 보조**: `workflow_navigation_service.py`, `content_pack_*`, `ai_result_parser.py`, `draft_*`, `fact_check_*`, `publish_*`와 해당 UI. 플랫폼 연동을 건드릴 때만 `chrome_extension/` 또는 Blogger 관련 코드를 추가 확인한다.
- **DB 스키마·마이그레이션**: `src/database.py`, 영향을 받는 서비스, `tests/test_database.py`와 해당 임시 DB 테스트. 실제 DB로 마이그레이션 시험하지 않는다.
- **적용 도구·개발 하네스**: `AGENTS.md`, `docs/AGENT_TEST_HARNESS.md`, `docs/HARNESS_LESSONS.md`, `run_agent_harness.bat`, `scripts/check_harness.ps1`, `scripts/agent_test_harness.py`, `apply_update.bat`, `scripts/apply_update_work.ps1`, `scripts/apply_update_release.ps1`, `scripts/apply_update.ps1`과 관련 계약 테스트.
- **Public 공개 준비**: `.gitignore`, `.env.example`, `docs/PUBLIC_READINESS.md`, `scripts/check_public_readiness.py`, 공개 대상 문서와 GitHub branch/tag/PR/Actions 메타데이터. 실제 visibility 변경과 history rewrite는 안전 검증과 별도 작업으로 분리한다.

## 앱·적용 도구 핵심 계약

- 앱은 `run_app.bat`의 전경 supervisor가 전용 루프백 포트 `8518`에서 단일 인스턴스로 관리한다.
- 런타임 PID·시작 시각·포트는 `%LOCALAPPDATA%\content-trend-tracker`에 기록하고 저장소 `data`에는 쓰지 않는다.
- `stop_app.bat`은 등록 PID와 시작 시각이 맞는 프로세스만 종료하며 모든 Python·Streamlit 프로세스를 일괄 종료하지 않는다.
- 웹 업데이트는 현재 supervisor에 적용 요청을 전달하고 같은 supervisor가 적용·재시작을 담당한다. 세부 계약은 `docs/APP_RUNTIME.md`를 따른다.
- `apply_update.bat work/<다음 버전>`은 깨끗한 작업 트리에서 작업 브랜치를 비강제 fast-forward하고 검증할 뿐 로컬·원격 `main`을 merge·push하지 않는다.
- 최종 릴리스 적용은 `main` 또는 깨끗한 `work/*` 브랜치에서 시작할 수 있다. 깨끗한 `work/*`에서 시작하면 `scripts/apply_update_release.ps1`이 `main` 전환을 안전하게 담당하고, 실패 시 가능하면 원래 작업 브랜치로 복귀한다. 사용자가 릴리스 전에 수동으로 `main`으로 전환하는 것을 전제로 하지 않는다.
- 최종 반영 엔진은 최신 `origin/main` 대비 `behind 0, ahead 1` 단일 최종 커밋만 허용한다.
- 강제 push, `reset --hard`, `git clean -fd`, 자동 stash, 강제 checkout과 자동 충돌 해결을 사용하지 않는다.

## 변경 영역별 검증 라우팅

개발 중에는 변경 범위에 맞는 관련 테스트부터 실행하고, 수정이 안정된 뒤 필요한 최종 검증으로 넓힌다. 작은 수정마다 처음부터 전체 하네스와 전체 pytest를 반복하지 않는다.

- 군집: `.\run_agent_harness.bat clustering`
- 예약 작업·쿼터: `.\run_agent_harness.bat scheduler`
- 최신 데이터 수집·부분 성공·수집 후 정리 순서: `.\run_agent_harness.bat latest-data`
- 보존·정리 정책: `.\run_agent_harness.bat cleanup`
- Gemini 주제 방향: `.\run_agent_harness.bat topic-angles`
- 읽기 전용 운영 진단·진단 CLI: `.\run_agent_harness.bat diagnostics`
- 앱 supervisor·웹 업데이트·운영 로그: `.\run_agent_harness.bat operations`
- AI 요청서→AI 결과→편집→발행 보조의 상태·이동 계약: `.\run_agent_harness.bat workflow`
- 하네스·적용 도구 자체: `.\run_agent_harness.bat harness`
- Public 공개 준비: `python -m pytest -q -p no:cacheprovider tests/test_public_repository_readiness.py`; 실제 전체 refs Secret 검사는 로컬에서 `python scripts/check_public_readiness.py`로 별도 실행한다.
- 여러 영역을 동시에 바꾼 경우 필요한 시나리오를 함께 지정한다. `all`은 광범위한 운영 계약 변경이나 하네스 전체 점검 때 사용한다.
- 개별 어댑터, 백업·발행처럼 전용 시나리오가 없는 영역은 해당 `tests/test_*.py`를 직접 선택한다.
- 문서만 바꾸고 코드 계약이 변하지 않았다면 `python scripts/check_text_hygiene.py`와 관련 문서 계약 테스트를 우선한다. 제품 코드·하네스·운영 계약 변경은 최종적으로 아래 전체 검증까지 수행한다.

## 하네스 확장 경계

- 지원 시나리오·별칭과 테스트 묶음의 단일 기준은 `scripts/agent_test_harness.py`다. BAT와 PowerShell 진입점은 실행 환경과 인자 전달만 담당하며 같은 목록을 중복 관리하지 않는다.
- 새 테스트가 기존 시나리오의 책임에 들어가면 새 시나리오를 만들지 말고 기존 묶음을 확장한다.
- 여러 테스트가 반복해서 함께 변경되고 별도 확인 경로가 필요한 독립 영역이 생길 때만 새 시나리오를 추가한다. 단일 파일·일회성 검증은 직접 pytest로 유지한다.
- 시나리오를 추가하면 하네스 계약 테스트와 `docs/AGENT_TEST_HARNESS.md`의 라우팅을 함께 갱신한다. 자동 파일 분류기나 Hook은 명확한 반복 비용이 확인되기 전에는 추가하지 않는다.

## Agent 하네스와 최종 검증

- 하네스는 시스템 임시 디렉터리, 테스트 DB, 가짜 어댑터·Gemini 응답만 사용하며 실제 DB·API·스케줄러를 사용하지 않는다.
- 하네스 통과는 실제 수집 품질, 실제 Gemini 품질, 브라우저 렌더링, 작업 스케줄러 등록 성공을 의미하지 않는다.
- 빠른 구문 검사: `python -m compileall -q app.py src tests scripts`
- 텍스트 검사: `python scripts/check_text_hygiene.py`
- 전체 회귀: `python -m pytest -q -p no:cacheprovider`
- GitHub CI와 `apply_update`의 전체 검증은 최종 게이트로 유지한다. 이미 유효하게 통과했고 이후 영향을 받지 않은 검증을 이유 없이 반복하지 않는다.
- 최종 diff에서 관련 문서와 보호 파일 포함 여부를 확인한다.
