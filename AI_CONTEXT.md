# 콘텐츠 트렌드 트래커 AI 작업 기준

## 1. 현재 운영 기준

- 프로젝트: `content-trend-tracker`
- 로컬 경로: `C:\AIProjects\content-trend-tracker`
- GitHub: `https://github.com/honggee486-dot/content-trend-tracker`
- 기본 원격·브랜치: `origin` / `main`
- 현재 문서 기준 버전: `0.10.110`
- 기본 DB: `data\content_trend_tracker.duckdb`
- 수집 잠금: `data\trend_refresh.lock`
- 군집 잠금: `data\trend_clustering.lock`
- 자동 수집 기본 입력값: `180분`
- Gemini 자동·예약 글감 분석: 요청당 기본값 `15개`·허용 상한 `30개`
- Gemini 자동·예약 기본 동시 요청: `1개`
- Gemini 글감 기회 기준: `50점 이상`
- Gemini 글감 분석 사고 수준 기본값: `medium`
- Gemini 글감 분석 요청 제한 시간: `600초`
- 관리형 Streamlit 주소: `http://127.0.0.1:8518`

실제 `.env` 처리량은 1~30 범위로 제한한다. 과거 기본값 `360초`는 `600초`로 자동 보정하며 사용자가 별도 값을 지정한 경우에는 그 값을 보존한다. 저장되지 않은 후보와 검증 탈락 후보는 삭제하지 않고 다음 실행 대상으로 남긴다.

`0.10.110`은 실제 블로그 제작 흐름을 중심으로 글감 추천·프로필 표시와 AI 요청서를 정리하고, Gemini 요청량을 현재 제한 상태에 맞춰 보수적으로 처리하면서 유효한 부분 응답을 보존한다. AI 요청서는 최신 웹 검색과 초안 후 세 차례 재검증, 자연스러운 인간 편집 품질 점검, SEO·이미지 위치·생성 프롬프트를 요구하며 `schema_version 2.1`의 SEO·무료 이미지 메타데이터, 내부 `contentReference` 제거, 공식 공개 페이지 캡처 작업 지시를 지원한다. 테스트·하네스·work 업데이트 검증 경로도 비용을 줄이면서 릴리스 검증 강도를 유지하도록 보강한다. 기존 DuckDB 데이터·설정·원본·자료팩·AI 기록·초안·리비전·사실 확인·발행 기록과 부분 성공·수동 최종 발행 원칙은 유지한다.

`0.10.109`에서는 `0.10.108`의 수집 후 정리 안전 경계를 유지하면서 대시보드 진행 문구를 실제 실행 순서와 맞추고, deterministic baseline 읽기 전용 비교의 불완전 지표 노출을 차단하며 사람이 현재 군집·baseline 대표 제목을 직접 비교할 수 있게 했다. 운영·P2 진단은 동일 군집 품질 runtime 결과를 재사용하고 개발 하네스는 시나리오 매핑을 단일 기준으로 통합했다. DB 스키마·외부 API·인증·의존성 계약과 기존 데이터·부분 성공·수동 최종 발행 원칙은 유지한다.

## 2. 프로젝트 목적과 데이터 흐름

YouTube, NAVER, Daum, Google Trends, Wikimedia 신호를 수집·정규화·군집화해 정보성 글감 후보를 만들고, 근거 검토부터 AI 요청서·초안·사실 확인·수동 발행 보조까지 지원하는 로컬 Streamlit 프로그램이다.

```text
외부 출처
  ↓
source_items
  ↓
trend_clusters / trend_cluster_items
  ↓
topics / topic_source_links
  ↓
content_packs
  ↓
generation_sessions
  ↓
drafts / draft_revisions
  ↓
fact_check_items / fact_check_history
  ↓
publish_records / publish_record_history
```

자동 로그인, 쿠키 저장, CAPTCHA 우회, 게시 버튼 자동 클릭과 무인 자동 게시를 수행하지 않는다.

## 3. 기준 소스와 Git 흐름

1. 최신 GitHub `origin/main`과 실제 저장소 파일을 먼저 확인한다.
2. 사용자가 GitHub보다 최신이라고 명시한 로컬 변경·ZIP·파일은 지정 범위에서 우선한다.
3. 이전 대화와 과거 커밋·테스트 결과는 참고만 하고 현재 사실로 단정하지 않는다.

일반 수정은 `work/<다음 버전>` 누적 브랜치에서 진행한다. 릴리스는 최신 `origin/main`에서 깨끗한 최종 브랜치를 만들고 누적 변경을 하나의 릴리스 커밋으로 정리한다. 최종 브랜치는 `behind 0, ahead 1`이어야 한다. 강제 push, `reset --hard`, `git clean -fd`, 자동 stash와 충돌 자동 해결을 사용하지 않는다.

### 적용 도구 단일 커밋 안전 강화

- 최종 반영 브랜치는 현재 `origin/main` 대비 `behind 0, ahead 1`인 경우에만 적용한다.
- `apply_update.bat`는 `pwsh.exe`를 우선 사용하고, 없으면 Windows PowerShell 5.1을 폴백으로 사용하며, 둘 다 없으면 변경 없이 실패한다.
- BAT는 `chcp 65001`과 상시 `pause`에 의존하지 않고 PowerShell 종료 코드를 그대로 반환한다.
- 격리 검증 뒤 원격 기준과 단일 커밋 관계를 다시 확인하고 비강제 fast-forward만 허용한다.
- 실패 시 로컬·원격 `main`과 사용자 파일을 변경하지 않는다.

## 4. 핵심 경로

- `app.py`: Streamlit 진입점
- `run_app.bat`, `stop_app.bat`: 관리형 앱 실행·정확한 종료
- `scripts/app_supervisor.ps1`: 전용 포트·단일 인스턴스·웹 업데이트 재시작 관리자
- `scripts/stop_registered_app.ps1`: 등록 PID와 시작 시각을 검증한 종료
- `scripts/refresh_trends.py`, `scripts/refresh_trends_safe.py`: 수동·예약 수집
- `scripts/refresh_trends_dashboard.py`: 화면 수집 백그라운드 실행
- `scripts/process_cluster_backlog.py`: 2단계 군집 작업
- `scripts/report_p2_diagnostics.py`, `scripts/report_operation_diagnostics.py`: 읽기 전용 운영·P2 진단
- `scripts/agent_test_harness.py`, `scripts/check_harness.ps1`: 변경 영역별 안전 하네스 라우팅
- `src/database.py`: 연결·스키마·추가형 마이그레이션
- `src/config.py`: 경로·환경변수·Gemini 설정
- `src/services/topic_angle_ai_service.py`: 주제 방향 준비·API 실행·검증·저장
- `src/services/topic_angle_candidate_diagnostic_service.py`: 주제 방향 대상·제외 사유 집계
- `src/services/trend_refresh_lock_service.py`, `src/services/trend_clustering_lock_service.py`: 잠금·소유자 판정
- `src/services/lock_lease_service.py`, `src/services/process_identity_service.py`: heartbeat/lease와 PID 재사용 방지
- `src/services/program_log_service.py`: 프로그램 로그 저장·조회
- `src/services/trend_clustering_quality_sample_service.py`: 저장된 군집 품질 표본 읽기 전용 재구성
- `src/services/trend_clustering_quality_runtime.py`: 품질 표본·deterministic baseline 단일 실행·재사용 runtime
- `src/services/trend_cluster_existing_index.py`: 기존 군집 후보 인덱스와 일반 편집 템플릿 식별 방어
- `src/services/portal_full_window_analysis_runtime.py`: NAVER·Daum 최근 시간 범위 전체 분석 계약
- `src/services/blog_channel_strategy_service.py`: 블로그 발행 채널 추천 전략과 글감 배정
- `src/services/publish_preparation_service.py`: 발행처별 SEO·이미지 슬롯·복사 패키지
- `src/services/blogger_draft_service.py`: Blogger 공식 API 비공개 초안 전송
- `src/services/publish_performance_service.py`: 발행 성과 스냅샷과 동일 구간 비교
- `apply_update.bat`, `scripts/apply_update_release.ps1`, `scripts/apply_update.ps1`: 작업 브랜치·릴리스 적용
- `tests/`: 회귀 테스트
- `docs/APP_RUNTIME.md`, `docs/TREND_CLUSTERING_RUNTIME.md`, `docs/NEXT_WORK.md`: 운영 계약

## 5. 0.10.107 운영·진단 계약

0.10.110에서도 0.10.109 이하의 핵심 호환 계약을 유지한다. 유효한 글감은 그대로 저장하고 누락·검증 탈락 항목은 다음 실행 대상으로 유지하며, HTTP 200 부분 응답의 누락·검증 탈락 ID만 최대 한 번 보강한다. 2단계 군집의 희소 응답 상호 배타·대표 후보 계약도 그대로 보존한다. 결정론적 군집 baseline 읽기 전용 비교 진단과 로컬 TPM 대기와 공급자 제한 분리 진단도 생산 규칙을 자동 변경하지 않는 읽기 전용 진단 계약으로 유지한다.

### 포털 전체 범위와 읽기 전용 진단

- NAVER·Daum 분석은 사용자 개수 상한 대신 `trend_lookback_hours` 최근 시간 범위 전체를 사용한다.
- 기존 포털 분석 상한 설정값은 삭제하지 않고 하위 호환 입력으로 보존하며 그룹당 `999,999` 내부 SQL 방어 경계만 유지한다.
- 운영 진단은 실제 DB를 변경하지 않고 현재 수집·Gemini·군집 상태와 출처 분석 범위를 읽어 보고한다.
- 진단 번들은 동일 스냅샷을 재사용하며 실제 DB/WAL 변경 여부를 검증할 수 있게 한다.

### 2단계 군집 진단과 품질 표본

- `50,000`은 Gemini 요청당 후보 수가 아니라 한 작업의 내부 스냅샷 스캔 안전 상한이다.
- 한 외부 작업에서 제목·사건·식별·기존 군집 관점을 토큰·TPM 기준으로 순차 분할하고 과거 고정 후보 수 작업 이력은 기존 의미로 읽는다.
- `trend_clustering.quality_sample`은 작업 원장과 현재 군집 스냅샷이 일치할 때만 `reconstruction_reliable=true`로 표시한다.
- 후속 군집으로 스냅샷이 달라진 표본은 당시 작업 결과라고 추정하지 않는다.
- deterministic baseline 비교는 재구성·baseline 비교가 모두 완전할 때만 일치·불일치 집계를 판단 근거로 노출하며, 불완전하면 원인과 대표 제목 표본만 보수적으로 표시한다.
- 운영·P2 사람용 진단은 현재 군집과 baseline 대표 제목을 직접 비교할 수 있게 하고, 동일 `trend_clustering_quality_runtime` 결과를 재사용해 품질 표본과 baseline을 중복 계산하지 않는다.
- 기존 군집 후보 인덱스는 `특징`, `선택`, `포인트`, `총정리` 및 조사 결합형 표현만 공유하는 후보를 식별 근거로 사용하지 않으며 실제 상품·사건 식별 토큰 공유를 요구한다.

### 관리형 Streamlit 실행·종료·웹 업데이트

- 앱은 `run_app.bat`으로 실행하고 전용 루프백 포트 `8518`을 사용한다.
- 전경 supervisor가 Streamlit 자식 프로세스를 소유하므로 웹 업데이트 뒤에도 원래 Antigravity 터미널의 제어권을 유지한다.
- 동일 프로젝트 supervisor는 이름 있는 mutex로 하나만 허용한다.
- 포트가 점유되면 임의 포트로 이동하지 않고 점유 PID를 표시해 실행을 중단한다.
- 런타임 상태는 `%LOCALAPPDATA%\content-trend-tracker`에 저장하며 Git 작업 트리와 실제 DB에는 기록하지 않는다.
- `stop_app.bat`은 프로젝트 경로·PID·프로세스 시작 시각이 일치하는 등록 프로세스 트리만 종료한다. 모든 Python·Streamlit 프로세스를 일괄 종료하지 않는다.
- 웹 업데이트 요청 프로세스는 현재 Streamlit만 종료하고 실제 `apply_update.bat` 실행과 재시작은 기존 supervisor가 담당한다.

### 수집·군집 진행 상태와 프로그램 로그

- 최신 데이터 수집은 별도 프로세스에서 실행한다.
- 공유 진행 상태 파일로 진행률·현재 단계·최신순 단계 이력을 표시한다.
- 실행 중에는 수집·재계산·주제 방향 버튼을 비활성화하고 완료·실패 후 다시 활성화한다.
- 출처 수집, 1차 군집, 2차 Gemini 군집, 통합 순위 계산·저장, 주제 방향 생성은 프로그램 로그에서 서로 다른 단계로 기록한다.
- 대시보드의 자동 정리 초반 표시는 조건 확인·예약 단계이며 실제 정리 완료를 의미하지 않는다. 정리가 필요하면 출처 저장 뒤 순위 준비 직전에 실행된 결과를 후속 단계에서 표시한다.
- 출처가 이미 정상 완료된 뒤 후속 군집·순위 단계가 실패해도 해당 출처를 실패로 다시 표시하지 않는다.
- 사용 가능한 모든 출처가 실패하면 기존 통합 순위와 보존 자료를 유지하고 자동 정리·주제 방향 생성을 건너뛴다. 일부 출처가 성공·부분 성공·변경 없음이면 해당 결과를 보존한 채 후속 순위 흐름을 계속한다.
- 기존 군집 작업이 활성 상태라 `skipped_overlap`이면 출처 저장은 유지하되 순위 재계산 보류를 완료와 구분해 표시한다.
- 같은 계산 안의 중복 `cluster_id`와 중복 원문 연결은 저장 전 정리해 기본키 충돌이 전체 결과를 취소하지 않게 한다.

### 수집·군집 잠금

- 수집·군집 잠금은 PID, 프로세스 시작 식별자, 소유권 token, heartbeat와 lease를 함께 사용한다.
- Windows PID가 재사용돼도 시작 시각이 다르면 기존 작업 소유자로 보지 않는다.
- 기존 형식 잠금은 PID 생존 여부만 확인하는 호환 경로를 유지한다.
- 시작 식별자를 읽지 못한 경우 실행 중인 작업을 잘못 삭제하지 않도록 보수적으로 활성 처리한다.
- heartbeat가 갱신되지 않고 lease가 만료된 잠금은 다음 실행에서 복구할 수 있다.
- 잠금 해제는 token이 일치하는 소유자만 수행한다.

### 1차·2차 군집과 부분 성공

- 1차는 같은 정규 URL과 안전한 동일 제목을 우선하되 날짜·회차·제품·행동·상승·하락 충돌을 분리한다.
- 2차 Gemini는 기존 군집 연결·2개 이상 신규 그룹·불확실·충돌만 희소 응답으로 반환한다.
- 후보별 로컬 `option_id`를 사용하고 허용 범위 밖 선택은 자동 저장하지 않는다.
- Gemini 요청과 점수 계산 동안 DuckDB 연결을 장시간 유지하지 않는다.
- 일부 군집이 정상 저장되면 전체 미처리 원문이 남아 있어도 저장된 군집의 주제 방향 생성을 이어간다.
- 남은 미처리는 다음 실행 대상으로 보존하며 Gemini 실패가 성공한 출처 수집과 군집 저장을 취소하지 않는다.

### 주제 방향 대상 선정 상세

- 주제 방향은 추천 상태가 `recommended` 또는 `review`이고 기회 점수가 기준 이상인 군집에서 생성한다.
- 방향 3개와 AI 작성 설정이 이미 완성된 군집은 생성 대상에서 제외한다.
- 제목이 민감정보 검사에 걸리거나 연결 근거가 없는 후보는 API 요청에서 제외한다.
- 한 번의 기본 실행은 최대 15개를 준비하며 한 글감마다 방향 3개를 저장한다.
- 프로그램 로그의 `주제 방향 대상 선정 상세`에는 전체 군집, 추천·검토, 기회 점수 통과, 기존 방향 완료, 생성 필요, 이번 확인, 민감 제목, 근거 없음, 이번 생성 대상과 요청 범위 밖 미검사 수를 남긴다.
- 추가 진단 집계 실패는 주제 방향 생성과 출처 수집 성공을 취소하지 않는다.

### Gemini 글감 분석

- 자료팩 화면의 직접 Gemini 초안 생성 경로는 사용하지 않으며 외부 AI 요청서·응답 검사·초안 편집 흐름을 유지한다.
- 표시 제목·요약·작성 설정·확인 항목과 방향 3개를 저장한다.
- 방향별 검색 의도·독자 질문·수요 근거·근거 ID와 점수 하위 항목을 검증하고 Python 합계로 안정 정렬한다.
- 요청·응답 글자·토큰·오류·재시도·소요 시간, 실제 요청 글감 수·설정 상한·사고 수준·제한 시간, `finishReason`과 선택적 메시지를 기록한다.
- 기존 호출에는 새 실행 조건과 종료 사유가 없을 수 있음 상태로 호환 표시한다.
- `MAX_TOKENS` 종료를 일반 응답 검증 실패와 별도로 진단한다.
- 유효 결과에서 요청 ID 일부가 빠지면 `response_partial`로 기록한다.
- HTTP 200 부분 응답은 기존 유효 결과를 유지하고 원본 배치의 누락·검증 탈락 ID만 최대 한 번 보강한다.
- 유효한 글감은 그대로 저장하고 보강 뒤에도 남은 누락·검증 탈락 항목은 다음 실행 대상으로 유지한다.
- 출처 수집 성공 판정과 Gemini 후처리 결과는 분리 표시한다.
- 600초 변경 효과는 적용 후 완료 실행 최소 3회·요청 글감 60개 이상 표본으로 판단한다.

### Gemini 안정성 추천

- 실제 런타임 처리량·사고 수준·성공률·검증 실패·재시도와 종료 사유를 읽기 전용으로 계산한다.
- 현재 처리량보다 큰 값을 추천하지 않는다.
- 표본이 부족하면 현재 설정을 유지한다.
- 모델·처리량·사고 수준을 자동 변경하지 않음 원칙을 유지한다.
- 실제 DB·로그 표본 없이 요청량·프롬프트·파서·사고 수준을 동시에 바꾸지 않는다.

### 설정 진단 DuckDB 연결 재사용 계약

- 설정 화면은 이미 열린 앱 DuckDB 연결을 진단 서비스에 전달한다.
- 같은 DB 파일을 서로 다른 구성으로 다시 열지 않는다.
- 전달받은 연결에서는 SELECT만 실행하고 연결의 소유권과 종료는 호출한 앱에 남긴다.
- 독립 실행에서는 `read_only=True` 폴백을 유지한다.
- 진단 하나의 실패가 다른 앱 기능을 중단시키지 않는다.

## 6. 읽기 전용 진단과 제작 흐름 보존

### 수집 출처 다양성·군집 사례

- `source_items`, `trend_clusters`, `trend_cluster_items`를 읽기 전용으로 조회한다.
- 출처 그룹별 분석 입력 상한 초과 추정과 상한 외 미연결을 구분한다.
- 진단 수치를 근거로 실제 DB·수집 이력 확인 없이 군집 유사도나 입력 상한을 자동 완화하지 않는다.
- 실제 군집 기준 변경은 진단 결과와 로컬 DB·수집 이력을 확인한 별도 작업으로 진행한다.
- 기존 최근 후보 외에 사례 핵심어가 포함된 같은 기간 군집을 추가 조회할 수 있다.
- 군집 사례 상세 진단도 동일한 세 테이블을 읽기만 하며 군집을 병합하거나 점수를 수정하지 않는다.
- 표시 원인은 확정 판정이 아니라 로컬 검증 표본을 좁히기 위한 추정이다.
- Agent 분석 전 군집 유사도나 정규화 규칙을 자동 변경하지 않는다.

### 제작 화면 이동 상태 안전화

- AI 요청서 화면은 `prefill_topic_id`와 `prefill_angle`만 유지한다.
- AI 결과 화면은 `prefill_content_pack_id`만 유지한다.
- 편집·발행 화면은 `prefill_draft_id`만 유지한다.
- `ai_import_raw_<content_pack_id>`의 사용자 원문은 삭제하지 않는다.
- 자료팩 재사용 payload는 같은 주제에서만 유지한다.
- 화면 이동 시 충돌하는 Streamlit 포인터와 파생 검사값만 제거하며 DB 기록은 수정하지 않는다.

### 작업 대기열·자료팩·AI 기록

- AI 요청서 준비 작업 전체 묶음과 각 항목의 수집 근거는 기본값이 접힘 상태다.
- 입력값 재사용만으로 새 `content_packs` 행을 만들지 않는다.
- 생성 세션과 초안은 생성 당시 `content_pack_id` 연결을 유지한다.
- 저장된 AI 원문은 현재 `parse_ai_result()`와 `validate_ai_result_against_references()`로 재검사할 수 있다.
- 저장 원문 다시 열기는 Streamlit 입력값만 준비하며 기존 기록을 수정하지 않는다.

### 초안·사실 확인·발행 이력

- 초안 복원 전 현재 편집본이 다르면 안전 리비전을 먼저 만든다.
- 복원 후 주제 상태는 `editing`으로 바꾸고 사실 확인 재검토를 안내한다.
- 과거 리비전은 덮어쓰거나 물리 삭제하지 않고 복원도 새 리비전으로 기록한다.
- 사실 확인 변경은 `fact_check_history`에 누적하고 되돌리기도 새 이력으로 남긴다.
- 이미 발행된 주제는 `published`를 유지한다.
- 발행 기록 정정·보관·복원 전후 값과 사유는 `publish_record_history`에 보존한다.
- 기존 `publish_records` 행을 물리 삭제하지 않는다.

## 7. 블로그 발행 채널·SEO·브라우저 보조·성과 비교 보존

0.10.89에서 구현한 발행 워크플로는 0.10.110 제작·요청서 안정화와 별개로 계속 유지한다.

- `src/services/blog_channel_strategy_service.py`는 Blogger 3개·네이버 1개의 관리형 추천 전략과 글감별 추천 사유를 제공한다.
- `src/services/curated_blog_profile_service.py`는 Blogger 3개·네이버 1개·티스토리 1개의 고정 활성 프로필 5개와 이전 프로필 비활성 보관을 유지한다.
- `src/services/publish_preparation_service.py`는 발행처별 SEO, 이미지 3개 슬롯, 제목·본문·태그와 이미지 프롬프트별 복사 패키지를 준비한다.
- `chrome_extension/`과 `src/services/chrome_extension_handoff_service.py`는 사용자가 직접 로그인한 현재 Chrome 탭에서만 제목·본문·태그 입력을 보조한다.
- `src/services/blogger_draft_service.py`는 최소 권한 OAuth를 사용해 Blogger 공식 API의 비공개 초안만 생성하며 자동 공개 발행하지 않는다.
- `src/services/publish_performance_service.py`는 발행 후 7일·30일·90일 수동 성과 스냅샷과 동일 구간 비교를 지원한다.
- 추천 규칙은 표본이 충분하기 전 자동 변경하지 않고, 최종 임시저장·발행은 사용자가 직접 수행한다.
- OAuth 클라이언트·토큰, 로그인 쿠키와 브라우저 프로필을 저장소·로그·AI 요청에 포함하지 않는다.

## 8. 데이터 안전과 호환성

- 기존 DuckDB 데이터, 설정, 자료팩, 초안, 리비전, 사실 확인과 발행 기록을 보존한다.
- DB 변경은 추가형 호환 마이그레이션을 우선하고 기존 테이블·컬럼·상태 의미를 삭제하거나 바꾸지 않는다.
- `topic_source_links` 원본 보존, 자료팩·AI 응답·발행 기록 멱등성을 유지한다.
- 같은 URL·외부 ID 재수집은 새 행을 무한 추가하지 않고 포착 시각·횟수를 갱신한다.
- 원문 게시 시각을 재수집 시각으로 덮어쓰지 않는다.
- 한 출처 실패가 다른 출처 결과를 취소하지 않는다.
- 시점 의존 글감은 사실 참고 자료가 없으면 AI 요청서 생성을 차단한다.
- 활성 수집 잠금, DB 점유 또는 비어 있지 않은 WAL이 있으면 백업·복구를 변경 없이 중단한다.
- 복구 전 현재 DB를 `pre_restore`로 보존하고 복구 실패 시 원상 복구를 시도한다.

## 9. 보호 대상

명시적 요청과 안전 검토 없이는 다음을 수정·복사·압축·커밋하지 않는다.

- `.env`, `.env.*` (`.env.example` 제외), `.streamlit/secrets.toml`
- API 키·비밀번호·토큰·OAuth 파일·쿠키·브라우저 프로필
- `data/*.duckdb`, `data/*.duckdb.wal`, 수집·군집 잠금
- `*.db`, `*.sqlite*`, `*.parquet`, `*.feather`, `*.arrow`
- 로그·리포트·exports·backups
- `.git`, 가상환경, 캐시, 테스트·빌드 산출물

## 10. 실행과 검증

```powershell
.\run_app.bat
.\stop_app.bat
.\run_trend_refresh.bat
.\run_tests.bat
```

직접 검사:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py src tests scripts
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

앱 실행, 실제 DB 초기화, 수집과 Gemini 호출은 운영 데이터·외부 API에 영향을 줄 수 있으므로 자동 검증에 임의 포함하지 않는다. 실행하지 않은 검사를 통과했다고 말하지 않는다.

## 11. Agent와 외부 프로젝트

실제 DuckDB·로그·Windows 프로세스·작업 스케줄러·브라우저 검증처럼 로컬 실행 이점이 큰 경우에만 Agent를 검토한다. Agent는 실제 DB를 수정하거나 기본 브랜치를 변경·커밋·push하지 않는다. `.env`, API 키, 실제 DB, 쿠키와 브라우저 프로필을 전달하지 않는다.

`youtube-trend-tracker` 등 외부 저장소는 연동 계약 확인용 읽기 전용이며 사용자가 명시하지 않으면 수정하지 않는다. 폐기된 `API Auto Dev`를 구현 근거로 사용하지 않는다.
